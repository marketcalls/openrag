from uuid import uuid4

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import Invitation, User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.auth.tokens import decode_access_token
from openrag.modules.tenancy.models import Organization, Role, UserRoleBinding


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


async def test_invitation_does_not_enumerate_foreign_tenant_email(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    foreign_org = Organization(name="Foreign invitation org")
    session.add(foreign_org)
    await session.flush()
    foreign_user = User(
        org_id=foreign_org.id,
        email="registered@foreign.example.com",
        password_hash=hash_password("pw123456"),
    )
    session.add(foreign_user)
    await session.commit()
    role = (
        await session.execute(
            select(Role).where(
                Role.org_id == seeded_user.org_id,
                Role.key == "user",
            )
        )
    ).scalar_one()
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "pw123456"},
    )
    headers = {
        "Authorization": f"Bearer {login_response.json()['access_token']}"
    }

    foreign = await client.post(
        "/api/v1/auth/invitations",
        json={
            "email": foreign_user.email,
            "role_id": str(role.id),
        },
        headers=headers,
    )
    unknown = await client.post(
        "/api/v1/auth/invitations",
        json={
            "email": "unknown@foreign.example.com",
            "role_id": str(role.id),
        },
        headers=headers,
    )

    assert foreign.status_code == unknown.status_code == 201
    assert set(foreign.json()) == set(unknown.json()) == {"invite_token"}
    invitations = list(
        (
            await session.execute(
                select(Invitation).where(Invitation.org_id == seeded_user.org_id)
            )
        ).scalars()
    )
    assert [invitation.email for invitation in invitations] == [
        "unknown@foreign.example.com"
    ]
    assert (
        await client.post(
            "/api/v1/auth/invitations/accept",
            json={
                "token": foreign.json()["invite_token"],
                "password": "newpw12345",
            },
        )
    ).status_code == 401


async def test_malformed_signed_claim_maps_to_http_401(
    client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    from openrag.core.app_settings import get_or_create_signing_key

    signing_key = await get_or_create_signing_key(session)
    malformed = jwt.encode(
        {
            "sub": str(uuid4()),
            "org": False,
            "platform_superadmin": False,
            "permissions": ["role.manage"],
        },
        signing_key,
        algorithm="HS256",
    )

    response = await client.get(
        "/api/v1/roles",
        headers={"Authorization": f"Bearer {malformed}"},
    )

    assert response.status_code == 401
    assert response.json()["title"] == "Authentication failed"
