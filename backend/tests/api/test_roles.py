from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.audit.models import AuditEvent
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.tenancy.models import Role, UserRoleBinding


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


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
