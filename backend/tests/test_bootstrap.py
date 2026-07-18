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
        assert user.role == "superadmin"
