"""The sole validated producer boundary for transactional Outbox rows."""

import hashlib
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import func

from openrag.modules.events.envelopes import (
    DocumentVersionIngestionRequestedV1,
    DocumentVersionRebuildRequestedV1,
    DocumentVersionReindexRequestedV1,
    RegisteredPayload,
    RunCancelRequestedV1,
    RunRequestedV1,
    build_envelope,
    canonical_envelope_bytes,
)
from openrag.modules.events.models import OutboxEvent


class AddSession(Protocol):
    def add(self, instance: object) -> None: ...


def _dedupe_key(
    payload: RegisteredPayload,
    *,
    aggregate_id: UUID,
    lifecycle_revision: int,
) -> str:
    if type(payload) is DocumentVersionIngestionRequestedV1:
        return f"document-version:{aggregate_id}:ingestion:{payload.attempt}"
    if type(payload) is DocumentVersionRebuildRequestedV1:
        return f"document-version:{aggregate_id}:rebuild:1"
    if type(payload) is DocumentVersionReindexRequestedV1:
        return (
            f"document-version:{aggregate_id}:reindex:{payload.deployment_id}"
        )
    if type(payload) is RunRequestedV1:
        return f"agent-run:{aggregate_id}:requested"
    if type(payload) is RunCancelRequestedV1:
        return f"agent-run:{aggregate_id}:cancel-requested"
    return f"document-version:{aggregate_id}:{lifecycle_revision}"


def add_registered_event(
    session: AddSession,
    *,
    payload: RegisteredPayload,
    org_id: UUID,
    workspace_id: UUID,
    aggregate_id: UUID,
    lifecycle_revision: int,
    occurred_at: datetime,
    correlation_id: UUID | None = None,
    event_id: UUID | None = None,
) -> OutboxEvent:
    """Validate/canonicalize an event completely before touching the session."""

    resolved_event_id = event_id or uuid4()
    envelope = build_envelope(
        payload=payload,
        event_id=resolved_event_id,
        org_id=org_id,
        workspace_id=workspace_id,
        aggregate_id=aggregate_id,
        lifecycle_revision=lifecycle_revision,
        correlation_id=correlation_id or uuid4(),
        occurred_at=occurred_at,
    )
    envelope_bytes = canonical_envelope_bytes(envelope)
    row = OutboxEvent(
        event_id=resolved_event_id,
        aggregate_type=envelope.aggregate_type,
        aggregate_id=envelope.aggregate_id,
        event_type=envelope.event_type,
        payload=envelope.model_dump(mode="json"),
        dedupe_key=_dedupe_key(
            payload,
            aggregate_id=aggregate_id,
            lifecycle_revision=lifecycle_revision,
        ),
        envelope_digest=hashlib.sha256(envelope_bytes).hexdigest(),
        # Eligibility is initialized by PostgreSQL, avoiding host/container
        # clock skew at the relay boundary.
        dispatch_after=func.timezone("UTC", func.now()),
    )
    session.add(row)
    return row
