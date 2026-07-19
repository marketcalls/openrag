from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from openrag.modules.documents.lifecycle import DocumentVersionState
from openrag.modules.events.envelopes import (
    MAX_ENVELOPE_BYTES,
    DocumentVersionLifecycleV1,
    EventEnvelopeBase,
    EventEnvelopeV1,
    build_envelope,
    canonical_envelope_bytes,
    parse_base_envelope,
    parse_registered_envelope,
)

EVENT_ID = UUID("10000000-0000-0000-0000-000000000001")
ORG_ID = UUID("20000000-0000-0000-0000-000000000002")
WORKSPACE_ID = UUID("30000000-0000-0000-0000-000000000003")
DOCUMENT_ID = UUID("40000000-0000-0000-0000-000000000004")
VERSION_ID = UUID("50000000-0000-0000-0000-000000000005")
CORRELATION_ID = UUID("60000000-0000-0000-0000-000000000006")


def lifecycle_payload() -> DocumentVersionLifecycleV1:
    return DocumentVersionLifecycleV1(
        document_id=DOCUMENT_ID,
        previous_state=DocumentVersionState.REVIEW,
        new_state=DocumentVersionState.APPROVED,
    )


def lifecycle_envelope() -> EventEnvelopeV1:
    return build_envelope(
        payload=lifecycle_payload(),
        event_id=EVENT_ID,
        org_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        aggregate_id=VERSION_ID,
        lifecycle_revision=2,
        correlation_id=CORRELATION_ID,
        occurred_at=datetime(2026, 7, 19, 12, 30, tzinfo=UTC),
    )


def test_registered_envelope_is_frozen_strict_and_utc() -> None:
    envelope = lifecycle_envelope()

    assert envelope.occurred_at.tzinfo is UTC
    with pytest.raises(ValidationError):
        EventEnvelopeV1.model_validate(
            {**envelope.model_dump(mode="json"), "storage_key": "private/object"}
        )
    with pytest.raises(ValidationError):
        DocumentVersionLifecycleV1.model_validate(
            {**lifecycle_payload().model_dump(mode="json"), "actor_id": str(EVENT_ID)}
        )
    with pytest.raises(ValidationError):
        envelope.lifecycle_revision = 3  # type: ignore[misc]


def test_envelope_normalizes_aware_timestamp_to_utc() -> None:
    envelope = build_envelope(
        payload=lifecycle_payload(),
        event_id=EVENT_ID,
        org_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        aggregate_id=VERSION_ID,
        lifecycle_revision=2,
        correlation_id=CORRELATION_ID,
        occurred_at=datetime(2026, 7, 19, 18, tzinfo=UTC) + timedelta(hours=5),
    )

    assert envelope.occurred_at.utcoffset() == timedelta(0)


def test_canonical_envelope_bytes_are_deterministic_and_bounded() -> None:
    envelope = lifecycle_envelope()

    first = canonical_envelope_bytes(envelope)
    second = canonical_envelope_bytes(
        EventEnvelopeV1.model_validate(envelope.model_dump(mode="json"))
    )

    assert first == second
    assert len(first) <= MAX_ENVELOPE_BYTES
    assert b" " not in first
    assert parse_registered_envelope(first) == envelope


@pytest.mark.parametrize(
    "forbidden",
    [
        "document_text",
        "prompt",
        "storage_path",
        "source_url",
        "content_hash",
        "credential",
        "raw_error",
        "stack_trace",
        "actor_id",
    ],
)
def test_envelope_rejects_prohibited_or_extra_fields(forbidden: str) -> None:
    encoded = lifecycle_envelope().model_dump(mode="json")
    encoded["payload"][forbidden] = "SENTINEL"

    with pytest.raises(ValueError):
        parse_registered_envelope(__import__("json").dumps(encoded, separators=(",", ":")).encode())


def test_envelope_rejects_naive_timestamp_and_unknown_schema() -> None:
    values = lifecycle_envelope().model_dump(mode="json")
    values["occurred_at"] = "2026-07-19T12:30:00"
    with pytest.raises(ValidationError):
        EventEnvelopeV1.model_validate(values)

    values = lifecycle_envelope().model_dump(mode="json")
    values["event_type"] = "document.version.future.v2"
    with pytest.raises(ValueError, match="schema_not_registered"):
        parse_registered_envelope(
            __import__("json").dumps(values, sort_keys=True, separators=(",", ":")).encode()
        )


def test_envelope_rejects_complete_canonical_size_above_16_kib() -> None:
    encoded = canonical_envelope_bytes(lifecycle_envelope())

    with pytest.raises(ValueError, match="16 KiB"):
        parse_registered_envelope(encoded + (b" " * (MAX_ENVELOPE_BYTES + 1)))


def test_envelope_rejects_duplicate_json_keys() -> None:
    encoded = canonical_envelope_bytes(lifecycle_envelope())
    duplicated = encoded.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1')

    with pytest.raises(ValueError, match="contract_invalid"):
        parse_registered_envelope(duplicated)


def test_base_envelope_accepts_attestable_future_schema() -> None:
    values = lifecycle_envelope().model_dump(mode="json")
    values["schema_version"] = 2
    values["event_type"] = "document.version.future.v2"
    encoded = __import__("json").dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    base = parse_base_envelope(encoded)

    assert isinstance(base, EventEnvelopeBase)
    assert base.schema_version == 2
    assert base.event_type == "document.version.future.v2"


@pytest.mark.parametrize(
    "encoded",
    [
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version": 1}',
        b"\xff",
    ],
)
def test_base_envelope_rejects_duplicate_noncanonical_or_invalid_utf8(
    encoded: bytes,
) -> None:
    with pytest.raises(ValueError, match="contract_invalid"):
        parse_base_envelope(encoded)
