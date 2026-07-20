from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from openrag.modules.runs.events import RunEventEnvelope, new_run_event
from openrag.modules.runs.replay import RunEventScope, stream_run_events


class FakeRequest:
    def __init__(self, disconnect_after: int = 100) -> None:
        self.calls = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls > self.disconnect_after


class FakeBus:
    def __init__(self, batches: list[list[RunEventEnvelope]]) -> None:
        self.batches = list(batches)
        self.cursors: list[UUID | None] = []

    async def read(
        self,
        run_id: UUID,
        *,
        after_event_id: UUID | None = None,
        block_ms: int | None = None,
    ) -> list[RunEventEnvelope]:
        del run_id, block_ms
        self.cursors.append(after_event_id)
        return self.batches.pop(0) if self.batches else []


def _scope() -> RunEventScope:
    return RunEventScope(
        run_id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        chat_id=uuid4(),
        terminal=False,
    )


def _event(
    scope: RunEventScope,
    sequence: int,
    event_type: str,
) -> RunEventEnvelope:
    return new_run_event(  # type: ignore[arg-type]
        sequence=sequence,
        event_type=event_type,
        run_id=scope.run_id,
        org_id=scope.org_id,
        workspace_id=scope.workspace_id,
        chat_id=scope.chat_id,
        payload={},
    )


async def _collect(stream: AsyncIterator[str]) -> list[str]:
    return [chunk async for chunk in stream]


async def test_replay_emits_initial_events_then_closes_on_terminal() -> None:
    scope = _scope()
    started = _event(scope, 1, "run.started")
    completed = _event(scope, 2, "run.completed")
    bus = FakeBus([[completed]])

    chunks = await _collect(
        stream_run_events(
            FakeRequest(),
            bus,
            scope,
            initial_events=[started],
            after_event_id=None,
            block_ms=10,
        )
    )

    assert "event: run.started" in chunks[0]
    assert "event: run.completed" in chunks[1]
    assert bus.cursors == [started.event_id]


async def test_replay_emits_idless_heartbeat_and_rechecks_disconnect() -> None:
    scope = _scope()
    chunks = await _collect(
        stream_run_events(
            FakeRequest(disconnect_after=1),
            FakeBus([[]]),
            scope,
            initial_events=[],
            after_event_id=None,
            block_ms=10,
        )
    )

    assert chunks == [": heartbeat\n\n"]
    assert "id:" not in chunks[0]


async def test_replay_drops_cross_scope_event_without_disclosure() -> None:
    scope = _scope()
    foreign = new_run_event(
        sequence=1,
        event_type="run.started",
        run_id=scope.run_id,
        org_id=uuid4(),
        workspace_id=scope.workspace_id,
        chat_id=scope.chat_id,
        payload={},
    )

    chunks = await _collect(
        stream_run_events(
            FakeRequest(),
            FakeBus([]),
            scope,
            initial_events=[foreign],
            after_event_id=None,
            block_ms=10,
        )
    )

    assert chunks == []


async def test_terminal_persisted_run_with_no_events_closes_immediately() -> None:
    base = _scope()
    terminal_scope = RunEventScope(
        run_id=base.run_id,
        org_id=base.org_id,
        workspace_id=base.workspace_id,
        chat_id=base.chat_id,
        terminal=True,
    )

    chunks = await _collect(
        stream_run_events(
            FakeRequest(),
            FakeBus([]),
            terminal_scope,
            initial_events=[],
            after_event_id=None,
            block_ms=10,
        )
    )

    assert chunks == []
