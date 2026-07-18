import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.auth.tokens import decode_access_token
from openrag.modules.tenancy.models import Role, UserRoleBinding


async def test_login_ok(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "a@acme.com", "password": "pw123456"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert "refresh_token" in response.cookies
    from openrag.core.app_settings import get_or_create_signing_key

    claims = decode_access_token(
        response.json()["access_token"], await get_or_create_signing_key(session)
    )
    assert claims.is_platform_superadmin is False
    assert "role.manage" in claims.permissions


async def test_login_bad_password_problem_json(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "a@acme.com", "password": "bad"},
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["title"] == "Authentication failed"


async def test_refresh_and_logout(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "a@acme.com", "password": "pw123456"},
    )
    refresh_response = await client.post("/api/v1/auth/refresh")
    assert refresh_response.status_code == 200
    assert (
        refresh_response.cookies["refresh_token"]
        != login_response.cookies["refresh_token"]
    )

    logout_response = await client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 204
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_invitation_accepts_role_id_and_creates_binding(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "pw123456"},
    )
    headers = {
        "Authorization": f"Bearer {login_response.json()['access_token']}"
    }
    role = (
        await session.execute(
            select(Role).where(
                Role.org_id == seeded_user.org_id,
                Role.key == "engineer",
            )
        )
    ).scalar_one()
    invitation = await client.post(
        "/api/v1/auth/invitations",
        json={"email": "new@acme.com", "role_id": str(role.id)},
        headers=headers,
    )
    assert invitation.status_code == 201
    accepted = await client.post(
        "/api/v1/auth/invitations/accept",
        json={
            "token": invitation.json()["invite_token"],
            "password": "newpw12345",
        },
    )
    assert accepted.status_code == 201
    user = (
        await session.execute(select(User).where(User.email == "new@acme.com"))
    ).scalar_one()
    binding = (
        await session.execute(
            select(UserRoleBinding).where(UserRoleBinding.user_id == user.id)
        )
    ).scalar_one()
    assert binding.role_id == role.id


async def test_invitation_rejects_legacy_role_string(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "pw123456"},
    )
    response = await client.post(
        "/api/v1/auth/invitations",
        json={"email": "unsafe@acme.com", "role": "superadmin"},
        headers={
            "Authorization": f"Bearer {login_response.json()['access_token']}"
        },
    )
    assert response.status_code == 422
