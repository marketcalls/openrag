"""Durable, content-free product facts for the RAG operations read model."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk, naive_utc


class RagRunFact(UUIDPk, Base):
    __tablename__ = "rag_run_facts"
    __table_args__ = (
        UniqueConstraint("org_id", "run_id", name="uq_rag_run_facts_org_run"),
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_rag_run_facts_org_workspace_id",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "run_id"],
            ["agent_runs.org_id", "agent_runs.workspace_id", "agent_runs.id"],
            name="fk_rag_run_facts_org_workspace_run",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "route IN ('direct','conversation','rag','analytics','clarify','unknown')",
            name="ck_rag_run_facts_route",
        ),
        CheckConstraint(
            "outcome IN ('grounded','conversational','no_answer','failed','cancelled')",
            name="ck_rag_run_facts_outcome",
        ),
        CheckConstraint(
            "latency_ms >= 0 AND (ttft_ms IS NULL OR ttft_ms >= 0) "
            "AND route_ms >= 0 AND retrieval_ms >= 0 AND provider_ms >= 0 "
            "AND persistence_ms >= 0 AND prompt_tokens >= 0 "
            "AND completion_tokens >= 0 AND retrieval_count >= 0 "
            "AND citation_count >= 0 AND memory_item_count >= 0 "
            "AND attempts >= 0 AND estimated_cost_microusd >= 0",
            name="ck_rag_run_facts_metrics_nonnegative",
        ),
        CheckConstraint(
            "trace_id IS NULL OR trace_id ~ '^[0-9a-f]{32}$'",
            name="ck_rag_run_facts_trace_id",
        ),
        CheckConstraint(
            "finished_at >= accepted_at",
            name="ck_rag_run_facts_time_order",
        ),
        Index("ix_rag_run_facts_time", "accepted_at", "id"),
        Index("ix_rag_run_facts_org_time", "org_id", "accepted_at", "id"),
        Index(
            "ix_rag_run_facts_workspace_time",
            "workspace_id",
            "accepted_at",
            "id",
        ),
        Index("ix_rag_run_facts_outcome_time", "outcome", "accepted_at"),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    run_id: Mapped[UUID] = mapped_column(index=True)
    model_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("models.id"),
        default=None,
        index=True,
    )
    trace_id: Mapped[str | None] = mapped_column(String(32), default=None, index=True)
    environment: Mapped[str] = mapped_column(String(32), index=True)
    release: Mapped[str | None] = mapped_column(String(100), default=None, index=True)
    route: Mapped[str] = mapped_column(String(32), index=True)
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    latency_ms: Mapped[int]
    ttft_ms: Mapped[int | None] = mapped_column(default=None)
    route_ms: Mapped[int] = mapped_column(default=0)
    retrieval_ms: Mapped[int] = mapped_column(default=0)
    provider_ms: Mapped[int] = mapped_column(default=0)
    persistence_ms: Mapped[int] = mapped_column(default=0)
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    retrieval_count: Mapped[int] = mapped_column(default=0)
    citation_count: Mapped[int] = mapped_column(default=0)
    memory_item_count: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    estimated_cost_microusd: Mapped[int] = mapped_column(BigInteger, default=0)
    accepted_at: Mapped[datetime] = mapped_column(index=True)
    finished_at: Mapped[datetime]


class ErrorIssue(UUIDPk, Base):
    __tablename__ = "error_issues"
    __table_args__ = (
        UniqueConstraint(
            "environment",
            "service",
            "fingerprint",
            name="uq_error_issues_environment_service_fingerprint",
        ),
        CheckConstraint(
            "category IN ('validation','policy','authentication','authorization',"
            "'rate_limit','admission_overload','provider_transient',"
            "'provider_permanent','retrieval','embedding','reranking','ingestion',"
            "'ocr','storage','tool','cancellation','persistence','broker','internal')",
            name="ck_error_issues_category",
        ),
        CheckConstraint(
            "status IN ('open','resolved','ignored')",
            name="ck_error_issues_status",
        ),
        CheckConstraint(
            "alert_state IN ('none','firing','acknowledged')",
            name="ck_error_issues_alert_state",
        ),
        CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_error_issues_fingerprint",
        ),
        CheckConstraint(
            "occurrence_count > 0 AND last_seen_at >= first_seen_at",
            name="ck_error_issues_counts_time",
        ),
        Index("ix_error_issues_last_seen", "last_seen_at", "id"),
        Index("ix_error_issues_status_last_seen", "status", "last_seen_at", "id"),
    )

    fingerprint: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    service: Mapped[str] = mapped_column(String(64), index=True)
    environment: Mapped[str] = mapped_column(String(32), index=True)
    exception_type: Mapped[str] = mapped_column(String(200))
    top_frame: Mapped[str | None] = mapped_column(String(300), default=None)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    alert_state: Mapped[str] = mapped_column(String(32), default="none", index=True)
    owner: Mapped[str | None] = mapped_column(String(200), default=None)
    first_release: Mapped[str | None] = mapped_column(String(100), default=None)
    last_release: Mapped[str | None] = mapped_column(String(100), default=None)
    occurrence_count: Mapped[int] = mapped_column(BigInteger, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(default=naive_utc)
    last_seen_at: Mapped[datetime] = mapped_column(default=naive_utc, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)


class ErrorOccurrence(UUIDPk, Base):
    __tablename__ = "error_occurrences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_error_occurrences_org_workspace_workspace",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "run_id"],
            ["agent_runs.org_id", "agent_runs.workspace_id", "agent_runs.id"],
            name="fk_error_occurrences_org_workspace_run",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "(workspace_id IS NULL OR org_id IS NOT NULL) AND "
            "(run_id IS NULL OR (org_id IS NOT NULL AND workspace_id IS NOT NULL))",
            name="ck_error_occurrences_run_scope",
        ),
        CheckConstraint(
            "trace_id IS NULL OR trace_id ~ '^[0-9a-f]{32}$'",
            name="ck_error_occurrences_trace_id",
        ),
        CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name="ck_error_occurrences_http_status",
        ),
        Index("ix_error_occurrences_issue_time", "issue_id", "occurred_at", "id"),
        Index("ix_error_occurrences_org_time", "org_id", "occurred_at", "id"),
        Index("ix_error_occurrences_run_time", "run_id", "occurred_at", "id"),
    )

    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("error_issues.id", ondelete="CASCADE"),
        index=True,
    )
    org_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"),
        default=None,
        index=True,
    )
    workspace_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    run_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), default=None, index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    exception_type: Mapped[str] = mapped_column(String(200))
    http_method: Mapped[str | None] = mapped_column(String(10), default=None)
    route_template: Mapped[str | None] = mapped_column(String(200), default=None)
    http_status: Mapped[int | None] = mapped_column(default=None)
    release: Mapped[str | None] = mapped_column(String(100), default=None)
    occurred_at: Mapped[datetime] = mapped_column(default=naive_utc, index=True)
