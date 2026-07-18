from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from openrag.bootstrap import bootstrap_superadmin
from openrag.core.db import build_session_factory
from openrag.modules.auth.models import User


async def test_bootstrap_idempotent(engine: AsyncEngine) -> None:
    factory = build_session_factory(engine)
    assert (
        await bootstrap_superadmin(
            factory,
            email="root@x.com",
            password="rootpw12345",  # noqa: S106 - inert test credential
        )
        is True
    )
    assert (
        await bootstrap_superadmin(
            factory,
            email="root@x.com",
            password="rootpw12345",  # noqa: S106 - inert test credential
        )
        is False
    )
    async with factory() as session:
        user = (
            await session.execute(select(User).where(User.email == "root@x.com"))
        ).scalar_one()
        assert user.is_platform_superadmin is True


async def test_http_user_contract_cannot_create_platform_superadmin(
    client,
    seeded_user: User,
) -> None:
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "pw123456"},
    )
    response = await client.patch(
        f"/api/v1/users/{seeded_user.id}",
        json={"is_platform_superadmin": True},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert response.status_code == 422
