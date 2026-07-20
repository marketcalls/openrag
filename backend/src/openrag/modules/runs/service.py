"""Tenant-safe, transactional command boundary for durable agent runs."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.db import naive_utc
from openrag.core.errors import ConflictError, NotFoundError
from openrag.core.telemetry import current_trace_id
from openrag.modules.chat import service as chat_service
from openrag.modules.chat.models import Chat, Message
from openrag.modules.events.envelopes import (
    RunCancelRequestedV1,
    RunRequestedV1,
)
from openrag.modules.events.outbox import add_registered_event
from openrag.modules.models import service as models_service
from openrag.modules.runs.models import AgentRun
from openrag.modules.runs.schemas import RunCreate, RunRegenerate
from openrag.modules.tenancy import service as tenancy_service
from openrag.modules.tenancy.context import TenantContext

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class AcceptedRun:
    run: AgentRun
    created: bool


async def _existing_request(
    session: AsyncSession,
    context: TenantContext,
    client_request_id: UUID,
) -> AgentRun | None:
    return (
        await session.execute(
            select(AgentRun).where(
                AgentRun.org_id == context.org_id,
                AgentRun.user_id == context.user_id,
                AgentRun.client_request_id == client_request_id,
            )
        )
    ).scalar_one_or_none()


def _same_chat(existing: AgentRun, chat_id: UUID) -> AcceptedRun:
    if existing.chat_id != chat_id:
        raise ConflictError("client request id already used")
    return AcceptedRun(run=existing, created=False)


async def accept_run(
    session: AsyncSession,
    context: TenantContext,
    chat_id: UUID,
    command: RunCreate,
) -> AcceptedRun:
    """Persist the input, run, and dispatch command in one commit."""

    existing = await _existing_request(
        session,
        context,
        command.client_request_id,
    )
    if existing is not None:
        await tenancy_service.get_workspace(
            session,
            context,
            existing.workspace_id,
            "chat.use",
        )
        return _same_chat(existing, chat_id)

    chat = (
        await session.execute(
            select(Chat)
            .where(
                Chat.id == chat_id,
                Chat.org_id == context.org_id,
                Chat.user_id == context.user_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if chat is None:
        raise NotFoundError("chat not found")

    workspace = await tenancy_service.get_workspace(
        session,
        context,
        chat.workspace_id,
        "chat.use",
    )
    resolved_model_id = command.model_id
    if resolved_model_id is not None:
        model = await models_service.resolve_model(
            session,
            requested_model_id=resolved_model_id,
            default_model_id=workspace.default_model_id,
        )
        resolved_model_id = model.id

    messages = await chat_service.list_messages(session, chat.id)
    if not messages and chat.title == "New chat":
        chat.title = chat_service.derive_chat_title(command.content)
    parent = chat_service.resolve_parent(
        messages,
        command.parent_message_id,
        explicit="parent_message_id" in command.model_fields_set,
    )
    user_message = await chat_service.build_message(
        session,
        context,
        chat,
        role=chat_service.ROLE_USER,
        content=command.content,
        parent=parent,
    )
    await session.flush()

    run = AgentRun(
        org_id=context.org_id,
        workspace_id=chat.workspace_id,
        user_id=context.user_id,
        chat_id=chat.id,
        input_message_id=user_message.id,
        model_id=resolved_model_id,
        client_request_id=command.client_request_id,
        trace_id=current_trace_id(),
    )
    session.add(run)
    await session.flush()
    add_registered_event(
        session,
        payload=RunRequestedV1(
            run_id=run.id,
            user_id=context.user_id,
            chat_id=chat.id,
            input_message_id=user_message.id,
            client_request_id=command.client_request_id,
            model_id=resolved_model_id,
        ),
        org_id=context.org_id,
        workspace_id=chat.workspace_id,
        aggregate_id=run.id,
        lifecycle_revision=1,
        correlation_id=command.client_request_id,
        occurred_at=datetime.now(UTC),
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raced = await _existing_request(
            session,
            context,
            command.client_request_id,
        )
        if raced is None:
            raise
        await tenancy_service.get_workspace(
            session,
            context,
            raced.workspace_id,
            "chat.use",
        )
        return _same_chat(raced, chat_id)
    return AcceptedRun(run=run, created=True)


async def accept_regeneration(
    session: AsyncSession,
    context: TenantContext,
    assistant_message_id: UUID,
    command: RunRegenerate,
) -> AcceptedRun:
    """Create a new run for an existing user turn without duplicating it."""

    chat, assistant = await chat_service.get_message(
        session,
        context,
        assistant_message_id,
    )
    if assistant.role != chat_service.ROLE_ASSISTANT or assistant.parent_message_id is None:
        raise ConflictError("only assistant messages can be regenerated")

    existing = await _existing_request(session, context, command.client_request_id)
    if existing is not None:
        await tenancy_service.get_workspace(
            session,
            context,
            existing.workspace_id,
            "chat.use",
        )
        if existing.chat_id != chat.id or existing.input_message_id != assistant.parent_message_id:
            raise ConflictError("client request id already used")
        return AcceptedRun(run=existing, created=False)

    workspace = await tenancy_service.get_workspace(
        session,
        context,
        chat.workspace_id,
        "chat.use",
    )
    user_message = await session.scalar(
        select(Message).where(
            Message.id == assistant.parent_message_id,
            Message.chat_id == chat.id,
            Message.org_id == context.org_id,
            Message.workspace_id == chat.workspace_id,
            Message.role == chat_service.ROLE_USER,
        )
    )
    if user_message is None:
        raise ConflictError("assistant parent is not a user message")
    chat_id = chat.id
    user_message_id = user_message.id

    resolved_model_id = command.model_id
    if resolved_model_id is not None:
        model = await models_service.resolve_model(
            session,
            requested_model_id=resolved_model_id,
            default_model_id=workspace.default_model_id,
        )
        resolved_model_id = model.id
    run = AgentRun(
        org_id=context.org_id,
        workspace_id=chat.workspace_id,
        user_id=context.user_id,
        chat_id=chat_id,
        input_message_id=user_message_id,
        model_id=resolved_model_id,
        client_request_id=command.client_request_id,
        trace_id=current_trace_id(),
    )
    session.add(run)
    await session.flush()
    add_registered_event(
        session,
        payload=RunRequestedV1(
            run_id=run.id,
            user_id=context.user_id,
            chat_id=chat_id,
            input_message_id=user_message_id,
            client_request_id=command.client_request_id,
            model_id=resolved_model_id,
        ),
        org_id=context.org_id,
        workspace_id=chat.workspace_id,
        aggregate_id=run.id,
        lifecycle_revision=1,
        correlation_id=command.client_request_id,
        occurred_at=datetime.now(UTC),
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raced = await _existing_request(session, context, command.client_request_id)
        if raced is None or raced.chat_id != chat_id or raced.input_message_id != user_message_id:
            raise
        return AcceptedRun(run=raced, created=False)
    return AcceptedRun(run=run, created=True)


async def get_run(
    session: AsyncSession,
    context: TenantContext,
    run_id: UUID,
    *,
    lock: bool = False,
) -> AgentRun:
    statement = select(AgentRun).where(
        AgentRun.id == run_id,
        AgentRun.org_id == context.org_id,
        AgentRun.user_id == context.user_id,
    )
    if lock:
        statement = statement.with_for_update()
    run = (await session.execute(statement)).scalar_one_or_none()
    if run is None:
        raise NotFoundError("run not found")
    await tenancy_service.get_workspace(
        session,
        context,
        run.workspace_id,
        "chat.use",
    )
    return run


async def request_cancel(
    session: AsyncSession,
    context: TenantContext,
    run_id: UUID,
) -> AgentRun:
    run = await get_run(session, context, run_id, lock=True)
    if run.status in _TERMINAL_STATUSES or run.cancel_requested_at is not None:
        return run

    run.cancel_requested_at = naive_utc()
    add_registered_event(
        session,
        payload=RunCancelRequestedV1(
            run_id=run.id,
            user_id=context.user_id,
        ),
        org_id=context.org_id,
        workspace_id=run.workspace_id,
        aggregate_id=run.id,
        lifecycle_revision=1,
        correlation_id=run.client_request_id,
        occurred_at=datetime.now(UTC),
    )
    await session.commit()
    return run
