"""rag operations facts

Revision ID: c8e0a3b5d7f9
Revises: b7d9f2a4c6e8
Create Date: 2026-07-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e0a3b5d7f9"
down_revision: str | Sequence[str] | None = "b7d9f2a4c6e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rag_run_facts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("model_id", sa.UUID(), nullable=True),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("release", sa.String(length=100), nullable=True),
        sa.Column("route", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("ttft_ms", sa.Integer(), nullable=True),
        sa.Column("route_ms", sa.Integer(), nullable=False),
        sa.Column("retrieval_ms", sa.Integer(), nullable=False),
        sa.Column("provider_ms", sa.Integer(), nullable=False),
        sa.Column("persistence_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("retrieval_count", sa.Integer(), nullable=False),
        sa.Column("citation_count", sa.Integer(), nullable=False),
        sa.Column("memory_item_count", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "route IN ('direct','conversation','rag','analytics','clarify','unknown')",
            name="ck_rag_run_facts_route",
        ),
        sa.CheckConstraint(
            "outcome IN ('grounded','conversational','no_answer','failed','cancelled')",
            name="ck_rag_run_facts_outcome",
        ),
        sa.CheckConstraint(
            "latency_ms >= 0 AND (ttft_ms IS NULL OR ttft_ms >= 0) "
            "AND route_ms >= 0 AND retrieval_ms >= 0 AND provider_ms >= 0 "
            "AND persistence_ms >= 0 AND prompt_tokens >= 0 "
            "AND completion_tokens >= 0 AND retrieval_count >= 0 "
            "AND citation_count >= 0 AND memory_item_count >= 0 "
            "AND attempts >= 0 AND estimated_cost_microusd >= 0",
            name="ck_rag_run_facts_metrics_nonnegative",
        ),
        sa.CheckConstraint(
            "trace_id IS NULL OR trace_id ~ '^[0-9a-f]{32}$'",
            name="ck_rag_run_facts_trace_id",
        ),
        sa.CheckConstraint(
            "finished_at >= accepted_at",
            name="ck_rag_run_facts_time_order",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "run_id"],
            ["agent_runs.org_id", "agent_runs.workspace_id", "agent_runs.id"],
            name="fk_rag_run_facts_org_workspace_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "run_id", name="uq_rag_run_facts_org_run"),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_rag_run_facts_org_workspace_id",
        ),
    )
    for name, columns in (
        ("ix_rag_run_facts_org_id", ["org_id"]),
        ("ix_rag_run_facts_workspace_id", ["workspace_id"]),
        ("ix_rag_run_facts_run_id", ["run_id"]),
        ("ix_rag_run_facts_model_id", ["model_id"]),
        ("ix_rag_run_facts_trace_id", ["trace_id"]),
        ("ix_rag_run_facts_environment", ["environment"]),
        ("ix_rag_run_facts_release", ["release"]),
        ("ix_rag_run_facts_route", ["route"]),
        ("ix_rag_run_facts_outcome", ["outcome"]),
        ("ix_rag_run_facts_error_code", ["error_code"]),
        ("ix_rag_run_facts_accepted_at", ["accepted_at"]),
        ("ix_rag_run_facts_time", ["accepted_at", "id"]),
        ("ix_rag_run_facts_org_time", ["org_id", "accepted_at", "id"]),
        ("ix_rag_run_facts_workspace_time", ["workspace_id", "accepted_at", "id"]),
        ("ix_rag_run_facts_outcome_time", ["outcome", "accepted_at"]),
    ):
        op.create_index(name, "rag_run_facts", columns)

    op.create_table(
        "error_issues",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("exception_type", sa.String(length=200), nullable=False),
        sa.Column("top_frame", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("alert_state", sa.String(length=32), nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=True),
        sa.Column("first_release", sa.String(length=100), nullable=True),
        sa.Column("last_release", sa.String(length=100), nullable=True),
        sa.Column("occurrence_count", sa.BigInteger(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "category IN ('validation','policy','authentication','authorization',"
            "'rate_limit','admission_overload','provider_transient',"
            "'provider_permanent','retrieval','embedding','reranking','ingestion',"
            "'ocr','storage','tool','cancellation','persistence','broker','internal')",
            name="ck_error_issues_category",
        ),
        sa.CheckConstraint(
            "status IN ('open','resolved','ignored')",
            name="ck_error_issues_status",
        ),
        sa.CheckConstraint(
            "alert_state IN ('none','firing','acknowledged')",
            name="ck_error_issues_alert_state",
        ),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_error_issues_fingerprint",
        ),
        sa.CheckConstraint(
            "occurrence_count > 0 AND last_seen_at >= first_seen_at",
            name="ck_error_issues_counts_time",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "environment",
            "service",
            "fingerprint",
            name="uq_error_issues_environment_service_fingerprint",
        ),
    )
    for name, columns in (
        ("ix_error_issues_category", ["category"]),
        ("ix_error_issues_code", ["code"]),
        ("ix_error_issues_service", ["service"]),
        ("ix_error_issues_environment", ["environment"]),
        ("ix_error_issues_status", ["status"]),
        ("ix_error_issues_alert_state", ["alert_state"]),
        ("ix_error_issues_last_seen_at", ["last_seen_at"]),
        ("ix_error_issues_last_seen", ["last_seen_at", "id"]),
        ("ix_error_issues_status_last_seen", ["status", "last_seen_at", "id"]),
    ):
        op.create_index(name, "error_issues", columns)

    op.create_table(
        "error_occurrences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("issue_id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("exception_type", sa.String(length=200), nullable=False),
        sa.Column("http_method", sa.String(length=10), nullable=True),
        sa.Column("route_template", sa.String(length=200), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("release", sa.String(length=100), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(workspace_id IS NULL OR org_id IS NOT NULL) AND "
            "(run_id IS NULL OR (org_id IS NOT NULL AND workspace_id IS NOT NULL))",
            name="ck_error_occurrences_run_scope",
        ),
        sa.CheckConstraint(
            "trace_id IS NULL OR trace_id ~ '^[0-9a-f]{32}$'",
            name="ck_error_occurrences_trace_id",
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name="ck_error_occurrences_http_status",
        ),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["error_issues.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_error_occurrences_org_workspace_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "run_id"],
            ["agent_runs.org_id", "agent_runs.workspace_id", "agent_runs.id"],
            name="fk_error_occurrences_org_workspace_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_error_occurrences_issue_id", ["issue_id"]),
        ("ix_error_occurrences_org_id", ["org_id"]),
        ("ix_error_occurrences_workspace_id", ["workspace_id"]),
        ("ix_error_occurrences_run_id", ["run_id"]),
        ("ix_error_occurrences_trace_id", ["trace_id"]),
        ("ix_error_occurrences_code", ["code"]),
        ("ix_error_occurrences_occurred_at", ["occurred_at"]),
        ("ix_error_occurrences_issue_time", ["issue_id", "occurred_at", "id"]),
        ("ix_error_occurrences_org_time", ["org_id", "occurred_at", "id"]),
        ("ix_error_occurrences_run_time", ["run_id", "occurred_at", "id"]),
    ):
        op.create_index(name, "error_occurrences", columns)


def downgrade() -> None:
    op.drop_table("error_occurrences")
    op.drop_table("error_issues")
    op.drop_table("rag_run_facts")
