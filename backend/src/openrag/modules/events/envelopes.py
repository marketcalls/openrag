"""Closed, content-free contracts for authoritative OpenRAG events."""

import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from openrag.modules.documents.lifecycle import DocumentVersionState

MAX_ENVELOPE_BYTES = 16 * 1024
LIFECYCLE_EVENT_TYPE = "document.version.lifecycle.v1"
INGESTION_REQUESTED_EVENT_TYPE = "document.version.ingestion_requested.v1"
REBUILD_REQUESTED_EVENT_TYPE = "document.version.rebuild_requested.v1"


class DocumentVersionLifecycleV1(BaseModel):
    """Content-free state change payload registered by Task 4A."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: UUID
    previous_state: DocumentVersionState
    new_state: DocumentVersionState


class DocumentVersionIngestionRequestedV1(BaseModel):
    """Content-free command that starts one numbered ingestion attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: UUID
    attempt: int = Field(gt=0)
    authority_generation_id: UUID


class DocumentVersionRebuildRequestedV1(BaseModel):
    """Content-free command that rebuilds legacy provenance and projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: UUID
    authority_generation_id: UUID


RegisteredPayload = (
    DocumentVersionLifecycleV1
    | DocumentVersionIngestionRequestedV1
    | DocumentVersionRebuildRequestedV1
)


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


class EventEnvelopeV1(BaseModel):
    """Stable base envelope whose canonical bytes are transport-attested."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    event_id: UUID
    event_type: Annotated[str, Field(min_length=1, max_length=200)]
    aggregate_type: Annotated[str, Field(min_length=1, max_length=120)]
    aggregate_id: UUID
    org_id: UUID
    workspace_id: UUID
    lifecycle_revision: int = Field(gt=0)
    correlation_id: UUID
    occurred_at: AwareDatetime
    payload: RegisteredPayload

    @field_validator("occurred_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class EventEnvelopeBase(BaseModel):
    """Schema-agnostic stable base used before payload-specific parsing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1, le=2_147_483_647)
    event_id: UUID
    event_type: Annotated[str, Field(min_length=1, max_length=200)]
    aggregate_type: Annotated[str, Field(min_length=1, max_length=120)]
    aggregate_id: UUID
    org_id: UUID
    workspace_id: UUID
    lifecycle_revision: int = Field(gt=0)
    correlation_id: UUID
    occurred_at: AwareDatetime
    payload: dict[str, object]

    @field_validator("occurred_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


def _registration(payload: object) -> tuple[str, str]:
    if type(payload) is DocumentVersionLifecycleV1:
        return LIFECYCLE_EVENT_TYPE, "document_version"
    if type(payload) is DocumentVersionIngestionRequestedV1:
        return INGESTION_REQUESTED_EVENT_TYPE, "document_version"
    if type(payload) is DocumentVersionRebuildRequestedV1:
        return REBUILD_REQUESTED_EVENT_TYPE, "document_version"
    raise ValueError("schema_not_registered")


def build_envelope(
    *,
    payload: RegisteredPayload,
    event_id: UUID,
    org_id: UUID,
    workspace_id: UUID,
    aggregate_id: UUID,
    lifecycle_revision: int,
    correlation_id: UUID,
    occurred_at: datetime,
) -> EventEnvelopeV1:
    event_type, aggregate_type = _registration(payload)
    return EventEnvelopeV1(
        event_id=event_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        org_id=org_id,
        workspace_id=workspace_id,
        lifecycle_revision=lifecycle_revision,
        correlation_id=correlation_id,
        occurred_at=occurred_at,
        payload=payload,
    )


def _canonical_model_bytes(envelope: BaseModel) -> bytes:
    encoded = json.dumps(
        envelope.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise ValueError("event envelope exceeds 16 KiB")
    return encoded


def canonical_envelope_bytes(envelope: EventEnvelopeV1) -> bytes:
    return _canonical_model_bytes(envelope)


def canonical_base_envelope_bytes(envelope: EventEnvelopeBase) -> bytes:
    return _canonical_model_bytes(envelope)


def _decode_envelope(encoded: bytes) -> dict[str, object]:
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise ValueError("event envelope exceeds 16 KiB")
    try:
        raw = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError("contract_invalid") from exc
    if not isinstance(raw, dict):
        raise ValueError("contract_invalid")
    return raw


def parse_base_envelope(encoded: bytes) -> EventEnvelopeBase:
    raw = _decode_envelope(encoded)
    try:
        envelope = EventEnvelopeBase.model_validate(raw)
    except ValidationError as exc:
        raise ValueError("contract_invalid") from exc
    if canonical_base_envelope_bytes(envelope) != encoded:
        raise ValueError("contract_invalid")
    return envelope


def parse_registered_envelope(encoded: bytes) -> EventEnvelopeV1:
    raw = _decode_envelope(encoded)
    if raw.get("event_type") not in {
        LIFECYCLE_EVENT_TYPE,
        INGESTION_REQUESTED_EVENT_TYPE,
        REBUILD_REQUESTED_EVENT_TYPE,
    }:
        raise ValueError("schema_not_registered")
    try:
        envelope = EventEnvelopeV1.model_validate(raw)
    except ValidationError as exc:
        raise ValueError("contract_invalid") from exc
    if _registration(envelope.payload)[0] != envelope.event_type:
        raise ValueError("contract_invalid")
    if canonical_envelope_bytes(envelope) != encoded:
        raise ValueError("contract_invalid")
    return envelope
