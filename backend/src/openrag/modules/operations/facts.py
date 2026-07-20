"""Idempotent, content-free projection of terminal agent runs."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter_ns
from typing import cast
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from openrag.core.telemetry import record_active_rag_run
from openrag.modules.chat.models import Citation, Message
from openrag.modules.operations.models import RagRunFact
from openrag.modules.operations.schemas import (
    RagRoute,
    RagRunFactCreate,
    RagRunOutcome,
)
from openrag.modules.runs.models import AgentRun, RunContextLedger

_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
_ROUTES = frozenset({"direct", "conversation", "rag", "analytics", "clarify"})


@dataclass(frozen=True, slots=True)
class RunObservation:
    route_ms: int = 0
    retrieval_ms: int = 0
    provider_ms: int = 0
    persistence_ms: int = 0

    def __post_init__(self) -> None:
        if (
            min(
                self.route_ms,
                self.retrieval_ms,
                self.provider_ms,
                self.persistence_ms,
            )
            < 0
        ):
            raise ValueError("run_observation_invalid")


class RunStageTimer:
    """Collect content-free monotonic stage durations for one worker attempt."""

    def __init__(self, *, clock_ns: Callable[[], int] = perf_counter_ns) -> None:
        self._clock_ns = clock_ns
        self._started_ns = clock_ns()
        self._retrieval_started_ns: int | None = None
        self._provider_started_ns: int | None = None
        self._persistence_started_ns: int | None = None
        self._first_token_seen = False
        self._route_ms = 0
        self._retrieval_ms = 0
        self._provider_ms = 0
        self._persistence_ms = 0

    @staticmethod
    def _milliseconds(start_ns: int, finish_ns: int) -> int:
        return max(0, (finish_ns - start_ns) // 1_000_000)

    def route_selected(self) -> None:
        now = self._clock_ns()
        self._route_ms = self._milliseconds(self._started_ns, now)
        self._provider_started_ns = now

    def retrieval_started(self) -> None:
        self._retrieval_started_ns = self._clock_ns()

    def retrieval_completed(self) -> None:
        now = self._clock_ns()
        if self._retrieval_started_ns is not None:
            self._retrieval_ms = self._milliseconds(self._retrieval_started_ns, now)
        self._provider_started_ns = now

    def first_token(self) -> None:
        if not self._first_token_seen and self._provider_started_ns is not None:
            self._provider_ms = self._milliseconds(self._provider_started_ns, self._clock_ns())
            self._first_token_seen = True

    def persistence_started(self) -> None:
        self._persistence_started_ns = self._clock_ns()

    def persistence_completed(self) -> None:
        if self._persistence_started_ns is not None:
            self._persistence_ms = self._milliseconds(
                self._persistence_started_ns,
                self._clock_ns(),
            )

    def snapshot(self) -> RunObservation:
        return RunObservation(
            route_ms=self._route_ms,
            retrieval_ms=self._retrieval_ms,
            provider_ms=self._provider_ms,
            persistence_ms=self._persistence_ms,
        )


@dataclass(frozen=True, slots=True)
class RunFactSource:
    org_id: UUID
    workspace_id: UUID
    run_id: UUID
    model_id: UUID | None
    trace_id: str | None
    status: str
    route: str | None
    error_code: str | None
    prompt_tokens: int
    completion_tokens: int
    attempts: int
    accepted_at: datetime
    first_token_at: datetime | None
    finished_at: datetime | None
    answer_status: str | None
    retrieval_count: int
    citation_count: int
    memory_item_count: int


def _duration_ms(start: datetime, finish: datetime) -> int:
    value = int((finish - start).total_seconds() * 1000)
    if value < 0:
        raise ValueError("run_fact_time_order_invalid")
    return value


def _outcome(source: RunFactSource) -> RagRunOutcome:
    if source.status == "failed":
        return "failed"
    if source.status == "cancelled":
        return "cancelled"
    if source.answer_status == "refused":
        return "no_answer"
    if source.route == "rag":
        return "grounded" if source.citation_count > 0 else "no_answer"
    return "conversational"


def project_run_fact(
    source: RunFactSource,
    observation: RunObservation,
    *,
    environment: str,
    release: str | None,
) -> RagRunFactCreate:
    if source.status not in _TERMINAL_STATUSES or source.finished_at is None:
        raise ValueError("run_fact_not_terminal")
    latency_ms = _duration_ms(source.accepted_at, source.finished_at)
    ttft_ms = (
        _duration_ms(source.accepted_at, source.first_token_at)
        if source.first_token_at is not None
        else None
    )
    if ttft_ms is not None and ttft_ms > latency_ms:
        raise ValueError("run_fact_time_order_invalid")
    route = source.route if source.route in _ROUTES else "unknown"
    return RagRunFactCreate(
        org_id=source.org_id,
        workspace_id=source.workspace_id,
        run_id=source.run_id,
        model_id=source.model_id,
        trace_id=source.trace_id,
        environment=environment,
        release=release,
        route=cast(RagRoute, route),
        outcome=_outcome(source),
        error_code=source.error_code,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        route_ms=observation.route_ms,
        retrieval_ms=observation.retrieval_ms,
        provider_ms=observation.provider_ms,
        persistence_ms=observation.persistence_ms,
        prompt_tokens=source.prompt_tokens,
        completion_tokens=source.completion_tokens,
        retrieval_count=source.retrieval_count,
        citation_count=source.citation_count,
        memory_item_count=source.memory_item_count,
        attempts=source.attempts,
        estimated_cost_microusd=0,
        accepted_at=source.accepted_at,
        finished_at=source.finished_at,
    )


def build_run_fact_insert(fact: RagRunFactCreate) -> Insert:
    return (
        insert(RagRunFact)
        .values(**fact.model_dump())
        .on_conflict_do_nothing(
            index_elements=[RagRunFact.org_id, RagRunFact.run_id],
        )
    )


def build_unprojected_run_query() -> Select[tuple[UUID]]:
    projected = exists(
        select(RagRunFact.id).where(
            RagRunFact.org_id == AgentRun.org_id,
            RagRunFact.run_id == AgentRun.id,
        )
    )
    return (
        select(AgentRun.id)
        .where(
            AgentRun.status.in_(_TERMINAL_STATUSES),
            AgentRun.finished_at.is_not(None),
            ~projected,
        )
        .order_by(AgentRun.finished_at, AgentRun.id)
        .limit(1)
    )


async def record_run_fact(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    observation: RunObservation,
    *,
    environment: str,
    release: str | None,
) -> None:
    """Project one terminal run in a short, retry-safe database transaction."""

    async with session_factory.begin() as session:
        run = (
            await session.execute(
                select(
                    AgentRun.org_id,
                    AgentRun.workspace_id,
                    AgentRun.id.label("run_id"),
                    AgentRun.model_id,
                    AgentRun.trace_id,
                    AgentRun.status,
                    AgentRun.route,
                    AgentRun.error_code,
                    AgentRun.prompt_tokens,
                    AgentRun.completion_tokens,
                    AgentRun.attempts,
                    AgentRun.accepted_at,
                    AgentRun.first_token_at,
                    AgentRun.finished_at,
                    AgentRun.assistant_message_id,
                ).where(AgentRun.id == run_id)
            )
        ).one_or_none()
        if run is None:
            raise ValueError("run_fact_source_missing")

        context = (
            await session.execute(
                select(
                    RunContextLedger.retrieval_items,
                    RunContextLedger.memory_items,
                )
                .where(
                    RunContextLedger.org_id == run.org_id,
                    RunContextLedger.run_id == run.run_id,
                )
                .order_by(RunContextLedger.attempt.desc())
                .limit(1)
            )
        ).one_or_none()
        retrieval_count = context.retrieval_items if context is not None else 0
        memory_item_count = context.memory_items if context is not None else 0
        answer_status: str | None = None
        citation_count = 0
        if run.assistant_message_id is not None:
            answer_status = await session.scalar(
                select(Message.answer_status).where(
                    Message.org_id == run.org_id,
                    Message.workspace_id == run.workspace_id,
                    Message.id == run.assistant_message_id,
                )
            )
            citation_count = int(
                await session.scalar(
                    select(func.count(Citation.id)).where(
                        Citation.org_id == run.org_id,
                        Citation.workspace_id == run.workspace_id,
                        Citation.message_id == run.assistant_message_id,
                    )
                )
                or 0
            )

        fact = project_run_fact(
            RunFactSource(
                org_id=run.org_id,
                workspace_id=run.workspace_id,
                run_id=run.run_id,
                model_id=run.model_id,
                trace_id=run.trace_id,
                status=run.status,
                route=run.route,
                error_code=run.error_code,
                prompt_tokens=run.prompt_tokens,
                completion_tokens=run.completion_tokens,
                attempts=run.attempts,
                accepted_at=run.accepted_at,
                first_token_at=run.first_token_at,
                finished_at=run.finished_at,
                answer_status=answer_status,
                retrieval_count=retrieval_count,
                citation_count=citation_count,
                memory_item_count=memory_item_count,
            ),
            observation,
            environment=environment,
            release=release,
        )
        await session.execute(build_run_fact_insert(fact))

    error_category = "none"
    if fact.error_code:
        candidate = fact.error_code.split(".", maxsplit=1)[0]
        error_category = candidate if candidate in {
            "provider", "retrieval", "persistence", "internal", "cancelled"
        } else "other"
    record_active_rag_run(
        route=fact.route,
        outcome=fact.outcome,
        error_category=error_category,
        latency_ms=fact.latency_ms,
        ttft_ms=fact.ttft_ms,
        retrieval_pass_ratio=1.0 if fact.outcome == "grounded" else 0.0,
        citation_coverage_ratio=(
            min(1.0, fact.citation_count / fact.retrieval_count)
            if fact.retrieval_count > 0
            else 0.0
        ),
        estimated_cost_microusd=fact.estimated_cost_microusd,
    )


async def reconcile_run_fact_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    environment: str,
    release: str | None,
) -> bool:
    """Retry one missing terminal projection without holding a worker lease."""

    async with session_factory() as session:
        run_id = await session.scalar(build_unprojected_run_query())
    if run_id is None:
        return False
    await record_run_fact(
        session_factory,
        run_id,
        RunObservation(),
        environment=environment,
        release=release,
    )
    return True
