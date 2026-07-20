"""Deterministic 100-stream gate for the durable public event boundary."""

import asyncio
import os
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from openrag.modules.chat.events import SSEEvent
from openrag.modules.runs.events import RunEventEnvelope, RunEventType, new_run_event
from openrag.modules.runs.lifecycle import RunIdentity
from openrag.modules.runs.reply_bridge import DurableReplyBridge

pytestmark = [
    pytest.mark.load,
    pytest.mark.skipif(
        os.getenv("OPENRAG_RUN_LOAD_TESTS") != "1",
        reason="set OPENRAG_RUN_LOAD_TESTS=1 to run concurrency gates",
    ),
]

RUNS = 100
DELTAS = 50
CANCELLED_RUNS = 10
MAX_EVENTS_PER_RUN = 64


class BoundedMemoryBus:
    """Model Redis' per-run sequence, dedupe, and retention invariants."""

    def __init__(self) -> None:
        self.events: dict[UUID, list[RunEventEnvelope]] = defaultdict(list)
        self.by_event_id: dict[UUID, RunEventEnvelope] = {}
        self._lock = asyncio.Lock()

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
    ) -> RunEventEnvelope:
        async with self._lock:
            resolved_id = event_id or uuid4()
            existing = self.by_event_id.get(resolved_id)
            if existing is not None:
                return existing
            rows = self.events[run_id]
            event = new_run_event(
                sequence=len(rows) + 1,
                event_type=event_type,
                run_id=run_id,
                org_id=org_id,
                workspace_id=workspace_id,
                chat_id=chat_id,
                payload=payload,
                event_id=resolved_id,
            )
            if len(rows) >= MAX_EVENTS_PER_RUN:
                raise AssertionError("run event retention bound exceeded")
            rows.append(event)
            self.by_event_id[resolved_id] = event
            return event

    def read(
        self,
        run_id: UUID,
        after_event_id: UUID | None = None,
    ) -> list[RunEventEnvelope]:
        rows = self.events[run_id]
        if after_event_id is None:
            return list(rows)
        index = next(
            position
            for position, event in enumerate(rows)
            if event.event_id == after_event_id
        )
        return list(rows[index + 1 :])


@dataclass
class LoadLifecycle:
    cancel_requested: bool = False
    terminal: str | None = None

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        del run_id
        return self.cancel_requested

    async def first_token(self, run_id: UUID) -> bool:
        del run_id
        return True

    async def complete(
        self,
        run_id: UUID,
        *,
        assistant_message_id: UUID | None,
        usage: tuple[int, int],
    ) -> bool:
        del run_id, assistant_message_id, usage
        self.terminal = "completed"
        return True

    async def fail(self, run_id: UUID, *, error_code: str) -> bool:
        del run_id, error_code
        self.terminal = "failed"
        return True

    async def acknowledge_cancel(self, run_id: UUID) -> bool:
        del run_id
        self.terminal = "cancelled"
        return True


async def deterministic_reply(
    lifecycle: LoadLifecycle,
    *,
    cancel_midstream: bool,
) -> AsyncIterator[SSEEvent]:
    yield SSEEvent("route_selected", {"route": "direct", "reason_code": "load"})
    for index in range(DELTAS):
        if cancel_midstream and index == DELTAS // 2:
            lifecycle.cancel_requested = True
        yield SSEEvent("token", {"delta": f"{index:02d}"})
        await asyncio.sleep(0.02)
    yield SSEEvent(
        "done",
        {
            "message_id": str(uuid4()),
            "prompt_tokens": 10,
            "completion_tokens": DELTAS,
            "no_answer": False,
        },
    )


async def test_100_concurrent_streams_are_ordered_replayable_and_isolated() -> None:
    organization_id = uuid4()
    workspaces = [uuid4() for _ in range(10)]
    identities = [
        RunIdentity(
            run_id=uuid4(),
            org_id=organization_id,
            workspace_id=workspaces[index // 10],
            chat_id=uuid4(),
        )
        for index in range(RUNS)
    ]
    lifecycles = [LoadLifecycle() for _ in identities]
    bus = BoundedMemoryBus()

    outcomes = await asyncio.gather(
        *(
            DurableReplyBridge(lifecycle, bus).consume(
                identity,
                deterministic_reply(
                    lifecycle,
                    cancel_midstream=index < CANCELLED_RUNS,
                ),
            )
            for index, (identity, lifecycle) in enumerate(
                zip(identities, lifecycles, strict=True)
            )
        )
    )

    assert outcomes.count("completed") == RUNS - CANCELLED_RUNS
    assert outcomes.count("cancelled") == CANCELLED_RUNS
    assert "failed" not in outcomes

    global_event_ids: set[UUID] = set()
    for index, identity in enumerate(identities):
        rows = bus.read(identity.run_id)
        assert rows
        assert len(rows) <= MAX_EVENTS_PER_RUN
        assert [event.sequence for event in rows] == list(range(1, len(rows) + 1))
        assert len({event.event_id for event in rows}) == len(rows)
        assert not global_event_ids.intersection(event.event_id for event in rows)
        global_event_ids.update(event.event_id for event in rows)
        assert all(
            (
                event.run_id,
                event.org_id,
                event.workspace_id,
                event.chat_id,
            )
            == (
                identity.run_id,
                identity.org_id,
                identity.workspace_id,
                identity.chat_id,
            )
            for event in rows
        )
        assert all(
            foreign.run_id != event.run_id
            and foreign.workspace_id != event.workspace_id
            for event in rows
            for foreign in identities
            if foreign.workspace_id != identity.workspace_id
        )

        # Fifty clients reconnect once from a real logical cursor. Combining
        # the already-seen prefix and replay tail must reconstruct one stream.
        if index % 2 == 0:
            split = len(rows) // 2
            prefix = rows[: split + 1]
            replay = bus.read(identity.run_id, prefix[-1].event_id)
            assert prefix + replay == rows

    assert len(global_event_ids) == sum(len(rows) for rows in bus.events.values())
