import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth import service
from openrag.modules.auth.models import User
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
