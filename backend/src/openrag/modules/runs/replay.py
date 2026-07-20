"""Redis-only SSE replay after tenant authorization has released SQL."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from openrag.modules.runs.events import RunEventEnvelope, encode_sse

_TERMINAL_EVENTS = frozenset(
    {"run.completed", "run.failed", "run.cancelled"}
)


@dataclass(frozen=True, slots=True)
class RunEventScope:
    run_id: UUID
    org_id: UUID
    workspace_id: UUID
    chat_id: UUID
    terminal: bool


class DisconnectRequest(Protocol):
    async def is_disconnected(self) -> bool: ...


class ReplayEventBus(Protocol):
    async def read(
        self,
        run_id: UUID,
        *,
        after_event_id: UUID | None = None,
        block_ms: int | None = None,
    ) -> list[RunEventEnvelope]: ...


def _authorized(event: RunEventEnvelope, scope: RunEventScope) -> bool:
    return (
        event.run_id == scope.run_id
        and event.org_id == scope.org_id
        and event.workspace_id == scope.workspace_id
        and event.chat_id == scope.chat_id
    )


async def stream_run_events(
    request: DisconnectRequest,
    bus: ReplayEventBus,
    scope: RunEventScope,
    *,
    initial_events: list[RunEventEnvelope],
    after_event_id: UUID | None,
    block_ms: int,
) -> AsyncIterator[str]:
    """Replay ordered events, heartbeat idlessly, and stop on terminal state."""

    cursor = after_event_id
    for event in initial_events:
        if not _authorized(event, scope):
            return
        yield encode_sse(event)
        cursor = event.event_id
        if event.event_type in _TERMINAL_EVENTS:
            return

    if scope.terminal and not initial_events:
        return

    while not await request.is_disconnected():
        events = await bus.read(
            scope.run_id,
            after_event_id=cursor,
            block_ms=block_ms,
        )
        if not events:
            # An SSE comment keeps proxies alive without changing Last-Event-ID.
            yield ": heartbeat\n\n"
            continue
        for event in events:
            if not _authorized(event, scope):
                return
            yield encode_sse(event)
            cursor = event.event_id
            if event.event_type in _TERMINAL_EVENTS:
                return
