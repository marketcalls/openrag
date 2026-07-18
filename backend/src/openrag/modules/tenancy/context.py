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
from openrag.modules.tenancy.models import WorkspaceMember


@dataclass(frozen=True)
class TenantContext:
    user_id: UUID
    org_id: UUID
    role: str
    workspace_ids: frozenset[UUID]


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
    if user is None or not user.active:
        raise AuthenticationError("unknown or inactive user")

    workspace_ids = (
        await session.execute(
            select(WorkspaceMember.workspace_id).where(
                WorkspaceMember.user_id == user.id
            )
        )
    ).scalars().all()
    return TenantContext(
        user_id=user.id,
        org_id=user.org_id,
        role=user.role,
        workspace_ids=frozenset(workspace_ids),
    )


def require_role(
    *roles: str,
) -> Callable[[TenantContext], Awaitable[TenantContext]]:
    async def guard(
        context: Annotated[TenantContext, Depends(get_tenant_context)],
    ) -> TenantContext:
        if context.role != "superadmin" and context.role not in roles:
            raise AuthorizationError(f"requires role in {sorted(roles)}")
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
