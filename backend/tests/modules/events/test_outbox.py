from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from openrag.modules.documents.lifecycle import DocumentVersionState
from openrag.modules.events.envelopes import DocumentVersionLifecycleV1
from openrag.modules.events.outbox import add_registered_event


class RecordingSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.added.append(value)


def event_values() -> dict[str, object]:
    return {
        "payload": DocumentVersionLifecycleV1(
            document_id=UUID("10000000-0000-0000-0000-000000000001"),
            previous_state=DocumentVersionState.REVIEW,
            new_state=DocumentVersionState.APPROVED,
        ),
        "org_id": UUID("20000000-0000-0000-0000-000000000002"),
        "workspace_id": UUID("30000000-0000-0000-0000-000000000003"),
        "aggregate_id": UUID("40000000-0000-0000-0000-000000000004"),
        "lifecycle_revision": 2,
        "correlation_id": UUID("50000000-0000-0000-0000-000000000005"),
        "occurred_at": datetime(2026, 7, 19, 12, tzinfo=UTC),
    }


def test_registered_factory_validates_before_session_add() -> None:
    session = RecordingSession()

    event = add_registered_event(session, **event_values())  # type: ignore[arg-type]

    assert session.added == [event]
    assert event.envelope_digest is not None
    assert len(event.envelope_digest) == 64
    assert event.event_type == "document.version.lifecycle.v1"
    assert event.aggregate_type == "document_version"
    assert event.payload["payload"] == {
        "document_id": "10000000-0000-0000-0000-000000000001",
        "previous_state": "review",
        "new_state": "approved",
    }
    assert "actor_id" not in repr(event.payload)


def test_registered_factory_rejects_unregistered_payload_before_add() -> None:
    session = RecordingSession()
    values = event_values()
    values["payload"] = SimpleNamespace(document_text="SENTINEL")

    with pytest.raises(ValueError, match="schema_not_registered"):
        add_registered_event(session, **values)  # type: ignore[arg-type]

    assert session.added == []


def test_registered_factory_rejects_oversized_envelope_before_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = RecordingSession()
    monkeypatch.setattr("openrag.modules.events.envelopes.MAX_ENVELOPE_BYTES", 1)

    with pytest.raises(ValueError, match="16 KiB"):
        add_registered_event(session, **event_values())  # type: ignore[arg-type]

    assert session.added == []
