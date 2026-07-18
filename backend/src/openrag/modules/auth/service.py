import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.app_settings import get_or_create_signing_key
from openrag.core.config import Settings
from openrag.core.db import naive_utc
from openrag.core.errors import AuthenticationError, ConflictError, NotFoundError
from openrag.modules.auth.models import Invitation, RefreshToken, User
from openrag.modules.auth.passwords import hash_password, verify_password
from openrag.modules.auth.tokens import issue_access_token
from openrag.modules.tenancy.context import TenantContext


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _issue_pair(
    session: AsyncSession,
    user: User,
    family_id: UUID,
    settings: Settings,
) -> TokenPair:
    signing_key = await get_or_create_signing_key(session)
    raw_refresh = secrets.token_urlsafe(48)
    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id,
            token_hash=_hash(raw_refresh),
            expires_at=naive_utc()
            + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
    )
    await session.commit()
    access_token = issue_access_token(
        user_id=user.id,
        org_id=user.org_id,
        role=user.role,
        signing_key=signing_key,
        ttl_seconds=settings.access_token_ttl_seconds,
    )
    return TokenPair(access_token=access_token, refresh_token=raw_refresh)


async def login(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    settings: Settings,
) -> TokenPair:
    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None or not user.active or not verify_password(user.password_hash, password):
        raise AuthenticationError("invalid credentials")
    return await _issue_pair(session, user, uuid4(), settings)


async def rotate_refresh(
    session: AsyncSession,
    *,
    raw_refresh: str,
    settings: Settings,
) -> TokenPair:
    token = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == _hash(raw_refresh))
        )
    ).scalar_one_or_none()
    if token is None:
        raise AuthenticationError("unknown refresh token")

    now = naive_utc()
    if token.revoked_at is not None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id)
            .values(revoked_at=now)
        )
        await session.commit()
        raise AuthenticationError("refresh token reuse detected")
    if token.expires_at < now:
        raise AuthenticationError("refresh token expired")

    token.revoked_at = now
    user = (
        await session.execute(select(User).where(User.id == token.user_id))
    ).scalar_one()
    if not user.active:
        raise AuthenticationError("user inactive")
    return await _issue_pair(session, user, token.family_id, settings)


async def logout(session: AsyncSession, *, raw_refresh: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == _hash(raw_refresh))
        .values(revoked_at=naive_utc())
    )
    await session.commit()


async def create_invitation(
    session: AsyncSession,
    context: TenantContext,
    *,
    email: str,
    role: str,
    ttl_hours: int = 72,
) -> str:
    existing = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("email already registered")

    raw_token = secrets.token_urlsafe(32)
    session.add(
        Invitation(
            org_id=context.org_id,
            email=email,
            role=role,
            token_hash=_hash(raw_token),
            expires_at=naive_utc() + timedelta(hours=ttl_hours),
        )
    )
    await session.commit()
    return raw_token


async def accept_invitation(
    session: AsyncSession,
    *,
    raw_token: str,
    password: str,
) -> User:
    invitation = (
        await session.execute(
            select(Invitation).where(Invitation.token_hash == _hash(raw_token))
        )
    ).scalar_one_or_none()
    now = naive_utc()
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.expires_at < now
    ):
        raise AuthenticationError("invalid or expired invitation")

    invitation.accepted_at = now
    user = User(
        org_id=invitation.org_id,
        email=invitation.email,
        password_hash=hash_password(password),
        role=invitation.role,
    )
    session.add(user)
    await session.commit()
    return user


async def list_users(
    session: AsyncSession,
    context: TenantContext,
) -> list[User]:
    statement = (
        select(User)
        .where(User.org_id == context.org_id)
        .order_by(User.email)
    )
    return list((await session.execute(statement)).scalars())


async def get_user(
    session: AsyncSession,
    context: TenantContext,
    user_id: UUID,
) -> User:
    user = (
        await session.execute(
            select(User).where(
                User.id == user_id,
                User.org_id == context.org_id,
            )
        )
    ).scalar_one_or_none()
    if user is None or user.role == "superadmin":
        raise NotFoundError("user not found")
    return user


async def set_user_active(
    session: AsyncSession,
    context: TenantContext,
    user_id: UUID,
    active: bool,
) -> User:
    user = await get_user(session, context, user_id)
    user.active = active
    await session.commit()
    return user


async def set_user_role(
    session: AsyncSession,
    context: TenantContext,
    user_id: UUID,
    role: str,
) -> User:
    user = await get_user(session, context, user_id)
    user.role = role
    await session.commit()
    return user
