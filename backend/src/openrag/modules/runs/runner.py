"""Lease, heartbeat, and execute queued runs through the Agno pipeline."""

import asyncio
from contextlib import suppress
from typing import Literal, cast
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings
from openrag.core.errors import ConflictError, UpstreamError
from openrag.modules.auth.models import User
from openrag.modules.chat import service as chat_service
from openrag.modules.chat.models import Message
from openrag.modules.models import service as models_service
from openrag.modules.models.reasoning import ReasoningEffort
from openrag.modules.operations.errors import record_error, top_application_frame
from openrag.modules.operations.facts import (
    RunObservation,
    RunStageTimer,
    reconcile_run_fact_once,
    record_run_fact,
)
from openrag.modules.operations.schemas import ErrorCategory, ErrorOccurrenceCreate
from openrag.modules.orchestration.runtime import create_model_execution
from openrag.modules.retrieval.service import retrieve
from openrag.modules.runs.context import record_run_context
from openrag.modules.runs.leases import (
    RunLeaseClaim,
    claim_next_run,
    fail_exhausted_run,
    renew_run_lease,
)
from openrag.modules.runs.lifecycle import (
    RunIdentity,
    RunLifecycle,
    SqlRunTransitionRepository,
)
from openrag.modules.runs.models import AgentRun
from openrag.modules.runs.reply_bridge import DurableReplyBridge, ReplyEventBus, RunOutcome
from openrag.modules.tenancy import service as tenancy_service
from openrag.modules.tenancy.authorization import resolve_authorization
from openrag.modules.tenancy.context import TenantContext

RunnerTickResult = Literal[
    "idle",
    "contested",
    "completed",
    "failed",
    "cancelled",
]
_logger = structlog.get_logger("openrag.run_worker")


async def _record_terminal_fact(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    run_id: UUID,
    observation: RunObservation,
) -> None:
    try:
        await record_run_fact(
            session_factory,
            run_id,
            observation,
            environment=settings.environment,
            release=settings.release,
        )
    except Exception as exc:  # noqa: BLE001 - the user run is already terminal
        _logger.error(
            "run_fact_projection_failed",
            run_id=str(run_id),
            exception_type=type(exc).__name__,
        )


async def _reconcile_terminal_fact(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    try:
        await reconcile_run_fact_once(
            session_factory,
            environment=settings.environment,
            release=settings.release,
        )
    except Exception as exc:  # noqa: BLE001 - reconciliation must not block new work
        _logger.error(
            "run_fact_reconciliation_failed",
            exception_type=type(exc).__name__,
        )


async def _record_run_error(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    claim: RunLeaseClaim,
    *,
    category: ErrorCategory,
    code: str,
    exc: BaseException,
) -> None:
    try:
        async with session_factory() as session:
            trace_id = await session.scalar(
                select(AgentRun.trace_id).where(AgentRun.id == claim.run_id)
            )
        await record_error(
            session_factory,
            ErrorOccurrenceCreate(
                category=category,
                code=code,
                service="run-worker",
                environment=settings.environment,
                release=settings.release,
                exception_type=type(exc).__name__,
                top_frame=top_application_frame(exc),
                org_id=claim.org_id,
                workspace_id=claim.workspace_id,
                run_id=claim.run_id,
                trace_id=trace_id,
            ),
        )
    except Exception as record_exc:  # noqa: BLE001 - terminal transition must continue
        _logger.error(
            "error_recording_failed",
            run_id=str(claim.run_id),
            exception_type=type(record_exc).__name__,
        )


async def _next_cancelled_run_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> UUID | None:
    async with session_factory() as session:
        return cast(
            UUID | None,
            await session.scalar(
                select(AgentRun.id)
                .where(
                    AgentRun.status.in_(("accepted", "queued", "running")),
                    AgentRun.cancel_requested_at.is_not(None),
                )
                .order_by(AgentRun.cancel_requested_at, AgentRun.id)
                .limit(1)
            ),
        )


async def _record_route(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    route: str,
) -> None:
    async with session_factory.begin() as session:
        await session.execute(
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.status == "running",
            )
            .values(route=route)
        )


async def _execute_started_run(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    identity: RunIdentity,
    lifecycle: RunLifecycle,
    bus: ReplyEventBus,
    attempt: int,
    stage_timer: RunStageTimer,
) -> RunOutcome:
    async with session_factory() as session:
        run = await session.get(AgentRun, identity.run_id)
        if run is None or run.status != "running":
            raise RuntimeError("run_authority_changed")
        user = await session.get(User, run.user_id)
        if user is None or not user.active or user.org_id != run.org_id:
            raise RuntimeError("run_principal_revoked")
        authorization = await resolve_authorization(session, user)
        context = TenantContext(
            user_id=user.id,
            org_id=user.org_id,
            authorization=authorization,
        )
        workspace = await tenancy_service.get_workspace(
            session,
            context,
            run.workspace_id,
            "chat.use",
        )
        chat = await chat_service.get_chat(session, context, run.chat_id)
        user_message = await session.scalar(
            select(Message).where(
                Message.id == run.input_message_id,
                Message.org_id == run.org_id,
                Message.workspace_id == run.workspace_id,
                Message.chat_id == run.chat_id,
                Message.role == chat_service.ROLE_USER,
            )
        )
        if user_message is None:
            raise RuntimeError("run_input_missing")
        model = await models_service.resolve_model(
            session,
            requested_model_id=run.model_id,
            default_model_id=workspace.default_model_id,
        )
        execution = await create_model_execution(
            session,
            model,
            settings,
            session_factory=session_factory,
            context=context,
            workspace_id=workspace.id,
            reasoning_effort=cast(ReasoningEffort, run.reasoning_effort),
        )
        bridge = DurableReplyBridge(
            lifecycle,
            bus,
            stage_observer=stage_timer,
            on_route=lambda route: _record_route(
                session_factory,
                identity.run_id,
                route,
            ),
        )
        return await bridge.consume(
            identity,
            chat_service.stream_reply(
                session,
                context,
                chat=chat,
                user_message=user_message,
                model=model,
                streamer=execution.streamer,
                retriever=retrieve,
                settings=settings,
                agent_gatherer_factory=execution.agent_gatherer_factory,
                retrieval_min_score=workspace.min_score,
                context_recorder=lambda snapshot, memories: record_run_context(
                    session_factory,
                    identity,
                    attempt=attempt,
                    snapshot=snapshot,
                    memories=memories,
                ),
            ),
        )


async def _execute_with_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    claim: RunLeaseClaim,
    lifecycle: RunLifecycle,
    bus: ReplyEventBus,
    stage_timer: RunStageTimer,
) -> RunOutcome | Literal["contested"]:
    identity = RunIdentity(
        run_id=claim.run_id,
        org_id=claim.org_id,
        workspace_id=claim.workspace_id,
        chat_id=claim.chat_id,
    )
    execution = asyncio.create_task(
        _execute_started_run(
            session_factory,
            settings,
            identity,
            lifecycle,
            bus,
            claim.attempt,
            stage_timer,
        )
    )
    heartbeat_seconds = max(5.0, settings.run_lease_seconds / 3)
    while True:
        done, _pending = await asyncio.wait(
            {execution},
            timeout=heartbeat_seconds,
        )
        if done:
            return await execution
        if not await renew_run_lease(
            session_factory,
            claim,
            lease_seconds=settings.run_lease_seconds,
        ):
            execution.cancel()
            with suppress(asyncio.CancelledError):
                await execution
            return "contested"


async def execute_queued_run_once(
    session_factory: async_sessionmaker[AsyncSession],
    bus: ReplyEventBus,
    settings: Settings,
    *,
    owner: str,
) -> RunnerTickResult:
    """A conditional lifecycle transition makes parallel worker ticks safe."""

    await _reconcile_terminal_fact(session_factory, settings)
    cancellation_lifecycle = RunLifecycle(
        SqlRunTransitionRepository(session_factory),
        bus,
    )
    cancelled_id = await _next_cancelled_run_id(session_factory)
    if cancelled_id is not None:
        acknowledged = await cancellation_lifecycle.acknowledge_cancel(cancelled_id)
        if acknowledged:
            await _record_terminal_fact(
                session_factory,
                settings,
                cancelled_id,
                RunObservation(),
            )
            return "cancelled"
        return "contested"

    exhausted = await fail_exhausted_run(session_factory)
    if exhausted is not None:
        await cancellation_lifecycle.announce_failure(
            RunIdentity(
                run_id=exhausted.run_id,
                org_id=exhausted.org_id,
                workspace_id=exhausted.workspace_id,
                chat_id=exhausted.chat_id,
            ),
            error_code="retry_exhausted",
        )
        await _record_terminal_fact(
            session_factory,
            settings,
            exhausted.run_id,
            RunObservation(),
        )
        return "failed"

    claim = await claim_next_run(
        session_factory,
        owner=owner,
        lease_seconds=settings.run_lease_seconds,
    )
    if claim is None:
        return "idle"
    run_id = claim.run_id
    lifecycle = RunLifecycle(
        SqlRunTransitionRepository(session_factory, lease_token=claim.token),
        bus,
    )
    stage_timer = RunStageTimer()
    try:
        await lifecycle.announce_start(
            RunIdentity(
                run_id=claim.run_id,
                org_id=claim.org_id,
                workspace_id=claim.workspace_id,
                chat_id=claim.chat_id,
            ),
            attempt=claim.attempt,
            recovered=claim.recovered,
        )
        outcome = await _execute_with_heartbeat(
            session_factory,
            settings,
            claim,
            lifecycle,
            bus,
            stage_timer,
        )
        if outcome != "contested":
            await _record_terminal_fact(
                session_factory,
                settings,
                run_id,
                stage_timer.snapshot(),
            )
        return outcome
    except ConflictError:
        transitioned = await lifecycle.fail(run_id, error_code="model_unavailable")
        if transitioned:
            await _record_terminal_fact(
                session_factory,
                settings,
                run_id,
                stage_timer.snapshot(),
            )
        return "failed"
    except UpstreamError as exc:
        await _record_run_error(
            session_factory,
            settings,
            claim,
            category="provider_transient",
            code="provider.transient",
            exc=exc,
        )
        transitioned = await lifecycle.fail(run_id, error_code="provider_transient")
        if transitioned:
            await _record_terminal_fact(
                session_factory,
                settings,
                run_id,
                stage_timer.snapshot(),
            )
        return "failed"
    except Exception as exc:
        error_id = uuid4()
        _logger.error(
            "durable_run_execution_failed",
            run_id=str(run_id),
            error_id=str(error_id),
            exception_type=type(exc).__name__,
        )
        await _record_run_error(
            session_factory,
            settings,
            claim,
            category="internal",
            code="run.internal",
            exc=exc,
        )
        transitioned = await lifecycle.fail(run_id, error_code="internal")
        if transitioned:
            await _record_terminal_fact(
                session_factory,
                settings,
                run_id,
                stage_timer.snapshot(),
            )
        return "failed"
