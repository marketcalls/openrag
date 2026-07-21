import hashlib
from uuid import uuid4

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth import service
from openrag.modules.auth.models import Invitation, User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.auth.tokens import decode_access_token
from openrag.modules.tenancy.authorization import resolve_authorization
from openrag.modules.tenancy.context import TenantContext
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
    role = (
        await session.execute(
            select(Role).where(
                Role.org_id == seeded_user.org_id,
                Role.key == "engineer",
            )
        )
    ).scalar_one()
    authorization = await resolve_authorization(session, seeded_user)
    invitation_token = await service.create_invitation(
        session,
        TenantContext(
            user_id=seeded_user.id,
            org_id=seeded_user.org_id,
            authorization=authorization,
        ),
        email="new@acme.com",
        role_id=role.id,
    )
    accepted = await client.post(
        "/api/v1/auth/invitations/accept",
        json={
            "token": invitation_token,
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

    responses = [
        await client.post(
            "/api/v1/auth/invitations",
            json={"email": email, "role_id": str(role.id)},
            headers=headers,
        )
        for email in (
            foreign_user.email,
            seeded_user.email,
            "unknown@foreign.example.com",
        )
    ]

    assert {response.status_code for response in responses} == {202}
    bodies = [response.json() for response in responses]
    assert all(body["accepted"] is True for body in bodies)
    assert all(
        body["accept_path"].startswith("/invite?token=") for body in bodies
    )
    assert len({len(body["accept_path"]) for body in bodies}) == 1
    assert all(
        email not in response.text
        for response, email in zip(
            responses,
            (
                foreign_user.email,
                seeded_user.email,
                "unknown@foreign.example.com",
            ),
            strict=True,
        )
    )
    invitations = list(
        (
            await session.execute(
                select(Invitation).where(Invitation.org_id == seeded_user.org_id)
            )
        ).scalars()
    )
    assert {invitation.email for invitation in invitations} == {
        foreign_user.email,
        seeded_user.email,
        "unknown@foreign.example.com",
    }

    authorization = await resolve_authorization(session, seeded_user)
    foreign_token = await service.create_invitation(
        session,
        TenantContext(
            user_id=seeded_user.id,
            org_id=seeded_user.org_id,
            authorization=authorization,
        ),
        email=foreign_user.email,
        role_id=role.id,
    )
    rejected = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": foreign_token, "password": "newpw12345"},
    )
    assert rejected.status_code == 401
    consumed = (
        await session.execute(
            select(Invitation).where(
                Invitation.token_hash
                == hashlib.sha256(foreign_token.encode()).hexdigest()
            )
        )
    ).scalar_one()
    await session.refresh(consumed)
    assert consumed.accepted_at is not None
    assert (
        await client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": foreign_token, "password": "otherpw12345"},
        )
    ).status_code == 401
    assert (
        await session.execute(
            select(User).where(
                User.org_id == seeded_user.org_id,
                User.email == foreign_user.email,
            )
        )
    ).scalar_one_or_none() is None


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
            "iat": 1,
            "exp": 2,
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
