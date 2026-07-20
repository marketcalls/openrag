"""Strict API contracts for immutable, budgeted RAG evaluations."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

EvaluationRunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
EvaluationCaseStatus = Literal["queued", "completed", "failed", "skipped"]


class EvaluationEvidenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_version_id: UUID
    evidence_span_id: UUID


class EvaluationCaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    should_refuse: bool = False
    expected_evidence: list[EvaluationEvidenceCreate] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_expected_evidence(self) -> Self:
        identities = {
            (item.document_version_id, item.evidence_span_id)
            for item in self.expected_evidence
        }
        if len(identities) != len(self.expected_evidence):
            raise ValueError("evaluation_evidence_duplicate")
        if self.should_refuse and self.expected_evidence:
            raise ValueError("evaluation_refusal_evidence_invalid")
        if not self.should_refuse and not self.expected_evidence:
            raise ValueError("evaluation_answer_evidence_required")
        return self


class EvaluationDatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class EvaluationDatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    org_id: UUID
    workspace_id: UUID
    name: str
    description: str
    archived: bool
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class EvaluationDatasetVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=100)
    cases: list[EvaluationCaseCreate] = Field(min_length=1, max_length=1000)


class EvaluationDatasetVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    org_id: UUID
    workspace_id: UUID
    dataset_id: UUID
    version: int
    label: str | None
    status: Literal["sealed"]
    case_count: int
    content_digest: str
    created_by: UUID
    created_at: datetime
    sealed_at: datetime


class EvaluationCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    sequence: int
    question: str
    should_refuse: bool
    expected_evidence: list[EvaluationEvidenceCreate]


class EvaluationDatasetVersionDetail(EvaluationDatasetVersionOut):
    cases: list[EvaluationCaseOut]


class EvaluationRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version_id: UUID
    model_id: UUID
    evaluator_model_id: UUID | None = None
    use_llm_judge: bool = False
    max_cases: int = Field(ge=1, le=10_000)
    max_tokens: int = Field(ge=1, le=50_000_000)
    max_cost_microusd: int = Field(ge=1, le=100_000_000_000)
    client_request_id: UUID | None = None

    @model_validator(mode="after")
    def validate_evaluator(self) -> Self:
        if self.use_llm_judge and self.evaluator_model_id is None:
            raise ValueError("evaluation_judge_model_required")
        if not self.use_llm_judge and self.evaluator_model_id is not None:
            raise ValueError("evaluation_judge_model_unused")
        return self


class EvaluationRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    org_id: UUID
    workspace_id: UUID
    dataset_version_id: UUID
    model_id: UUID
    evaluator_model_id: UUID | None
    use_llm_judge: bool
    status: EvaluationRunStatus
    max_cases: int
    max_tokens: int
    max_cost_microusd: int
    total_cases: int
    completed_cases: int
    failed_cases: int
    consumed_tokens: int
    consumed_cost_microusd: int
    error_code: str | None
    recall: float | None
    precision: float | None
    mrr: float | None
    ndcg: float | None
    citation_precision: float | None
    citation_recall: float | None
    groundedness: float | None
    answer_relevance: float | None
    correct_refusal: float | None
    created_by: UUID
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class EvaluationCaseResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    case_id: UUID
    sequence: int
    status: EvaluationCaseStatus
    did_refuse: bool | None
    retrieved_evidence_ids: list[UUID]
    cited_evidence_ids: list[UUID]
    recall: float | None
    precision: float | None
    mrr: float | None
    ndcg: float | None
    citation_precision: float | None
    citation_recall: float | None
    groundedness: float | None
    answer_relevance: float | None
    correct_refusal: float | None
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_microusd: int
    answer_digest: str | None
    error_code: str | None
    created_at: datetime


class EvaluationRunDetail(EvaluationRunOut):
    results: list[EvaluationCaseResultOut]
