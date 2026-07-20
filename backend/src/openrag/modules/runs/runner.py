"""Claim and execute one queued agent run through the existing Agno pipeline."""

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
from openrag.modules.orchestration.runtime import create_model_streamer
from openrag.modules.retrieval.service import retrieve
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


async def _next_run_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> UUID | None:
    async with session_factory() as session:
        return cast(
            UUID | None,
            await session.scalar(
                select(AgentRun.id)
                .where(
                    AgentRun.status.in_(("accepted", "queued")),
                    AgentRun.cancel_requested_at.is_(None),
                )
                .order_by(AgentRun.accepted_at, AgentRun.id)
                .limit(1)
            ),
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
        streamer = await create_model_streamer(session, model, settings)
        bridge = DurableReplyBridge(
            lifecycle,
            bus,
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
                streamer=streamer,
                retriever=retrieve,
                settings=settings,
            ),
        )


async def execute_queued_run_once(
    session_factory: async_sessionmaker[AsyncSession],
    bus: ReplyEventBus,
    settings: Settings,
) -> RunnerTickResult:
    """A conditional lifecycle transition makes parallel worker ticks safe."""

    lifecycle = RunLifecycle(SqlRunTransitionRepository(session_factory), bus)
    cancelled_id = await _next_cancelled_run_id(session_factory)
    if cancelled_id is not None:
        acknowledged = await lifecycle.acknowledge_cancel(cancelled_id)
        return "cancelled" if acknowledged else "contested"

    run_id = await _next_run_id(session_factory)
    if run_id is None:
        return "idle"
    try:
        if not await lifecycle.start(run_id):
            return "contested"
        async with session_factory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise RuntimeError("run_missing_after_claim")
            identity = RunIdentity(
                run_id=run.id,
                org_id=run.org_id,
                workspace_id=run.workspace_id,
                chat_id=run.chat_id,
            )
        return await _execute_started_run(
            session_factory,
            settings,
            identity,
            lifecycle,
            bus,
        )
    except ConflictError:
        await lifecycle.fail(run_id, error_code="model_unavailable")
        return "failed"
    except UpstreamError:
        await lifecycle.fail(run_id, error_code="provider_transient")
        return "failed"
    except Exception as exc:
        error_id = uuid4()
        _logger.error(
            "durable_run_execution_failed",
            run_id=str(run_id),
            error_id=str(error_id),
            exception_type=type(exc).__name__,
        )
        await lifecycle.fail(run_id, error_code="internal")
        return "failed"
