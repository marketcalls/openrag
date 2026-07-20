"""Add immutable, budgeted RAG evaluation datasets and runs.

Revision ID: d9f1b4c6e8a0
Revises: c8e0a3b5d7f9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d9f1b4c6e8a0"
down_revision: str | Sequence[str] | None = "c8e0a3b5d7f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
    ]


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "evaluation_datasets",
        *_timestamps(),
        *_scope_columns(),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_evaluation_datasets_name",
        ),
        sa.CheckConstraint(
            "char_length(description) <= 500",
            name="ck_evaluation_datasets_description",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_evaluation_datasets_scope_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_evaluation_datasets_scope_creator",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "workspace_id", "id", name="uq_evaluation_datasets_scope_id"
        ),
        sa.UniqueConstraint(
            "org_id", "workspace_id", "name", name="uq_evaluation_datasets_scope_name"
        ),
    )
    op.create_index("ix_evaluation_datasets_org_id", "evaluation_datasets", ["org_id"])
    op.create_index(
        "ix_evaluation_datasets_archived", "evaluation_datasets", ["archived"]
    )

    op.create_table(
        "evaluation_dataset_versions",
        *_timestamps(),
        *_scope_columns(),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("sealed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_evaluation_dataset_versions_number"),
        sa.CheckConstraint("status = 'sealed'", name="ck_evaluation_dataset_versions_status"),
        sa.CheckConstraint(
            "case_count BETWEEN 1 AND 1000",
            name="ck_evaluation_dataset_versions_case_count",
        ),
        sa.CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_dataset_versions_digest",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "dataset_id"],
            [
                "evaluation_datasets.org_id",
                "evaluation_datasets.workspace_id",
                "evaluation_datasets.id",
            ],
            name="fk_evaluation_dataset_versions_scope_dataset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_evaluation_dataset_versions_scope_creator",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_evaluation_dataset_versions_scope_id",
        ),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "dataset_id",
            "version",
            name="uq_evaluation_dataset_versions_number",
        ),
    )
    op.create_index(
        "ix_evaluation_dataset_versions_org_id", "evaluation_dataset_versions", ["org_id"]
    )
    op.create_index(
        "ix_evaluation_dataset_versions_dataset_id",
        "evaluation_dataset_versions",
        ["dataset_id"],
    )

    op.create_table(
        "evaluation_cases",
        *_timestamps(),
        *_scope_columns(),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("should_refuse", sa.Boolean(), nullable=False),
        sa.CheckConstraint("sequence > 0", name="ck_evaluation_cases_sequence"),
        sa.CheckConstraint(
            "char_length(btrim(question)) BETWEEN 1 AND 2000",
            name="ck_evaluation_cases_question",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "dataset_version_id"],
            [
                "evaluation_dataset_versions.org_id",
                "evaluation_dataset_versions.workspace_id",
                "evaluation_dataset_versions.id",
            ],
            name="fk_evaluation_cases_scope_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "workspace_id", "id", name="uq_evaluation_cases_scope_id"
        ),
        sa.UniqueConstraint(
            "dataset_version_id", "sequence", name="uq_evaluation_cases_version_sequence"
        ),
    )
    op.create_index("ix_evaluation_cases_org_id", "evaluation_cases", ["org_id"])
    op.create_index(
        "ix_evaluation_cases_dataset_version_id",
        "evaluation_cases",
        ["dataset_version_id"],
    )

    op.create_table(
        "evaluation_case_evidence",
        *_timestamps(),
        *_scope_columns(),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_span_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("position > 0", name="ck_evaluation_case_evidence_position"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "case_id"],
            ["evaluation_cases.org_id", "evaluation_cases.workspace_id", "evaluation_cases.id"],
            name="fk_evaluation_case_evidence_scope_case",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "document_version_id", "evidence_span_id"],
            [
                "document_evidence_spans.org_id",
                "document_evidence_spans.document_version_id",
                "document_evidence_spans.id",
            ],
            name="fk_evaluation_case_evidence_scope_span",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_evaluation_case_evidence_scope_id",
        ),
        sa.UniqueConstraint(
            "case_id", "evidence_span_id", name="uq_evaluation_case_evidence_span"
        ),
    )
    for column in ("org_id", "case_id", "document_version_id", "evidence_span_id"):
        op.create_index(
            f"ix_evaluation_case_evidence_{column}", "evaluation_case_evidence", [column]
        )

    op.create_table(
        "evaluation_runs",
        *_timestamps(),
        *_scope_columns(),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("evaluator_model_id", sa.Uuid(), nullable=True),
        sa.Column("use_llm_judge", sa.Boolean(), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("max_cases", sa.Integer(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("max_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("completed_cases", sa.Integer(), nullable=False),
        sa.Column("failed_cases", sa.Integer(), nullable=False),
        sa.Column("consumed_tokens", sa.Integer(), nullable=False),
        sa.Column("consumed_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        *[sa.Column(name, sa.Float(), nullable=True) for name in (
            "recall", "precision", "mrr", "ndcg", "citation_precision",
            "citation_recall", "groundedness", "answer_relevance", "correct_refusal",
        )],
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_evaluation_runs_status",
        ),
        sa.CheckConstraint(
            "max_cases BETWEEN 1 AND 10000 AND max_tokens BETWEEN 1 AND 50000000 "
            "AND max_cost_microusd BETWEEN 1 AND 100000000000",
            name="ck_evaluation_runs_budgets",
        ),
        sa.CheckConstraint(
            "total_cases >= 0 AND completed_cases >= 0 AND failed_cases >= 0 "
            "AND completed_cases + failed_cases <= total_cases",
            name="ck_evaluation_runs_counts",
        ),
        sa.CheckConstraint(
            "consumed_tokens >= 0 AND consumed_cost_microusd >= 0",
            name="ck_evaluation_runs_consumption",
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 1000", name="ck_evaluation_runs_attempts"),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_evaluation_runs_lease",
        ),
        sa.CheckConstraint(
            "(use_llm_judge AND evaluator_model_id IS NOT NULL) OR "
            "(NOT use_llm_judge AND evaluator_model_id IS NULL)",
            name="ck_evaluation_runs_judge",
        ),
        sa.CheckConstraint(
            " AND ".join(f"({name} IS NULL OR {name} BETWEEN 0 AND 1)" for name in (
                "recall", "precision", "mrr", "ndcg", "citation_precision",
                "citation_recall", "groundedness", "answer_relevance", "correct_refusal",
            )),
            name="ck_evaluation_runs_metrics",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(["evaluator_model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "dataset_version_id"],
            [
                "evaluation_dataset_versions.org_id",
                "evaluation_dataset_versions.workspace_id",
                "evaluation_dataset_versions.id",
            ],
            name="fk_evaluation_runs_scope_version",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_evaluation_runs_scope_creator",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "workspace_id", "id", name="uq_evaluation_runs_scope_id"
        ),
        sa.UniqueConstraint(
            "org_id",
            "created_by",
            "client_request_id",
            name="uq_evaluation_runs_creator_request",
        ),
    )
    for column in (
        "org_id", "dataset_version_id", "model_id", "status", "lease_token",
        "lease_expires_at", "created_by",
    ):
        op.create_index(f"ix_evaluation_runs_{column}", "evaluation_runs", [column])
    op.create_index(
        "ix_evaluation_runs_claim", "evaluation_runs", ["status", "created_at", "id"]
    )

    op.create_table(
        "evaluation_case_results",
        *_timestamps(),
        *_scope_columns(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("did_refuse", sa.Boolean(), nullable=True),
        sa.Column(
            "retrieved_evidence_ids",
            postgresql.ARRAY(sa.Uuid()),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column(
            "cited_evidence_ids",
            postgresql.ARRAY(sa.Uuid()),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        *[sa.Column(name, sa.Float(), nullable=True) for name in (
            "recall", "precision", "mrr", "ndcg", "citation_precision",
            "citation_recall", "groundedness", "answer_relevance", "correct_refusal",
        )],
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("answer_digest", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','completed','failed','skipped')",
            name="ck_evaluation_case_results_status",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_evaluation_case_results_sequence"),
        sa.CheckConstraint(
            "latency_ms >= 0 AND prompt_tokens >= 0 AND completion_tokens >= 0 "
            "AND estimated_cost_microusd >= 0",
            name="ck_evaluation_case_results_usage",
        ),
        sa.CheckConstraint(
            "answer_digest IS NULL OR answer_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_case_results_digest",
        ),
        sa.CheckConstraint(
            " AND ".join(f"({name} IS NULL OR {name} BETWEEN 0 AND 1)" for name in (
                "recall", "precision", "mrr", "ndcg", "citation_precision",
                "citation_recall", "groundedness", "answer_relevance", "correct_refusal",
            )),
            name="ck_evaluation_case_results_metrics",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "run_id"],
            ["evaluation_runs.org_id", "evaluation_runs.workspace_id", "evaluation_runs.id"],
            name="fk_evaluation_case_results_scope_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "case_id"],
            ["evaluation_cases.org_id", "evaluation_cases.workspace_id", "evaluation_cases.id"],
            name="fk_evaluation_case_results_scope_case",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_evaluation_case_results_scope_id",
        ),
        sa.UniqueConstraint("run_id", "case_id", name="uq_evaluation_case_results_run_case"),
    )
    for column in ("org_id", "run_id", "case_id"):
        op.create_index(
            f"ix_evaluation_case_results_{column}", "evaluation_case_results", [column]
        )

    op.execute(
        """
        CREATE FUNCTION openrag_reject_evaluation_corpus_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'sealed evaluation corpus is immutable' USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table in (
        "evaluation_dataset_versions",
        "evaluation_cases",
        "evaluation_case_evidence",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION openrag_reject_evaluation_corpus_mutation()"
        )


def downgrade() -> None:
    for table in (
        "evaluation_case_evidence",
        "evaluation_cases",
        "evaluation_dataset_versions",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS openrag_reject_evaluation_corpus_mutation()")
    for table in (
        "evaluation_case_results",
        "evaluation_runs",
        "evaluation_case_evidence",
        "evaluation_cases",
        "evaluation_dataset_versions",
        "evaluation_datasets",
    ):
        op.drop_table(table)
