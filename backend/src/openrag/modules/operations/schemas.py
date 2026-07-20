"""Strict, content-free contracts for RAG operations and error facts."""

from datetime import datetime, timedelta
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

RagRoute = Literal["direct", "conversation", "rag", "analytics", "clarify", "unknown"]
RagRunOutcome = Literal["grounded", "conversational", "no_answer", "failed", "cancelled"]
ErrorCategory = Literal[
    "validation",
    "policy",
    "authentication",
    "authorization",
    "rate_limit",
    "admission_overload",
    "provider_transient",
    "provider_permanent",
    "retrieval",
    "embedding",
    "reranking",
    "ingestion",
    "ocr",
    "storage",
    "tool",
    "cancellation",
    "persistence",
    "broker",
    "internal",
]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]

_CODE_PATTERN = r"^[a-z][a-z0-9_.-]{0,63}$"
_SERVICE_PATTERN = r"^[a-z][a-z0-9_.-]{0,63}$"
_ENVIRONMENT_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,31}$"
_RELEASE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}$"
_TRACE_PATTERN = r"^[0-9a-f]{32}$"
_EXCEPTION_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.]{0,199}$"


class RagRunFactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    org_id: UUID
    workspace_id: UUID
    run_id: UUID
    model_id: UUID | None = None
    trace_id: str | None = Field(default=None, pattern=_TRACE_PATTERN)
    environment: str = Field(min_length=1, max_length=32, pattern=_ENVIRONMENT_PATTERN)
    release: str | None = Field(default=None, pattern=_RELEASE_PATTERN)
    route: RagRoute
    outcome: RagRunOutcome
    error_code: str | None = Field(default=None, pattern=_CODE_PATTERN)
    latency_ms: int = Field(ge=0)
    ttft_ms: int | None = Field(default=None, ge=0)
    route_ms: int = Field(default=0, ge=0)
    retrieval_ms: int = Field(default=0, ge=0)
    provider_ms: int = Field(default=0, ge=0)
    persistence_ms: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    retrieval_count: int = Field(default=0, ge=0)
    citation_count: int = Field(default=0, ge=0)
    memory_item_count: int = Field(default=0, ge=0)
    attempts: int = Field(default=0, ge=0, le=1000)
    estimated_cost_microusd: int = Field(default=0, ge=0)
    accepted_at: datetime | None = None
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def validate_time_order(self) -> Self:
        if (
            self.accepted_at is not None
            and self.finished_at is not None
            and self.finished_at < self.accepted_at
        ):
            raise ValueError("finished_at must not precede accepted_at")
        return self


class ErrorOccurrenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ErrorCategory
    code: str = Field(min_length=1, max_length=64, pattern=_CODE_PATTERN)
    service: str = Field(min_length=1, max_length=64, pattern=_SERVICE_PATTERN)
    environment: str = Field(min_length=1, max_length=32, pattern=_ENVIRONMENT_PATTERN)
    exception_type: str = Field(
        min_length=1,
        max_length=200,
        pattern=_EXCEPTION_PATTERN,
    )
    top_frame: str | None = Field(default=None, min_length=1, max_length=300)
    org_id: UUID | None = None
    workspace_id: UUID | None = None
    run_id: UUID | None = None
    trace_id: str | None = Field(default=None, pattern=_TRACE_PATTERN)
    http_method: HttpMethod | None = None
    route_template: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^/[^\s]*$",
    )
    http_status: int | None = Field(default=None, ge=100, le=599)
    release: str | None = Field(default=None, pattern=_RELEASE_PATTERN)
    occurred_at: datetime | None = None

    @model_validator(mode="after")
    def validate_run_scope(self) -> Self:
        if self.workspace_id is not None and self.org_id is None:
            raise ValueError("workspace scope requires organization")
        if self.run_id is not None and (self.org_id is None or self.workspace_id is None):
            raise ValueError("run scope requires organization and workspace")
        return self


class RagRunFactOut(RagRunFactCreate):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    accepted_at: datetime
    finished_at: datetime


class ErrorIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    category: ErrorCategory
    code: str
    service: str
    environment: str
    exception_type: str
    status: Literal["open", "resolved", "ignored"]
    alert_state: Literal["none", "firing", "acknowledged"]
    owner: str | None
    first_release: str | None
    last_release: str | None
    occurrence_count: int = Field(gt=0)
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None


class ErrorOccurrenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    issue_id: UUID
    org_id: UUID | None
    workspace_id: UUID | None
    run_id: UUID | None
    trace_id: str | None
    code: str
    exception_type: str
    http_method: HttpMethod | None
    route_template: str | None
    http_status: int | None
    release: str | None
    occurred_at: datetime


class RagOperationsFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_at: AwareDatetime
    to_at: AwareDatetime
    org_id: UUID | None = None
    workspace_id: UUID | None = None
    route: RagRoute | None = None
    outcome: RagRunOutcome | None = None
    model_id: UUID | None = None
    environment: str | None = Field(default=None, pattern=_ENVIRONMENT_PATTERN)
    release: str | None = Field(default=None, pattern=_RELEASE_PATTERN)

    @model_validator(mode="after")
    def validate_window_and_scope(self) -> Self:
        if self.to_at <= self.from_at or self.to_at - self.from_at > timedelta(days=90):
            raise ValueError("rag_operations_window_invalid")
        if self.workspace_id is not None and self.org_id is None:
            raise ValueError("rag_operations_workspace_requires_org")
        return self


class RagOperationsOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_count: int = Field(ge=0)
    grounded_count: int = Field(ge=0)
    no_answer_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    cancelled_count: int = Field(ge=0)
    grounded_rate: float = Field(ge=0, le=1)
    no_answer_rate: float = Field(ge=0, le=1)
    p50_latency_ms: float | None = Field(default=None, ge=0)
    p95_latency_ms: float | None = Field(default=None, ge=0)
    p99_latency_ms: float | None = Field(default=None, ge=0)
    average_ttft_ms: float | None = Field(default=None, ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    estimated_cost_microusd: int = Field(ge=0)
