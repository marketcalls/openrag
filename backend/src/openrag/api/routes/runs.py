"""Authenticated command and status endpoints for durable agent runs."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.config import Settings, get_settings
from openrag.core.errors import ConflictError
from openrag.modules.events.bus import (
    RedisEventBus,
    RunEventCursorExpired,
    as_event_bus_redis,
)
from openrag.modules.runs import service
from openrag.modules.runs.models import AgentRun
from openrag.modules.runs.replay import RunEventScope, stream_run_events
from openrag.modules.runs.schemas import (
    RunAccepted,
    RunCreate,
    RunStatus,
    RunStatusOut,
)
from openrag.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    rate_limit_user,
)

router = APIRouter(tags=["runs"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ContextDep = Annotated[TenantContext, Depends(get_tenant_context)]
AcceptContextDep = Annotated[
    TenantContext,
    Depends(rate_limit_user("chat_send", 30, 60)),
]
RUN_SSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _status(run: AgentRun) -> RunStatusOut:
    return RunStatusOut(
        run_id=run.id,
        chat_id=run.chat_id,
        input_message_id=run.input_message_id,
        assistant_message_id=run.assistant_message_id,
        status=cast(RunStatus, run.status),
        route=run.route,
        error_code=run.error_code,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        accepted_at=run.accepted_at,
        started_at=run.started_at,
        first_token_at=run.first_token_at,
        cancel_requested_at=run.cancel_requested_at,
        finished_at=run.finished_at,
    )


@router.post(
    "/chats/{chat_id}/runs",
    status_code=202,
    response_model=RunAccepted,
)
async def accept_run(
    chat_id: UUID,
    body: RunCreate,
    session: SessionDep,
    context: AcceptContextDep,
) -> RunAccepted:
    accepted = await service.accept_run(session, context, chat_id, body)
    run = accepted.run
    return RunAccepted(
        run_id=run.id,
        input_message_id=run.input_message_id,
        status=cast(RunStatus, run.status),
        created=accepted.created,
        events_url=f"/api/v1/runs/{run.id}/events",
    )


@router.get("/runs/{run_id}", response_model=RunStatusOut)
async def get_run(
    run_id: UUID,
    session: SessionDep,
    context: ContextDep,
) -> RunStatusOut:
    return _status(await service.get_run(session, context, run_id))


@router.get("/runs/{run_id}/events")
async def replay_run_events(
    run_id: UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    context: ContextDep,
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID"),
    ] = None,
) -> StreamingResponse:
    run = await service.get_run(session, context, run_id)
    scope = RunEventScope(
        run_id=run.id,
        org_id=run.org_id,
        workspace_id=run.workspace_id,
        chat_id=run.chat_id,
        terminal=run.status in _TERMINAL_STATUSES,
    )
    # Release the connection before Redis blocking reads or browser waits.
    await session.rollback()

    cursor: UUID | None = None
    if last_event_id is not None:
        try:
            cursor = UUID(last_event_id)
        except ValueError as exc:
            raise ConflictError(
                "event cursor expired; reload run status and reconnect"
            ) from exc
    bus = RedisEventBus(
        as_event_bus_redis(request.app.state.event_redis),
        max_events=settings.run_event_max_events,
        retention_seconds=settings.run_event_retention_seconds,
    )
    try:
        initial = await bus.read(run_id, after_event_id=cursor)
    except RunEventCursorExpired as exc:
        raise ConflictError(
            "event cursor expired; reload run status and reconnect"
        ) from exc

    return StreamingResponse(
        stream_run_events(
            request,
            bus,
            scope,
            initial_events=initial,
            after_event_id=cursor,
            block_ms=settings.run_event_block_ms,
        ),
        media_type="text/event-stream",
        headers=RUN_SSE_HEADERS,
    )


@router.post(
    "/runs/{run_id}/cancel",
    status_code=202,
    response_model=RunStatusOut,
)
async def cancel_run(
    run_id: UUID,
    session: SessionDep,
    context: ContextDep,
) -> RunStatusOut:
    return _status(await service.request_cancel(session, context, run_id))
