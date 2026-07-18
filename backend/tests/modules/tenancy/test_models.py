from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.tenancy.models import Organization


async def test_create_org_and_user(session: AsyncSession) -> None:
    organization = Organization(name="Acme")
    session.add(organization)
    await session.flush()

    user = User(
        org_id=organization.id,
        email="a@acme.com",
        password_hash="x",  # noqa: S106 - deliberately inert test fixture
        role="admin",
    )
    session.add(user)
    await session.commit()

    found = (
        await session.execute(select(User).where(User.email == "a@acme.com"))
    ).scalar_one()
    assert found.org_id == organization.id
    assert found.active is True
