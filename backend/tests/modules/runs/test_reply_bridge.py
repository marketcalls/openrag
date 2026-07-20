from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

from openrag.modules.chat.events import SSEEvent
from openrag.modules.runs.events import (
    RunEventEnvelope,
    RunEventType,
    new_run_event,
)
from openrag.modules.runs.lifecycle import RunIdentity
from openrag.modules.runs.reply_bridge import DurableReplyBridge


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

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
        values: dict[str, object] = {
            "event_type": event_type,
            "run_id": run_id,
            "org_id": org_id,
            "workspace_id": workspace_id,
            "chat_id": chat_id,
            "payload": payload,
            "event_id": event_id,
        }
        self.events.append(values)
        return new_run_event(
            sequence=len(self.events),
            event_type=event_type,
            run_id=run_id,
            org_id=org_id,
            workspace_id=workspace_id,
            chat_id=chat_id,
            payload=payload,
            event_id=event_id,
        )


@dataclass
class FakeLifecycle:
    cancelled: bool = False
    first_tokens: int = 0
    completed: tuple[UUID | None, tuple[int, int]] | None = None
    failed: str | None = None
    acknowledged: int = 0

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        del run_id
        return self.cancelled

    async def first_token(self, run_id: UUID) -> bool:
        del run_id
        self.first_tokens += 1
        return True

    async def complete(
        self,
        run_id: UUID,
        *,
        assistant_message_id: UUID | None,
        usage: tuple[int, int],
    ) -> bool:
        del run_id
        self.completed = (assistant_message_id, usage)
        return True

    async def fail(self, run_id: UUID, *, error_code: str) -> bool:
        del run_id
        self.failed = error_code
        return True

    async def acknowledge_cancel(self, run_id: UUID) -> bool:
        del run_id
        self.acknowledged += 1
        return True


def _identity() -> RunIdentity:
    return RunIdentity(
        run_id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        chat_id=uuid4(),
    )


async def _events(*events: SSEEvent) -> AsyncIterator[SSEEvent]:
    for event in events:
        yield event


async def test_bridge_streams_safe_durable_events_and_completes() -> None:
    identity = _identity()
    lifecycle = FakeLifecycle()
    bus = RecordingBus()
    message_id = uuid4()
    bridge = DurableReplyBridge(lifecycle, bus)

    outcome = await bridge.consume(
        identity,
        _events(
            SSEEvent(
                "route_selected",
                {"route": "direct", "reason_code": "greeting"},
            ),
            SSEEvent("token", {"delta": "Hello"}),
            SSEEvent("token", {"delta": "!"}),
            SSEEvent(
                "done",
                {
                    "message_id": str(message_id),
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "no_answer": False,
                },
            ),
        ),
    )

    assert outcome == "completed"
    assert lifecycle.first_tokens == 1
    assert lifecycle.completed == (message_id, (4, 2))
    assert [event["event_type"] for event in bus.events] == [
        "route.selected",
        "message.delta",
        "message.delta",
        "message.completed",
        "usage.updated",
    ]
    assert [event["payload"] for event in bus.events if event["event_type"] == "message.delta"] == [
        {"delta": "Hello"},
        {"delta": "!"},
    ]


async def test_bridge_strips_source_snippets_and_unknown_fields() -> None:
    identity = _identity()
    lifecycle = FakeLifecycle()
    bus = RecordingBus()
    bridge = DurableReplyBridge(lifecycle, bus)

    await bridge.consume(
        identity,
        _events(
            SSEEvent(
                "sources",
                {
                    "sources": [
                        {
                            "marker": 1,
                            "document_id": str(uuid4()),
                            "filename": "policy.pdf",
                            "version_label": "3",
                            "section_label": "Safety",
                            "page": 7,
                            "score": 0.91,
                            "snippet": "confidential document text",
                            "api_key": "must-not-escape",
                        }
                    ]
                },
            ),
            SSEEvent("error", {"detail": "raw provider secret"}),
        ),
    )

    assert lifecycle.failed == "provider_rejected"
    payload = bus.events[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["sources"] == [
        {
            "marker": 1,
            "document_id": payload["sources"][0]["document_id"],
            "filename": "policy.pdf",
            "version_label": "3",
            "section_label": "Safety",
            "page": 7,
            "score": 0.91,
        }
    ]
    assert "confidential" not in str(payload)
    assert "secret" not in str(payload)


async def test_bridge_honors_cooperative_cancellation_before_delta() -> None:
    identity = _identity()
    lifecycle = FakeLifecycle(cancelled=True)
    bus = RecordingBus()
    bridge = DurableReplyBridge(lifecycle, bus)

    outcome = await bridge.consume(
        identity,
        _events(SSEEvent("token", {"delta": "must not escape"})),
    )

    assert outcome == "cancelled"
    assert lifecycle.acknowledged == 1
    assert bus.events == []
