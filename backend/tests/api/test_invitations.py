from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth import service
from openrag.modules.auth.models import Invitation, User
from openrag.modules.tenancy.authorization import resolve_authorization
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Role


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_invite_flow(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    role = (
        await session.execute(
            select(Role).where(
                Role.org_id == seeded_user.org_id,
                Role.key == "user",
            )
        )
    ).scalar_one()
    authorization = await resolve_authorization(session, seeded_user)
    token = await service.create_invitation(
        session,
        TenantContext(
            user_id=seeded_user.id,
            org_id=seeded_user.org_id,
            authorization=authorization,
        ),
        email="new@acme.com",
        role_id=role.id,
    )

    accept_response = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "newpw12345"},
    )
    assert accept_response.status_code == 201

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "new@acme.com", "password": "newpw12345"},
    )
    assert login_response.status_code == 200

    reuse_response = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "other12345"},
    )
    assert reuse_response.status_code == 401


async def test_invite_requires_admin(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    response = await client.post(
        "/api/v1/auth/invitations",
        json={"email": "x@x.com", "role": "user"},
    )
    assert response.status_code == 401


async def test_admin_receives_one_time_manual_invite_link(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    role = (
        await session.execute(
            select(Role).where(
                Role.org_id == seeded_user.org_id,
                Role.key == "user",
            )
        )
    ).scalar_one()
    headers = await auth(client, seeded_user.email)

    response = await client.post(
        "/api/v1/auth/invitations",
        json={"email": "manual-share@acme.com", "role_id": str(role.id)},
        headers=headers,
    )

    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["accepted"] is True
    accept_path = response.json()["accept_path"]
    assert accept_path.startswith("/invite?token=")
    raw_token = parse_qs(urlparse(accept_path).query)["token"][0]
    invitation = (
        await session.execute(
            select(Invitation).where(Invitation.email == "manual-share@acme.com")
        )
    ).scalar_one()
    assert invitation.token_hash != raw_token

    accepted = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": raw_token, "password": "newpw12345"},
    )
    assert accepted.status_code == 201


async def test_new_manual_link_revokes_older_pending_link_for_same_email(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    role = (
        await session.execute(
            select(Role).where(
                Role.org_id == seeded_user.org_id,
                Role.key == "user",
            )
        )
    ).scalar_one()
    headers = await auth(client, seeded_user.email)

    first = await client.post(
        "/api/v1/auth/invitations",
        json={"email": "replace-link@acme.com", "role_id": str(role.id)},
        headers=headers,
    )
    second = await client.post(
        "/api/v1/auth/invitations",
        json={"email": "replace-link@acme.com", "role_id": str(role.id)},
        headers=headers,
    )
    first_token = parse_qs(urlparse(first.json()["accept_path"]).query)["token"][0]
    second_token = parse_qs(urlparse(second.json()["accept_path"]).query)["token"][0]

    revoked = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": first_token, "password": "newpw12345"},
    )
    accepted = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": second_token, "password": "newpw12345"},
    )

    assert revoked.status_code == 401
    assert accepted.status_code == 201
