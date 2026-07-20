"""Short-transaction lease claims for crash-recoverable agent execution."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.db import naive_utc
from openrag.modules.runs.models import AgentRun

MAX_RUN_ATTEMPTS = 8


@dataclass(frozen=True, slots=True)
class RunLeaseClaim:
    run_id: UUID
    org_id: UUID
    workspace_id: UUID
    chat_id: UUID
    token: UUID
    owner: str
    attempt: int
    recovered: bool


@dataclass(frozen=True, slots=True)
class ExhaustedRun:
    run_id: UUID
    org_id: UUID
    workspace_id: UUID
    chat_id: UUID


def _validate(owner: str, lease_seconds: int) -> None:
    if not 1 <= len(owner) <= 200:
        raise ValueError("run_lease_owner_invalid")
    if not 15 <= lease_seconds <= 600:
        raise ValueError("run_lease_seconds_invalid")


async def claim_next_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    lease_seconds: int,
) -> RunLeaseClaim | None:
    """Claim queued work or take over one expired attempt using SKIP LOCKED."""

    _validate(owner, lease_seconds)
    now = naive_utc()
    async with session_factory.begin() as session:
        run = await session.scalar(
            select(AgentRun)
            .where(
                AgentRun.cancel_requested_at.is_(None),
                AgentRun.attempts < MAX_RUN_ATTEMPTS,
                or_(
                    AgentRun.status == "queued",
                    and_(
                        AgentRun.status == "running",
                        AgentRun.lease_expires_at.is_not(None),
                        AgentRun.lease_expires_at <= now,
                    ),
                ),
            )
            .order_by(AgentRun.accepted_at, AgentRun.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if run is None:
            return None
        recovered = run.status == "running"
        token = uuid4()
        run.status = "running"
        run.started_at = run.started_at or now
        run.lease_owner = owner
        run.lease_token = token
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        run.attempts += 1
        await session.flush()
        return RunLeaseClaim(
            run_id=run.id,
            org_id=run.org_id,
            workspace_id=run.workspace_id,
            chat_id=run.chat_id,
            token=token,
            owner=owner,
            attempt=run.attempts,
            recovered=recovered,
        )


async def renew_run_lease(
    session_factory: async_sessionmaker[AsyncSession],
    claim: RunLeaseClaim,
    *,
    lease_seconds: int,
) -> bool:
    _validate(claim.owner, lease_seconds)
    expires_at = naive_utc() + timedelta(seconds=lease_seconds)
    async with session_factory.begin() as session:
        result = await session.execute(
            update(AgentRun)
            .where(
                AgentRun.id == claim.run_id,
                AgentRun.status == "running",
                AgentRun.lease_token == claim.token,
                AgentRun.lease_owner == claim.owner,
                AgentRun.cancel_requested_at.is_(None),
            )
            .values(lease_expires_at=expires_at)
            .returning(AgentRun.id)
        )
        return result.scalar_one_or_none() == claim.run_id


async def fail_exhausted_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> ExhaustedRun | None:
    """Terminally reconcile one expired run after its bounded retry budget."""

    now = naive_utc()
    async with session_factory.begin() as session:
        candidate = await session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.status == "running",
                AgentRun.attempts >= MAX_RUN_ATTEMPTS,
                AgentRun.lease_expires_at.is_not(None),
                AgentRun.lease_expires_at <= now,
                AgentRun.cancel_requested_at.is_(None),
            )
            .order_by(AgentRun.lease_expires_at, AgentRun.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if candidate is None:
            return None
        row = (
            await session.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == candidate,
                    AgentRun.status == "running",
                    AgentRun.attempts >= MAX_RUN_ATTEMPTS,
                    AgentRun.lease_expires_at <= now,
                    AgentRun.cancel_requested_at.is_(None),
                )
                .values(
                    status="failed",
                    error_code="retry_exhausted",
                    finished_at=now,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                )
                .returning(
                    AgentRun.id,
                    AgentRun.org_id,
                    AgentRun.workspace_id,
                    AgentRun.chat_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        return ExhaustedRun(
            run_id=row.id,
            org_id=row.org_id,
            workspace_id=row.workspace_id,
            chat_id=row.chat_id,
        )
