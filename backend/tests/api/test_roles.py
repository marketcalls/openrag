import asyncio
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.audit.models import AuditEvent
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.tenancy.models import (
    Organization,
    Role,
    RolePermission,
    UserRoleBinding,
)
from openrag.modules.tenancy.permissions import ALL_PERMISSIONS


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_permission_catalog_is_explicit_stable_and_role_manager_only(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    headers = await auth(client, seeded_user.email)
    response = await client.get("/api/v1/roles/catalog", headers=headers)

    assert response.status_code == 200
    catalog = response.json()
    assert catalog == sorted(catalog, key=lambda item: item["code"])
    assert set(catalog[0]) == {"code", "label", "group", "description"}
    catalog_codes = {item["code"] for item in catalog}
    assert catalog_codes == ALL_PERMISSIONS
    assert catalog_codes == {
        "audit.read",
        "chat.use",
        "document.approve",
        "document.read",
        "document.upload",
        "model.configure",
        "rag.evaluate",
        "role.manage",
        "user.manage",
        "workspace.manage",
        "workspace.read_all",
    }
    assert all(item["label"] and item["group"] and item["description"] for item in catalog)
    assert "platform.superadmin" not in {item["code"] for item in catalog}

    ordinary = User(
        org_id=seeded_user.org_id,
        email="ordinary-catalog@acme.com",
        password_hash=hash_password("pw123456"),
    )
    user_role = (
        await session.execute(
            select(Role).where(
                Role.org_id == seeded_user.org_id,
                Role.key == "user",
            )
        )
    ).scalar_one()
    session.add(ordinary)
    await session.flush()
    session.add(
        UserRoleBinding(
            org_id=seeded_user.org_id,
            user_id=ordinary.id,
            role_id=user_role.id,
            created_by=seeded_user.id,
        )
    )
    await session.commit()

    denied = await client.get(
        "/api/v1/roles/catalog",
        headers=await auth(client, ordinary.email),
    )
    assert denied.status_code == 403


async def test_role_crud_is_scoped_strict_and_audited(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    headers = await auth(client, seeded_user.email)
    rejected = await client.post(
        "/api/v1/roles",
        json={
            "name": "Unsafe",
            "description": "",
            "permissions": ["platform.superadmin"],
        },
        headers=headers,
    )
    assert rejected.status_code == 422

    created = await client.post(
        "/api/v1/roles",
        json={
            "name": "Safety Reviewer",
            "description": "Reviews evidence",
            "permissions": ["document.read", "document.approve"],
        },
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert set(body) == {
        "id",
        "key",
        "name",
        "description",
        "permissions",
        "is_system",
        "is_assignable",
    }
    assert body["key"].startswith("custom_")
    assert body["is_system"] is False

    updated = await client.patch(
        f"/api/v1/roles/{body['id']}",
        json={"name": "Senior Safety Reviewer", "permissions": ["document.read"]},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Senior Safety Reviewer"

    deleted = await client.delete(
        f"/api/v1/roles/{body['id']}", headers=headers
    )
    assert deleted.status_code == 204
    actions = set(
        (
            await session.execute(
                select(AuditEvent.action).where(
                    AuditEvent.target_id == body["id"]
                )
            )
        ).scalars()
    )
    assert actions == {"role.created", "role.updated", "role.deleted"}


async def test_system_and_cross_org_roles_are_protected(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    headers = await auth(client, seeded_user.email)
    administrator = (
        await session.execute(
            select(Role).where(
                Role.org_id == seeded_user.org_id,
                Role.key == "administrator",
            )
        )
    ).scalar_one()
    assert (
        await client.patch(
            f"/api/v1/roles/{administrator.id}",
            json={"name": "Owner"},
            headers=headers,
        )
    ).status_code == 409
    assert (
        await client.delete(
            f"/api/v1/roles/{administrator.id}", headers=headers
        )
    ).status_code == 409
    assert (
        await client.patch(
            f"/api/v1/roles/{uuid4()}",
            json={"description": "probe"},
            headers=headers,
        )
    ).status_code == 404


async def test_bindings_reject_escalation_cross_org_and_last_admin_removal(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    target = User(
        org_id=seeded_user.org_id,
        email="target@acme.com",
        password_hash=hash_password("pw123456"),
    )
    session.add(target)
    await session.commit()
    user_role = (
        await session.execute(
            select(Role).where(
                Role.org_id == seeded_user.org_id,
                Role.key == "user",
            )
        )
    ).scalar_one()
    # A random role id is indistinguishable from a foreign role at the API boundary.
    headers = await auth(client, seeded_user.email)
    cross_org = await client.put(
        f"/api/v1/users/{target.id}/role-bindings",
        json={"role_ids": [str(uuid4())]},
        headers=headers,
    )
    assert cross_org.status_code == 404

    bound = await client.put(
        f"/api/v1/users/{target.id}/role-bindings",
        json={"role_ids": [str(user_role.id)]},
        headers=headers,
    )
    assert bound.status_code == 200
    assert [role["key"] for role in bound.json()["roles"]] == ["user"]
    binding = (
        await session.execute(
            select(UserRoleBinding).where(UserRoleBinding.user_id == target.id)
        )
    ).scalar_one()
    assert binding.created_by == seeded_user.id

    last_admin = await client.put(
        f"/api/v1/users/{seeded_user.id}/role-bindings",
        json={"role_ids": [str(user_role.id)]},
        headers=headers,
    )
    assert last_admin.status_code == 409

    arbitrary_legacy = await client.patch(
        f"/api/v1/users/{target.id}",
        json={"role": "superadmin"},
        headers=headers,
    )
    assert arbitrary_legacy.status_code == 422


async def test_bound_role_cannot_be_deleted_and_real_invalid_roles_cannot_bind(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    target = User(
        org_id=seeded_user.org_id,
        email="binding-target@acme.com",
        password_hash=hash_password("pw123456"),
    )
    session.add(target)
    foreign_org = Organization(name="Foreign roles org")
    session.add(foreign_org)
    await session.flush()
    foreign_role = Role(
        org_id=foreign_org.id,
        key="foreign",
        name="Foreign",
    )
    unassignable = Role(
        org_id=seeded_user.org_id,
        key="service_account",
        name="Service Account",
        is_assignable=False,
    )
    session.add_all([foreign_role, unassignable])
    await session.commit()
    headers = await auth(client, seeded_user.email)

    for invalid_role in (foreign_role, unassignable):
        response = await client.put(
            f"/api/v1/users/{target.id}/role-bindings",
            json={"role_ids": [str(invalid_role.id)]},
            headers=headers,
        )
        assert response.status_code == 404

    created = await client.post(
        "/api/v1/roles",
        json={"name": "Bound Role", "permissions": ["chat.use"]},
        headers=headers,
    )
    assert created.status_code == 201
    role_id = created.json()["id"]
    assert (
        await client.put(
            f"/api/v1/users/{target.id}/role-bindings",
            json={"role_ids": [role_id]},
            headers=headers,
        )
    ).status_code == 200
    assert (
        await client.delete(f"/api/v1/roles/{role_id}", headers=headers)
    ).status_code == 409


async def test_concurrent_replacements_preserve_one_active_administrator(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    administrator = (
        await session.execute(
            select(Role).where(
                Role.org_id == seeded_user.org_id,
                Role.key == "administrator",
            )
        )
    ).scalar_one()
    user_role = (
        await session.execute(
            select(Role).where(
                Role.org_id == seeded_user.org_id,
                Role.key == "user",
            )
        )
    ).scalar_one()
    second = User(
        org_id=seeded_user.org_id,
        email="second-admin@acme.com",
        password_hash=hash_password("pw123456"),
    )
    manager = User(
        org_id=seeded_user.org_id,
        email="role-manager@acme.com",
        password_hash=hash_password("pw123456"),
    )
    manager_role = Role(
        org_id=seeded_user.org_id,
        key="concurrent_role_manager",
        name="Concurrent Role Manager",
    )
    session.add_all([second, manager, manager_role])
    await session.flush()
    session.add_all(
        [
            RolePermission(
                role_id=manager_role.id,
                permission="role.manage",
            ),
        UserRoleBinding(
            org_id=seeded_user.org_id,
            user_id=second.id,
            role_id=administrator.id,
            created_by=seeded_user.id,
            ),
            UserRoleBinding(
                org_id=seeded_user.org_id,
                user_id=manager.id,
                role_id=manager_role.id,
                created_by=seeded_user.id,
            ),
        ]
    )
    await session.commit()
    headers = await auth(client, manager.email)

    responses = await asyncio.gather(
        client.put(
            f"/api/v1/users/{seeded_user.id}/role-bindings",
            json={"role_ids": [str(user_role.id)]},
            headers=headers,
        ),
        client.put(
            f"/api/v1/users/{second.id}/role-bindings",
            json={"role_ids": [str(user_role.id)]},
            headers=headers,
        ),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    remaining = (
        await session.execute(
            select(func.count())
            .select_from(UserRoleBinding)
            .join(User, User.id == UserRoleBinding.user_id)
            .where(
                UserRoleBinding.org_id == seeded_user.org_id,
                UserRoleBinding.role_id == administrator.id,
                UserRoleBinding.workspace_id.is_(None),
                User.active.is_(True),
            )
        )
    ).scalar_one()
    assert remaining == 1
