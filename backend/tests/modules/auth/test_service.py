import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.core.errors import AuthenticationError
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.auth.service import login, logout, rotate_refresh
from openrag.modules.tenancy.models import Organization

SETTINGS = Settings(_env_file=None)


async def make_user(session: AsyncSession, email: str = "u@acme.com") -> User:
    organization = Organization(name=f"org-{email}")
    session.add(organization)
    await session.flush()
    user = User(
        org_id=organization.id,
        email=email,
        password_hash=hash_password("pw123456"),
    )
    session.add(user)
    await session.commit()
    return user


async def test_login_returns_pair(session: AsyncSession) -> None:
    await make_user(session)
    pair = await login(
        session,
        email="u@acme.com",
        password="pw123456",  # noqa: S106 - inert test credential
        settings=SETTINGS,
    )
    assert pair.access_token
    assert pair.refresh_token


async def test_login_wrong_password(session: AsyncSession) -> None:
    await make_user(session)
    with pytest.raises(AuthenticationError):
        await login(
            session,
            email="u@acme.com",
            password="nope",  # noqa: S106 - inert test credential
            settings=SETTINGS,
        )


async def test_rotation_and_reuse_revokes_family(session: AsyncSession) -> None:
    await make_user(session)
    first = await login(
        session,
        email="u@acme.com",
        password="pw123456",  # noqa: S106 - inert test credential
        settings=SETTINGS,
    )
    second = await rotate_refresh(
        session,
        raw_refresh=first.refresh_token,
        settings=SETTINGS,
    )
    assert second.refresh_token != first.refresh_token

    with pytest.raises(AuthenticationError):
        await rotate_refresh(
            session,
            raw_refresh=first.refresh_token,
            settings=SETTINGS,
        )
    with pytest.raises(AuthenticationError):
        await rotate_refresh(
            session,
            raw_refresh=second.refresh_token,
            settings=SETTINGS,
        )


async def test_logout_revokes(session: AsyncSession) -> None:
    await make_user(session)
    pair = await login(
        session,
        email="u@acme.com",
        password="pw123456",  # noqa: S106 - inert test credential
        settings=SETTINGS,
    )
    await logout(session, raw_refresh=pair.refresh_token)
    with pytest.raises(AuthenticationError):
        await rotate_refresh(
            session,
            raw_refresh=pair.refresh_token,
            settings=SETTINGS,
        )
