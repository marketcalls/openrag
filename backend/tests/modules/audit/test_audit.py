from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.modules.audit.models import AuditEvent
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.auth.service import login
from openrag.modules.tenancy.models import Organization


async def test_login_writes_audit(session: AsyncSession) -> None:
    organization = Organization(name="A")
    session.add(organization)
    await session.flush()
    session.add(
        User(
            org_id=organization.id,
            email="a@a.com",
            password_hash=hash_password("pw123456"),
            role="user",
        )
    )
    await session.commit()

    await login(
        session,
        email="a@a.com",
        password="pw123456",  # noqa: S106 - inert test credential
        settings=Settings(_env_file=None),
    )
    events = list((await session.execute(select(AuditEvent))).scalars())
    assert [event.action for event in events] == ["login.success"]
    assert events[0].org_id == organization.id
