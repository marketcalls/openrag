import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

RunEventType = Literal[
    "run.accepted",
    "run.started",
    "run.completed",
    "run.failed",
    "run.cancel.requested",
    "run.cancelled",
    "route.selected",
    "retrieval.started",
    "retrieval.sources",
    "retrieval.completed",
    "agent.started",
    "agent.progress",
    "agent.completed",
    "tool.started",
    "tool.progress",
    "tool.completed",
    "tool.failed",
    "message.started",
    "message.delta",
    "message.completed",
    "ui.block.upsert",
    "ui.committed",
    "artifact.created",
    "artifact.versioned",
    "usage.updated",
    "approval.requested",
    "clarification.requested",
    "heartbeat",
]

_MAX_PAYLOAD_BYTES = 65_536


class RunEventEnvelope(BaseModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    sequence: int = Field(gt=0)
    event_type: RunEventType
    run_id: UUID
    org_id: UUID
    workspace_id: UUID
    chat_id: UUID
    occurred_at: AwareDatetime
    payload: dict[str, Any]

    model_config = ConfigDict(frozen=True)


def new_run_event(
    *,
    sequence: int,
    event_type: RunEventType,
    run_id: UUID,
    org_id: UUID,
    workspace_id: UUID,
    chat_id: UUID,
    payload: dict[str, Any],
    event_id: UUID | None = None,
    occurred_at: datetime | None = None,
) -> RunEventEnvelope:
    encoded_payload = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded_payload) > _MAX_PAYLOAD_BYTES:
        raise ValueError("event payload exceeds 65536 bytes")

    return RunEventEnvelope(
        event_id=event_id or uuid4(),
        sequence=sequence,
        event_type=event_type,
        run_id=run_id,
        org_id=org_id,
        workspace_id=workspace_id,
        chat_id=chat_id,
        occurred_at=occurred_at or datetime.now(UTC),
        payload=payload,
    )


def encode_sse(event: RunEventEnvelope) -> str:
    data = json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {data}\n\n"
