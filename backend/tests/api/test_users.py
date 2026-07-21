import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.tenancy.models import Role


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_list_and_deactivate(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    plain_user = User(
        org_id=seeded_user.org_id,
        email="p@acme.com",
        password_hash=seeded_user.password_hash,
    )
    session.add(plain_user)
    await session.commit()

    headers = await auth(client, "a@acme.com")
    response = await client.get("/api/v1/users", headers=headers)
    emails = [user["email"] for user in response.json()]
    assert set(emails) == {"a@acme.com", "p@acme.com"}

    patch_response = await client.patch(
        f"/api/v1/users/{plain_user.id}",
        json={"active": False},
        headers=headers,
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["active"] is False

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "p@acme.com", "password": "pw123456"},
    )
    assert login_response.status_code == 401


async def test_only_platform_superadmin_can_soft_delete_and_reinvite_user(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    target = User(
        org_id=seeded_user.org_id,
        email="delete-me@acme.com",
        password_hash=seeded_user.password_hash,
    )
    session.add(target)
    await session.commit()
    regular_headers = await auth(client, seeded_user.email)

    denied = await client.delete(
        f"/api/v1/users/{target.id}",
        headers=regular_headers,
    )
    assert denied.status_code == 403

    seeded_user.is_platform_superadmin = True
    await session.commit()
    superadmin_headers = await auth(client, seeded_user.email)
    deleted = await client.delete(
        f"/api/v1/users/{target.id}",
        headers=superadmin_headers,
    )

    assert deleted.status_code == 204
    await session.refresh(target)
    assert target.active is False
    assert target.deleted_at is not None
    assert target.email.endswith("@deleted.invalid")
    listed = await client.get("/api/v1/users", headers=superadmin_headers)
    assert "delete-me@acme.com" not in {item["email"] for item in listed.json()}

    role_id = (
        await session.execute(
            select(Role.id).where(
                Role.org_id == seeded_user.org_id,
                Role.key == "user",
            )
        )
    ).scalar_one()
    reinvite = await client.post(
        "/api/v1/auth/invitations",
        json={"email": "delete-me@acme.com", "role_id": str(role_id)},
        headers=superadmin_headers,
    )
    assert reinvite.status_code == 202
