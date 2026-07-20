"""Safe HTTP contracts for durable agent runs."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

RunStatus = Literal[
    "accepted",
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
]


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=32_000)
    client_request_id: UUID
    parent_message_id: UUID | None = None
    model_id: UUID | None = None


class RunRegenerate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    model_id: UUID | None = None


class RunAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    input_message_id: UUID
    status: RunStatus
    created: bool
    events_url: str


class RunStatusOut(BaseModel):
    """Operational state only: no prompts, credentials, or internal traces."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    chat_id: UUID
    input_message_id: UUID
    assistant_message_id: UUID | None
    status: RunStatus
    route: str | None
    error_code: str | None
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    accepted_at: datetime
    started_at: datetime | None
    first_token_at: datetime | None
    cancel_requested_at: datetime | None
    finished_at: datetime | None
