import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.app_settings import get_or_create_signing_key
from openrag.core.config import Settings
from openrag.core.db import naive_utc
from openrag.core.errors import AuthenticationError, NotFoundError
from openrag.modules.audit.service import record_audit
from openrag.modules.auth.models import Invitation, RefreshToken, User
from openrag.modules.auth.passwords import hash_password, verify_password
from openrag.modules.auth.schemas import UserOut
from openrag.modules.auth.tokens import issue_access_token
from openrag.modules.tenancy import service as tenancy_service
from openrag.modules.tenancy.authorization import resolve_authorization
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Role, UserRoleBinding
from openrag.modules.tenancy.schemas import RoleOut


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _email_lock_id(email: str) -> int:
    digest = hashlib.sha256(email.casefold().encode()).digest()
    return int.from_bytes(digest[:8], signed=True)


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
    authorization = await resolve_authorization(session, user)
    access_token = issue_access_token(
        user_id=user.id,
        org_id=user.org_id,
        is_platform_superadmin=authorization.is_platform_superadmin,
        permissions=authorization.org_permissions,
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
        await record_audit(
            session,
            org_id=None,
            actor_id=None,
            action="login.failure",
            target_type="user",
            target_id=email,
        )
        await session.commit()
        raise AuthenticationError("invalid credentials")
    await record_audit(
        session,
        org_id=user.org_id,
        actor_id=user.id,
        action="login.success",
        target_type="user",
        target_id=str(user.id),
    )
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
    role_id: UUID,
    ttl_hours: int = 72,
) -> str:
    role = (
        await session.execute(
            select(Role).where(
                Role.id == role_id,
                Role.org_id == context.org_id,
                Role.is_assignable.is_(True),
            )
        )
    ).scalar_one_or_none()
    if role is None:
        raise NotFoundError("role not found")

    raw_token = secrets.token_urlsafe(32)
    invitation = Invitation(
        org_id=context.org_id,
        email=email,
        role_id=role.id,
        token_hash=_hash(raw_token),
        expires_at=naive_utc() + timedelta(hours=ttl_hours),
    )
    session.add(invitation)
    await session.flush()
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="invitation.created",
        target_type="invitation",
        target_id=str(invitation.id),
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
            select(Invitation)
            .where(Invitation.token_hash == _hash(raw_token))
            .with_for_update()
        )
    ).scalar_one_or_none()
    now = naive_utc()
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.expires_at < now
    ):
        raise AuthenticationError("invalid or expired invitation")

    role = (
        await session.execute(
            select(Role).where(
                Role.id == invitation.role_id,
                Role.org_id == invitation.org_id,
                Role.is_assignable.is_(True),
            )
        )
    ).scalar_one_or_none()
    if role is None:
        raise AuthenticationError("invalid or expired invitation")
    # Serialize invitation acceptance for the globally unique email, then reject
    # all pre-existing accounts with the same generic authentication error.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _email_lock_id(invitation.email)},
    )
    existing = (
        await session.execute(select(User.id).where(User.email == invitation.email))
    ).scalar_one_or_none()
    if existing is not None:
        # Consume the one-time credential even though account creation is refused;
        # otherwise a rejected token would remain replayable until expiry.
        invitation.accepted_at = now
        await record_audit(
            session,
            org_id=invitation.org_id,
            actor_id=None,
            action="invitation.rejected",
            target_type="invitation",
            target_id=str(invitation.id),
        )
        await session.commit()
        raise AuthenticationError("invalid or expired invitation")
    invitation.accepted_at = now
    user = User(
        org_id=invitation.org_id,
        email=invitation.email,
        password_hash=hash_password(password),
    )
    session.add(user)
    await session.flush()
    session.add(
        UserRoleBinding(
            org_id=invitation.org_id,
            user_id=user.id,
            role_id=role.id,
            created_by=None,
        )
    )
    await record_audit(
        session,
        org_id=invitation.org_id,
        actor_id=user.id,
        action="invitation.accepted",
        target_type="user",
        target_id=str(user.id),
    )
    await session.commit()
    return user


async def list_users(
    session: AsyncSession,
    context: TenantContext,
) -> list[User]:
    statement = (
        select(User)
        .where(
            User.org_id == context.org_id,
            User.is_platform_superadmin.is_(False),
        )
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
    if user is None or user.is_platform_superadmin:
        raise NotFoundError("user not found")
    return user


async def set_user_active(
    session: AsyncSession,
    context: TenantContext,
    user_id: UUID,
    active: bool,
) -> User:
    user = await get_user(session, context, user_id)
    if not active and user.active:
        await tenancy_service.ensure_can_deactivate_user(
            session,
            org_id=context.org_id,
            user_id=user.id,
        )
    user.active = active
    if not active:
        await record_audit(
            session,
            org_id=context.org_id,
            actor_id=context.user_id,
            action="user.deactivated",
            target_type="user",
            target_id=str(user.id),
        )
    await session.commit()
    return user


async def user_to_out(
    session: AsyncSession,
    user: User,
) -> UserOut:
    roles = list(
        (
            await session.execute(
                select(Role)
                .join(
                    UserRoleBinding,
                    (UserRoleBinding.role_id == Role.id)
                    & (UserRoleBinding.org_id == Role.org_id),
                )
                .where(
                    UserRoleBinding.org_id == user.org_id,
                    UserRoleBinding.user_id == user.id,
                    UserRoleBinding.workspace_id.is_(None),
                )
                .order_by(Role.name, Role.id)
            )
        ).scalars()
    )
    role_out: list[RoleOut] = await tenancy_service.roles_to_out(session, roles)
    return UserOut(
        id=user.id,
        email=user.email,
        active=user.active,
        is_platform_superadmin=user.is_platform_superadmin,
        roles=role_out,
    )


async def users_to_out(
    session: AsyncSession,
    users: list[User],
) -> list[UserOut]:
    return [await user_to_out(session, user) for user in users]
