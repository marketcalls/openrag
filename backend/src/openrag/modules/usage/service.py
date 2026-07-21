"""Tenant-safe monthly token usage accounting and quota enforcement."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.db import naive_utc
from openrag.core.errors import NotFoundError, QuotaExceeded
from openrag.modules.audit.service import record_audit
from openrag.modules.auth.models import User
from openrag.modules.runs.models import AgentRun
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.usage.models import OrgQuota, UserQuota
from openrag.modules.usage.schemas import UserQuotaOut

_WARNING_RATIO = 0.8
_TOKENS = AgentRun.prompt_tokens + AgentRun.completion_tokens


def _clamped(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, min(day, monthrange(year, month)[1]))


def period_bounds(now: datetime, reset_day: int) -> tuple[datetime, datetime]:
    """Return the current quota period start and next reset in naive UTC."""

    current = _clamped(now.year, now.month, reset_day)
    if now >= current:
        next_year, next_month = (
            (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        )
        return current, _clamped(next_year, next_month, reset_day)
    previous_year, previous_month = (
        (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    )
    return _clamped(previous_year, previous_month, reset_day), current


async def _sum_since(
    session: AsyncSession,
    start: datetime,
    *,
    org_id: UUID | None = None,
    user_id: UUID | None = None,
) -> int:
    statement = select(func.coalesce(func.sum(_TOKENS), 0)).where(
        AgentRun.accepted_at >= start
    )
    if org_id is not None:
        statement = statement.where(AgentRun.org_id == org_id)
    if user_id is not None:
        statement = statement.where(AgentRun.user_id == user_id)
    return int((await session.execute(statement)).scalar_one())


@dataclass(frozen=True, slots=True)
class UsageStatus:
    used_tokens: int
    allocated_tokens: int | None
    org_used_tokens: int
    org_allocated_tokens: int | None
    resets_at: datetime
    warning: bool
    blocked: bool


async def get_usage_status(
    session: AsyncSession,
    *,
    org_id: UUID,
    user_id: UUID,
) -> UsageStatus:
    org_quota = await session.scalar(select(OrgQuota).where(OrgQuota.org_id == org_id))
    reset_day = org_quota.reset_day if org_quota is not None else 1
    start, resets_at = period_bounds(naive_utc(), reset_day)
    used_tokens = await _sum_since(session, start, user_id=user_id)
    org_used_tokens = await _sum_since(session, start, org_id=org_id)
    user_quota = await session.scalar(
        select(UserQuota).where(
            UserQuota.org_id == org_id,
            UserQuota.user_id == user_id,
        )
    )
    allocated_tokens = (
        user_quota.monthly_tokens
        if user_quota is not None
        else (
            org_quota.default_user_monthly_tokens
            if org_quota is not None
            else None
        )
    )
    org_allocated_tokens = org_quota.monthly_tokens if org_quota is not None else None

    def ratio(used: int, allocated: int | None) -> float:
        return used / allocated if allocated else 0.0

    warning = max(
        ratio(used_tokens, allocated_tokens),
        ratio(org_used_tokens, org_allocated_tokens),
    ) >= _WARNING_RATIO
    blocked = (
        allocated_tokens is not None and used_tokens >= allocated_tokens
    ) or (
        org_allocated_tokens is not None
        and org_used_tokens >= org_allocated_tokens
    )
    return UsageStatus(
        used_tokens=used_tokens,
        allocated_tokens=allocated_tokens,
        org_used_tokens=org_used_tokens,
        org_allocated_tokens=org_allocated_tokens,
        resets_at=resets_at,
        warning=warning,
        blocked=blocked,
    )


async def check_quota(
    session: AsyncSession,
    *,
    org_id: UUID,
    user_id: UUID,
) -> None:
    status = await get_usage_status(session, org_id=org_id, user_id=user_id)
    if status.blocked:
        raise QuotaExceeded(
            f"monthly token quota exhausted; resets "
            f"{status.resets_at.date().isoformat()}"
        )


async def get_org_quota(session: AsyncSession, org_id: UUID) -> OrgQuota | None:
    return (
        await session.execute(select(OrgQuota).where(OrgQuota.org_id == org_id))
    ).scalar_one_or_none()


async def set_org_quota(
    session: AsyncSession,
    context: TenantContext,
    *,
    monthly_tokens: int,
    default_user_monthly_tokens: int | None,
    reset_day: int,
) -> OrgQuota:
    row = await get_org_quota(session, context.org_id)
    if row is None:
        row = OrgQuota(
            org_id=context.org_id,
            monthly_tokens=monthly_tokens,
            default_user_monthly_tokens=default_user_monthly_tokens,
            reset_day=reset_day,
        )
        session.add(row)
    else:
        row.monthly_tokens = monthly_tokens
        row.default_user_monthly_tokens = default_user_monthly_tokens
        row.reset_day = reset_day
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="quota.organization.updated",
        target_type="organization",
        target_id=str(context.org_id),
    )
    await session.commit()
    return row


async def set_user_quota(
    session: AsyncSession,
    context: TenantContext,
    user_id: UUID,
    monthly_tokens: int | None,
) -> None:
    user = await session.scalar(
        select(User).where(User.id == user_id, User.org_id == context.org_id)
    )
    if user is None:
        raise NotFoundError("user not found")
    if monthly_tokens is None:
        await session.execute(
            delete(UserQuota).where(
                UserQuota.org_id == context.org_id,
                UserQuota.user_id == user_id,
            )
        )
    else:
        row = await session.scalar(
            select(UserQuota).where(
                UserQuota.org_id == context.org_id,
                UserQuota.user_id == user_id,
            )
        )
        if row is None:
            session.add(
                UserQuota(
                    org_id=context.org_id,
                    user_id=user_id,
                    monthly_tokens=monthly_tokens,
                )
            )
        else:
            row.monthly_tokens = monthly_tokens
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="quota.user.updated",
        target_type="user",
        target_id=str(user_id),
    )
    await session.commit()


async def get_user_quota_with_usage(
    session: AsyncSession,
    context: TenantContext,
    user_id: UUID,
) -> UserQuotaOut:
    user = await session.scalar(
        select(User).where(User.id == user_id, User.org_id == context.org_id)
    )
    if user is None:
        raise NotFoundError("user not found")
    override = await session.scalar(
        select(UserQuota).where(
            UserQuota.org_id == context.org_id,
            UserQuota.user_id == user_id,
        )
    )
    status = await get_usage_status(
        session,
        org_id=context.org_id,
        user_id=user_id,
    )
    return UserQuotaOut(
        user_id=user_id,
        monthly_tokens=override.monthly_tokens if override is not None else None,
        used_tokens=status.used_tokens,
        allocated_tokens=status.allocated_tokens,
        resets_at=status.resets_at,
    )
