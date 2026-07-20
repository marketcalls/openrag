"""Concurrency-safe authoritative lifecycle for asynchronous agent runs."""

from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.elements import ColumnElement

from openrag.modules.runs.events import RunEventEnvelope, RunEventType
from openrag.modules.runs.models import AgentRun

_NONTERMINAL = ("accepted", "queued", "running")
_SAFE_ERROR_CODES = frozenset(
    {
        "grounding_failed",
        "internal",
        "model_unavailable",
        "persistence_failed",
        "provider_rejected",
        "provider_transient",
        "rate_limited",
        "retrieval_failed",
        "retry_exhausted",
        "timeout",
    }
)


@dataclass(frozen=True, slots=True)
class RunIdentity:
    run_id: UUID
    org_id: UUID
    workspace_id: UUID
    chat_id: UUID


class RunTransitionRepository(Protocol):
    async def start(self, run_id: UUID) -> RunIdentity | None: ...

    async def first_token(self, run_id: UUID) -> RunIdentity | None: ...

    async def complete(
        self,
        run_id: UUID,
        *,
        assistant_message_id: UUID | None,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> RunIdentity | None: ...

    async def fail(
        self,
        run_id: UUID,
        *,
        error_code: str,
    ) -> RunIdentity | None: ...

    async def request_cancel(self, run_id: UUID) -> RunIdentity | None: ...

    async def acknowledge_cancel(
        self,
        run_id: UUID,
    ) -> RunIdentity | None: ...

    async def is_cancel_requested(self, run_id: UUID) -> bool: ...


class LifecycleEventBus(Protocol):
    async def append(
        self,
        *,
        event_type: RunEventType,
        run_id: UUID,
        org_id: UUID,
        workspace_id: UUID,
        chat_id: UUID,
        payload: dict[str, object],
        event_id: UUID | None = None,
    ) -> RunEventEnvelope: ...


def _identity(row: RowMapping | None) -> RunIdentity | None:
    if row is None:
        return None
    return RunIdentity(
        run_id=cast(UUID, row["run_id"]),
        org_id=cast(UUID, row["org_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        chat_id=cast(UUID, row["chat_id"]),
    )


def _returning_identity(statement: Update) -> Update:
    return statement.returning(
        AgentRun.id.label("run_id"),
        AgentRun.org_id,
        AgentRun.workspace_id,
        AgentRun.chat_id,
    )


class SqlRunTransitionRepository:
    """Every method is one conditional UPDATE in one short transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        lease_token: UUID | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._lease_token = lease_token

    def _lease_conditions(self) -> tuple[ColumnElement[bool], ...]:
        if self._lease_token is None:
            return ()
        return (AgentRun.lease_token == self._lease_token,)

    async def _execute(self, statement: Update) -> RunIdentity | None:
        async with self._session_factory.begin() as session:
            result = await session.execute(statement)
            return _identity(result.mappings().one_or_none())

    async def start(self, run_id: UUID) -> RunIdentity | None:
        now = func.timezone("UTC", func.now())
        statement = (
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.status.in_(("accepted", "queued")),
                AgentRun.cancel_requested_at.is_(None),
            )
            .values(status="running", started_at=now)
        )
        return await self._execute(_returning_identity(statement))

    async def first_token(self, run_id: UUID) -> RunIdentity | None:
        statement = (
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.status == "running",
                AgentRun.cancel_requested_at.is_(None),
                AgentRun.first_token_at.is_(None),
                *self._lease_conditions(),
            )
            .values(first_token_at=func.timezone("UTC", func.now()))
        )
        return await self._execute(_returning_identity(statement))

    async def complete(
        self,
        run_id: UUID,
        *,
        assistant_message_id: UUID | None,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> RunIdentity | None:
        statement = (
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.status == "running",
                AgentRun.cancel_requested_at.is_(None),
                *self._lease_conditions(),
            )
            .values(
                status="completed",
                assistant_message_id=assistant_message_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                finished_at=func.timezone("UTC", func.now()),
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
        )
        return await self._execute(_returning_identity(statement))

    async def fail(
        self,
        run_id: UUID,
        *,
        error_code: str,
    ) -> RunIdentity | None:
        statuses = ("running",) if self._lease_token is not None else _NONTERMINAL
        statement = (
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.status.in_(statuses),
                AgentRun.cancel_requested_at.is_(None),
                *self._lease_conditions(),
            )
            .values(
                status="failed",
                error_code=error_code,
                finished_at=func.timezone("UTC", func.now()),
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
        )
        return await self._execute(_returning_identity(statement))

    async def request_cancel(self, run_id: UUID) -> RunIdentity | None:
        statement = (
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.status.in_(_NONTERMINAL),
                AgentRun.cancel_requested_at.is_(None),
            )
            .values(cancel_requested_at=func.timezone("UTC", func.now()))
        )
        return await self._execute(_returning_identity(statement))

    async def acknowledge_cancel(
        self,
        run_id: UUID,
    ) -> RunIdentity | None:
        statement = (
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.status.in_(_NONTERMINAL),
                AgentRun.cancel_requested_at.is_not(None),
            )
            .values(
                status="cancelled",
                finished_at=func.timezone("UTC", func.now()),
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
        )
        return await self._execute(_returning_identity(statement))

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(
                        AgentRun.cancel_requested_at,
                        AgentRun.status,
                    ).where(AgentRun.id == run_id)
                )
            ).one_or_none()
        return row is not None and (
            row.cancel_requested_at is not None or row.status == "cancelled"
        )


class RunLifecycle:
    def __init__(
        self,
        repository: RunTransitionRepository,
        bus: LifecycleEventBus,
    ) -> None:
        self._repository = repository
        self._bus = bus

    async def _emit(
        self,
        identity: RunIdentity,
        event_type: RunEventType,
        payload: dict[str, object],
    ) -> None:
        await self._bus.append(
            event_type=event_type,
            run_id=identity.run_id,
            org_id=identity.org_id,
            workspace_id=identity.workspace_id,
            chat_id=identity.chat_id,
            payload=payload,
            event_id=uuid5(
                identity.run_id,
                f"openrag-run-event:{event_type}",
            ),
        )

    async def start(self, run_id: UUID) -> bool:
        identity = await self._repository.start(run_id)
        if identity is None:
            return False
        await self._emit(identity, "run.started", {})
        return True

    async def announce_start(
        self,
        identity: RunIdentity,
        *,
        attempt: int,
        recovered: bool,
    ) -> None:
        if attempt < 1:
            raise ValueError("run_attempt_invalid")
        await self._emit(
            identity,
            "run.started",
            {"attempt": attempt, "recovered": recovered},
        )

    async def first_token(self, run_id: UUID) -> bool:
        identity = await self._repository.first_token(run_id)
        if identity is None:
            return False
        await self._emit(identity, "message.started", {})
        return True

    async def complete(
        self,
        run_id: UUID,
        *,
        assistant_message_id: UUID | None,
        usage: tuple[int, int],
    ) -> bool:
        prompt_tokens, completion_tokens = usage
        if prompt_tokens < 0 or completion_tokens < 0:
            raise ValueError("run_usage_invalid")
        identity = await self._repository.complete(
            run_id,
            assistant_message_id=assistant_message_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        if identity is None:
            return False
        await self._emit(
            identity,
            "run.completed",
            {
                "assistant_message_id": (
                    str(assistant_message_id) if assistant_message_id is not None else None
                ),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        )
        return True

    async def fail(self, run_id: UUID, *, error_code: str) -> bool:
        if error_code not in _SAFE_ERROR_CODES:
            raise ValueError("run_error_code_invalid")
        identity = await self._repository.fail(
            run_id,
            error_code=error_code,
        )
        if identity is None:
            return False
        await self._emit(
            identity,
            "run.failed",
            {"error_code": error_code},
        )
        return True

    async def announce_failure(
        self,
        identity: RunIdentity,
        *,
        error_code: str,
    ) -> None:
        if error_code not in _SAFE_ERROR_CODES:
            raise ValueError("run_error_code_invalid")
        await self._emit(
            identity,
            "run.failed",
            {"error_code": error_code},
        )

    async def request_cancel(self, run_id: UUID) -> bool:
        identity = await self._repository.request_cancel(run_id)
        if identity is None:
            return False
        await self._emit(identity, "run.cancel.requested", {})
        return True

    async def acknowledge_cancel(self, run_id: UUID) -> bool:
        identity = await self._repository.acknowledge_cancel(run_id)
        if identity is None:
            return False
        await self._emit(identity, "run.cancelled", {})
        return True

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        return await self._repository.is_cancel_requested(run_id)
