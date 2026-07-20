from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from openrag.modules.documents.lifecycle import DocumentVersionState
from openrag.modules.events.envelopes import (
    DocumentVersionIngestionRequestedV1,
    DocumentVersionLifecycleV1,
    DocumentVersionRebuildRequestedV1,
    DocumentVersionReindexRequestedV1,
    RunCancelRequestedV1,
    RunRequestedV1,
)
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


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            DocumentVersionIngestionRequestedV1(
                document_id=UUID("10000000-0000-0000-0000-000000000001"),
                attempt=3,
                authority_generation_id=UUID(
                    "60000000-0000-0000-0000-000000000006"
                ),
            ),
            "document-version:40000000-0000-0000-0000-000000000004:ingestion:3",
        ),
        (
            DocumentVersionRebuildRequestedV1(
                document_id=UUID("10000000-0000-0000-0000-000000000001"),
                authority_generation_id=UUID(
                    "60000000-0000-0000-0000-000000000006"
                ),
            ),
            "document-version:40000000-0000-0000-0000-000000000004:rebuild:1",
        ),
        (
            DocumentVersionReindexRequestedV1(
                document_id=UUID("10000000-0000-0000-0000-000000000001"),
                deployment_id=UUID("70000000-0000-0000-0000-000000000007"),
                embedding_profile_version=f"embedding/v1/{'a' * 64}",
                authority_generation_id=UUID(
                    "60000000-0000-0000-0000-000000000006"
                ),
            ),
            "document-version:40000000-0000-0000-0000-000000000004:"
            "reindex:70000000-0000-0000-0000-000000000007",
        ),
    ],
)
def test_registered_factory_derives_document_start_dedupe_keys(
    payload: (
        DocumentVersionIngestionRequestedV1
        | DocumentVersionRebuildRequestedV1
        | DocumentVersionReindexRequestedV1
    ),
    expected: str,
) -> None:
    session = RecordingSession()
    values = event_values()
    values["payload"] = payload

    event = add_registered_event(session, **values)  # type: ignore[arg-type]

    assert event.dedupe_key == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            RunRequestedV1(
                run_id=UUID("40000000-0000-0000-0000-000000000004"),
                user_id=UUID("10000000-0000-0000-0000-000000000001"),
                chat_id=UUID("20000000-0000-0000-0000-000000000002"),
                input_message_id=UUID(
                    "30000000-0000-0000-0000-000000000003"
                ),
                client_request_id=UUID(
                    "50000000-0000-0000-0000-000000000005"
                ),
                model_id=None,
            ),
            "agent-run:40000000-0000-0000-0000-000000000004:requested",
        ),
        (
            RunCancelRequestedV1(
                run_id=UUID("40000000-0000-0000-0000-000000000004"),
                user_id=UUID("10000000-0000-0000-0000-000000000001"),
            ),
            "agent-run:40000000-0000-0000-0000-000000000004:cancel-requested",
        ),
    ],
)
def test_registered_factory_derives_agent_run_dedupe_keys(
    payload: RunRequestedV1 | RunCancelRequestedV1,
    expected: str,
) -> None:
    session = RecordingSession()
    values = event_values()
    values["payload"] = payload

    event = add_registered_event(session, **values)  # type: ignore[arg-type]

    assert event.dedupe_key == expected
