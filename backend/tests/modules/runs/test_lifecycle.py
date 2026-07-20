import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

from openrag.modules.runs.lifecycle import RunIdentity, RunLifecycle


@dataclass
class FakeState:
    identity: RunIdentity
    status: str = "accepted"
    cancel_requested: bool = False
    first_token_seen: bool = False


class FakeRepository:
    def __init__(self, state: FakeState) -> None:
        self.state = state
        self.lock = asyncio.Lock()

    async def start(self, run_id: UUID) -> RunIdentity | None:
        async with self.lock:
            if (
                run_id != self.state.identity.run_id
                or self.state.status not in {"accepted", "queued"}
                or self.state.cancel_requested
            ):
                return None
            self.state.status = "running"
            return self.state.identity

    async def first_token(self, run_id: UUID) -> RunIdentity | None:
        async with self.lock:
            if (
                run_id != self.state.identity.run_id
                or self.state.status != "running"
                or self.state.first_token_seen
            ):
                return None
            self.state.first_token_seen = True
            return self.state.identity

    async def complete(
        self,
        run_id: UUID,
        *,
        assistant_message_id: UUID | None,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> RunIdentity | None:
        del assistant_message_id, prompt_tokens, completion_tokens
        async with self.lock:
            if (
                run_id != self.state.identity.run_id
                or self.state.status != "running"
                or self.state.cancel_requested
            ):
                return None
            self.state.status = "completed"
            return self.state.identity

    async def fail(
        self,
        run_id: UUID,
        *,
        error_code: str,
    ) -> RunIdentity | None:
        del error_code
        async with self.lock:
            if (
                run_id != self.state.identity.run_id
                or self.state.status not in {"accepted", "queued", "running"}
                or self.state.cancel_requested
            ):
                return None
            self.state.status = "failed"
            return self.state.identity

    async def request_cancel(self, run_id: UUID) -> RunIdentity | None:
        async with self.lock:
            if (
                run_id != self.state.identity.run_id
                or self.state.status in {"completed", "failed", "cancelled"}
                or self.state.cancel_requested
            ):
                return None
            self.state.cancel_requested = True
            return self.state.identity

    async def acknowledge_cancel(self, run_id: UUID) -> RunIdentity | None:
        async with self.lock:
            if (
                run_id != self.state.identity.run_id
                or self.state.status in {"completed", "failed", "cancelled"}
                or not self.state.cancel_requested
            ):
                return None
            self.state.status = "cancelled"
            return self.state.identity

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        return run_id == self.state.identity.run_id and self.state.cancel_requested


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def append(self, **values: object) -> object:
        self.events.append(values)
        return values


def _lifecycle() -> tuple[RunLifecycle, FakeState, RecordingBus]:
    identity = RunIdentity(
        run_id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        chat_id=uuid4(),
    )
    state = FakeState(identity=identity)
    bus = RecordingBus()
    return RunLifecycle(FakeRepository(state), bus), state, bus


async def test_only_one_terminal_transition_wins() -> None:
    lifecycle, state, bus = _lifecycle()
    assert await lifecycle.start(state.identity.run_id) is True

    results = await asyncio.gather(
        lifecycle.complete(
            state.identity.run_id,
            assistant_message_id=None,
            usage=(4, 2),
        ),
        lifecycle.fail(state.identity.run_id, error_code="internal"),
    )

    assert sorted(results) == [False, True]
    assert state.status in {"completed", "failed"}
    terminal = [
        event for event in bus.events if event["event_type"] in {"run.completed", "run.failed"}
    ]
    assert len(terminal) == 1


async def test_cancel_is_idempotent_and_terminal() -> None:
    lifecycle, state, bus = _lifecycle()

    assert await lifecycle.request_cancel(state.identity.run_id) is True
    assert await lifecycle.request_cancel(state.identity.run_id) is False
    assert await lifecycle.acknowledge_cancel(state.identity.run_id) is True
    assert await lifecycle.acknowledge_cancel(state.identity.run_id) is False

    assert state.status == "cancelled"
    assert [event["event_type"] for event in bus.events] == [
        "run.cancel.requested",
        "run.cancelled",
    ]


async def test_start_refuses_pre_cancelled_run_without_emitting_started() -> None:
    lifecycle, state, bus = _lifecycle()
    assert await lifecycle.request_cancel(state.identity.run_id) is True

    assert await lifecycle.start(state.identity.run_id) is False
    assert await lifecycle.acknowledge_cancel(state.identity.run_id) is True

    assert all(event["event_type"] != "run.started" for event in bus.events)


async def test_first_token_and_terminal_events_use_deterministic_ids() -> None:
    lifecycle, state, bus = _lifecycle()
    assert await lifecycle.start(state.identity.run_id) is True
    assert await lifecycle.first_token(state.identity.run_id) is True
    assert await lifecycle.first_token(state.identity.run_id) is False
    assert await lifecycle.complete(
        state.identity.run_id,
        assistant_message_id=None,
        usage=(1, 1),
    )

    event_ids = [event["event_id"] for event in bus.events]
    assert len(event_ids) == len(set(event_ids))
    assert all(isinstance(event_id, UUID) for event_id in event_ids)


async def test_unknown_error_code_is_rejected_before_repository_mutation() -> None:
    lifecycle, state, bus = _lifecycle()

    try:
        await lifecycle.fail(state.identity.run_id, error_code="raw traceback")
    except ValueError as exc:
        assert str(exc) == "run_error_code_invalid"
    else:
        raise AssertionError("unsafe error code was accepted")

    assert state.status == "accepted"
    assert bus.events == []


async def test_reconciler_can_announce_a_safe_terminal_failure() -> None:
    lifecycle, state, bus = _lifecycle()

    await lifecycle.announce_failure(
        state.identity,
        error_code="retry_exhausted",
    )

    assert bus.events[0]["event_type"] == "run.failed"
    assert bus.events[0]["payload"] == {"error_code": "retry_exhausted"}
