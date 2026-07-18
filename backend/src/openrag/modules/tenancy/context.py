from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.app_settings import get_or_create_signing_key
from openrag.core.db import get_session
from openrag.core.errors import AuthenticationError, AuthorizationError
from openrag.core.ratelimit import FixedWindowLimiter, RedisFixedWindowLimiter
from openrag.modules.auth.models import User
from openrag.modules.auth.tokens import decode_access_token
from openrag.modules.tenancy.authorization import (
    AuthorizationSnapshot,
    resolve_authorization,
)
from openrag.modules.tenancy.permissions import ALL_PERMISSIONS


@dataclass(frozen=True)
class TenantContext:
    user_id: UUID
    org_id: UUID
    authorization: AuthorizationSnapshot

    def __post_init__(self) -> None:
        if (
            self.authorization.user_id != self.user_id
            or self.authorization.org_id != self.org_id
        ):
            raise ValueError("authorization snapshot does not match tenant context")

    @property
    def workspace_ids(self) -> frozenset[UUID]:
        return self.authorization.workspace_ids


_bearer = HTTPBearer(auto_error=False)


async def get_tenant_context(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantContext:
    if credentials is None:
        raise AuthenticationError("missing bearer token")

    signing_key = await get_or_create_signing_key(session)
    claims = decode_access_token(credentials.credentials, signing_key)
    user = (
        await session.execute(select(User).where(User.id == claims.user_id))
    ).scalar_one_or_none()
    # Only the signed subject identifies the principal. Organization and permission
    # claims are display hints; authoritative state is reloaded for every request.
    if user is None or not user.active:
        raise AuthenticationError("unknown or inactive user")
    authorization = await resolve_authorization(session, user)
    return TenantContext(
        user_id=user.id,
        org_id=user.org_id,
        authorization=authorization,
    )


def require_permission(
    permission: str,
) -> Callable[[TenantContext], Awaitable[TenantContext]]:
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"unknown permission: {permission}")

    async def guard(
        context: Annotated[TenantContext, Depends(get_tenant_context)],
    ) -> TenantContext:
        if not context.authorization.has(permission):
            raise AuthorizationError(f"requires permission: {permission}")
        return context

    return guard


def require_platform_superadmin(
) -> Callable[[TenantContext], Awaitable[TenantContext]]:
    async def guard(
        context: Annotated[TenantContext, Depends(get_tenant_context)],
    ) -> TenantContext:
        if not context.authorization.is_platform_superadmin:
            raise AuthorizationError("requires platform superadmin")
        return context

    return guard


def rate_limit_user(
    scope: str,
    limit: int,
    window_seconds: int,
) -> Callable[..., Awaitable[TenantContext]]:
    """Authenticate first, then rate-limit using the stable user id."""
    local = FixedWindowLimiter(limit, window_seconds)
    shared = RedisFixedWindowLimiter(limit, window_seconds)

    async def guard(
        request: Request,
        context: Annotated[TenantContext, Depends(get_tenant_context)],
    ) -> TenantContext:
        redis = getattr(request.app.state, "redis", None)
        key = f"{scope}:{context.user_id}"
        if redis is None:
            local.check(f"{id(request.app)}:{key}")
        else:
            await shared.check(redis, key)
        return context

    return guard
