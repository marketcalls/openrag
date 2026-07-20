"""Tenant-scoped persistence for immutable RAG evaluation corpora and results."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk, naive_utc


class EvaluationDataset(UUIDPk, Base):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "workspace_id", "id", name="uq_evaluation_datasets_scope_id"
        ),
        UniqueConstraint(
            "org_id", "workspace_id", "name", name="uq_evaluation_datasets_scope_name"
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_evaluation_datasets_scope_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_evaluation_datasets_scope_creator",
        ),
        CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_evaluation_datasets_name",
        ),
        CheckConstraint(
            "char_length(description) <= 500",
            name="ck_evaluation_datasets_description",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_by: Mapped[UUID] = mapped_column(index=True)
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)


class EvaluationDatasetVersion(UUIDPk, Base):
    __tablename__ = "evaluation_dataset_versions"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_evaluation_dataset_versions_scope_id",
        ),
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "dataset_id",
            "version",
            name="uq_evaluation_dataset_versions_number",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "dataset_id"],
            [
                "evaluation_datasets.org_id",
                "evaluation_datasets.workspace_id",
                "evaluation_datasets.id",
            ],
            name="fk_evaluation_dataset_versions_scope_dataset",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_evaluation_dataset_versions_scope_creator",
        ),
        CheckConstraint("version > 0", name="ck_evaluation_dataset_versions_number"),
        CheckConstraint("status = 'sealed'", name="ck_evaluation_dataset_versions_status"),
        CheckConstraint(
            "case_count BETWEEN 1 AND 1000",
            name="ck_evaluation_dataset_versions_case_count",
        ),
        CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_dataset_versions_digest",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    dataset_id: Mapped[UUID] = mapped_column(index=True)
    version: Mapped[int]
    label: Mapped[str | None] = mapped_column(String(100), default=None)
    status: Mapped[str] = mapped_column(String(16), default="sealed")
    case_count: Mapped[int]
    content_digest: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[UUID] = mapped_column(index=True)
    sealed_at: Mapped[datetime] = mapped_column(default=naive_utc)


class EvaluationCase(UUIDPk, Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "workspace_id", "id", name="uq_evaluation_cases_scope_id"
        ),
        UniqueConstraint(
            "dataset_version_id", "sequence", name="uq_evaluation_cases_version_sequence"
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "dataset_version_id"],
            [
                "evaluation_dataset_versions.org_id",
                "evaluation_dataset_versions.workspace_id",
                "evaluation_dataset_versions.id",
            ],
            name="fk_evaluation_cases_scope_version",
            ondelete="CASCADE",
        ),
        CheckConstraint("sequence > 0", name="ck_evaluation_cases_sequence"),
        CheckConstraint(
            "char_length(btrim(question)) BETWEEN 1 AND 2000",
            name="ck_evaluation_cases_question",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    dataset_version_id: Mapped[UUID] = mapped_column(index=True)
    sequence: Mapped[int]
    question: Mapped[str] = mapped_column(Text)
    should_refuse: Mapped[bool] = mapped_column(Boolean, default=False)


class EvaluationCaseEvidence(UUIDPk, Base):
    __tablename__ = "evaluation_case_evidence"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_evaluation_case_evidence_scope_id",
        ),
        UniqueConstraint(
            "case_id", "evidence_span_id", name="uq_evaluation_case_evidence_span"
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "case_id"],
            ["evaluation_cases.org_id", "evaluation_cases.workspace_id", "evaluation_cases.id"],
            name="fk_evaluation_case_evidence_scope_case",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "document_version_id", "evidence_span_id"],
            [
                "document_evidence_spans.org_id",
                "document_evidence_spans.document_version_id",
                "document_evidence_spans.id",
            ],
            name="fk_evaluation_case_evidence_scope_span",
        ),
        CheckConstraint("position > 0", name="ck_evaluation_case_evidence_position"),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    case_id: Mapped[UUID] = mapped_column(index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    evidence_span_id: Mapped[UUID] = mapped_column(index=True)
    position: Mapped[int]


class EvaluationRun(UUIDPk, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "workspace_id", "id", name="uq_evaluation_runs_scope_id"
        ),
        UniqueConstraint(
            "org_id",
            "created_by",
            "client_request_id",
            name="uq_evaluation_runs_creator_request",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "dataset_version_id"],
            [
                "evaluation_dataset_versions.org_id",
                "evaluation_dataset_versions.workspace_id",
                "evaluation_dataset_versions.id",
            ],
            name="fk_evaluation_runs_scope_version",
        ),
        ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_evaluation_runs_scope_creator",
        ),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_evaluation_runs_status",
        ),
        CheckConstraint(
            "max_cases BETWEEN 1 AND 10000 AND max_tokens BETWEEN 1 AND 50000000 "
            "AND max_cost_microusd BETWEEN 1 AND 100000000000",
            name="ck_evaluation_runs_budgets",
        ),
        CheckConstraint(
            "total_cases >= 0 AND completed_cases >= 0 AND failed_cases >= 0 "
            "AND completed_cases + failed_cases <= total_cases",
            name="ck_evaluation_runs_counts",
        ),
        CheckConstraint(
            "consumed_tokens >= 0 AND consumed_cost_microusd >= 0",
            name="ck_evaluation_runs_consumption",
        ),
        CheckConstraint(
            "attempts BETWEEN 0 AND 1000",
            name="ck_evaluation_runs_attempts",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_evaluation_runs_lease",
        ),
        CheckConstraint(
            "(use_llm_judge AND evaluator_model_id IS NOT NULL) OR "
            "(NOT use_llm_judge AND evaluator_model_id IS NULL)",
            name="ck_evaluation_runs_judge",
        ),
        CheckConstraint(
            "(recall IS NULL OR recall BETWEEN 0 AND 1) "
            "AND (precision IS NULL OR precision BETWEEN 0 AND 1) "
            "AND (mrr IS NULL OR mrr BETWEEN 0 AND 1) "
            "AND (ndcg IS NULL OR ndcg BETWEEN 0 AND 1) "
            "AND (citation_precision IS NULL OR citation_precision BETWEEN 0 AND 1) "
            "AND (citation_recall IS NULL OR citation_recall BETWEEN 0 AND 1) "
            "AND (groundedness IS NULL OR groundedness BETWEEN 0 AND 1) "
            "AND (answer_relevance IS NULL OR answer_relevance BETWEEN 0 AND 1) "
            "AND (correct_refusal IS NULL OR correct_refusal BETWEEN 0 AND 1)",
            name="ck_evaluation_runs_metrics",
        ),
        Index("ix_evaluation_runs_claim", "status", "created_at", "id"),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    dataset_version_id: Mapped[UUID] = mapped_column(index=True)
    model_id: Mapped[UUID] = mapped_column(ForeignKey("models.id"), index=True)
    evaluator_model_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("models.id"), default=None
    )
    use_llm_judge: Mapped[bool] = mapped_column(Boolean, default=False)
    client_request_id: Mapped[UUID | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    max_cases: Mapped[int]
    max_tokens: Mapped[int]
    max_cost_microusd: Mapped[int] = mapped_column(BigInteger)
    total_cases: Mapped[int] = mapped_column(default=0)
    completed_cases: Mapped[int] = mapped_column(default=0)
    failed_cases: Mapped[int] = mapped_column(default=0)
    consumed_tokens: Mapped[int] = mapped_column(default=0)
    consumed_cost_microusd: Mapped[int] = mapped_column(BigInteger, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    recall: Mapped[float | None] = mapped_column(default=None)
    precision: Mapped[float | None] = mapped_column(default=None)
    mrr: Mapped[float | None] = mapped_column(default=None)
    ndcg: Mapped[float | None] = mapped_column(default=None)
    citation_precision: Mapped[float | None] = mapped_column(default=None)
    citation_recall: Mapped[float | None] = mapped_column(default=None)
    groundedness: Mapped[float | None] = mapped_column(default=None)
    answer_relevance: Mapped[float | None] = mapped_column(default=None)
    correct_refusal: Mapped[float | None] = mapped_column(default=None)
    lease_owner: Mapped[str | None] = mapped_column(String(200), default=None)
    lease_token: Mapped[UUID | None] = mapped_column(default=None, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    created_by: Mapped[UUID] = mapped_column(index=True)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class EvaluationCaseResult(UUIDPk, Base):
    __tablename__ = "evaluation_case_results"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_evaluation_case_results_scope_id",
        ),
        UniqueConstraint("run_id", "case_id", name="uq_evaluation_case_results_run_case"),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "run_id"],
            ["evaluation_runs.org_id", "evaluation_runs.workspace_id", "evaluation_runs.id"],
            name="fk_evaluation_case_results_scope_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "case_id"],
            ["evaluation_cases.org_id", "evaluation_cases.workspace_id", "evaluation_cases.id"],
            name="fk_evaluation_case_results_scope_case",
        ),
        CheckConstraint(
            "status IN ('queued','completed','failed','skipped')",
            name="ck_evaluation_case_results_status",
        ),
        CheckConstraint("sequence > 0", name="ck_evaluation_case_results_sequence"),
        CheckConstraint(
            "latency_ms >= 0 AND prompt_tokens >= 0 AND completion_tokens >= 0 "
            "AND estimated_cost_microusd >= 0",
            name="ck_evaluation_case_results_usage",
        ),
        CheckConstraint(
            "answer_digest IS NULL OR answer_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_case_results_digest",
        ),
        CheckConstraint(
            "(recall IS NULL OR recall BETWEEN 0 AND 1) "
            "AND (precision IS NULL OR precision BETWEEN 0 AND 1) "
            "AND (mrr IS NULL OR mrr BETWEEN 0 AND 1) "
            "AND (ndcg IS NULL OR ndcg BETWEEN 0 AND 1) "
            "AND (citation_precision IS NULL OR citation_precision BETWEEN 0 AND 1) "
            "AND (citation_recall IS NULL OR citation_recall BETWEEN 0 AND 1) "
            "AND (groundedness IS NULL OR groundedness BETWEEN 0 AND 1) "
            "AND (answer_relevance IS NULL OR answer_relevance BETWEEN 0 AND 1) "
            "AND (correct_refusal IS NULL OR correct_refusal BETWEEN 0 AND 1)",
            name="ck_evaluation_case_results_metrics",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    run_id: Mapped[UUID] = mapped_column(index=True)
    case_id: Mapped[UUID] = mapped_column(index=True)
    sequence: Mapped[int]
    status: Mapped[str] = mapped_column(String(16), default="queued")
    did_refuse: Mapped[bool | None] = mapped_column(default=None)
    retrieved_evidence_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), default=list
    )
    cited_evidence_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), default=list
    )
    recall: Mapped[float | None] = mapped_column(default=None)
    precision: Mapped[float | None] = mapped_column(default=None)
    mrr: Mapped[float | None] = mapped_column(default=None)
    ndcg: Mapped[float | None] = mapped_column(default=None)
    citation_precision: Mapped[float | None] = mapped_column(default=None)
    citation_recall: Mapped[float | None] = mapped_column(default=None)
    groundedness: Mapped[float | None] = mapped_column(default=None)
    answer_relevance: Mapped[float | None] = mapped_column(default=None)
    correct_refusal: Mapped[float | None] = mapped_column(default=None)
    latency_ms: Mapped[int] = mapped_column(default=0)
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    estimated_cost_microusd: Mapped[int] = mapped_column(BigInteger, default=0)
    answer_digest: Mapped[str | None] = mapped_column(String(64), default=None)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
