"""Add opt-in durable document enrichment jobs.

Revision ID: f5a7b9c1d3e4
Revises: e4f6a8c0d2b3
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f5a7b9c1d3e4"
down_revision: str | None = "e4f6a8c0d2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "enrichment_enabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_table(
        "document_enrichment_jobs",
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("embedding_deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_probe_revision", sa.Integer(), nullable=False),
        sa.Column("prompt_contract_version", sa.String(length=100), nullable=False),
        sa.Column("evidence_start_ordinal", sa.Integer(), nullable=False),
        sa.Column("evidence_end_ordinal", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("total_evidence", sa.Integer(), nullable=False),
        sa.Column("generated_evidence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("invalid_evidence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "source IN ('approval','backfill','reindex')",
            name="ck_document_enrichment_jobs_source",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','skipped')",
            name="ck_document_enrichment_jobs_status",
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND 8",
            name="ck_document_enrichment_jobs_attempts",
        ),
        sa.CheckConstraint(
            "model_probe_revision > 0",
            name="ck_document_enrichment_jobs_model_revision",
        ),
        sa.CheckConstraint(
            "evidence_start_ordinal >= 0 "
            "AND evidence_end_ordinal > evidence_start_ordinal "
            "AND evidence_end_ordinal - evidence_start_ordinal <= 16",
            name="ck_document_enrichment_jobs_batch",
        ),
        sa.CheckConstraint(
            "char_length(prompt_contract_version) BETWEEN 1 AND 100",
            name="ck_document_enrichment_jobs_prompt_version",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_owner IS NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_document_enrichment_jobs_lease",
        ),
        sa.CheckConstraint(
            "total_evidence = evidence_end_ordinal - evidence_start_ordinal "
            "AND generated_evidence >= 0 "
            "AND invalid_evidence >= 0 AND prompt_tokens >= 0 "
            "AND completion_tokens >= 0 "
            "AND generated_evidence + invalid_evidence <= total_evidence "
            "AND (status <> 'completed' OR "
            "generated_evidence + invalid_evidence = total_evidence)",
            name="ck_document_enrichment_jobs_results",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR char_length(error_code) BETWEEN 1 AND 64",
            name="ck_document_enrichment_jobs_error",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["embedding_deployment_id"],
            ["embedding_deployments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "document_version_id"],
            [
                "document_versions.org_id",
                "document_versions.workspace_id",
                "document_versions.id",
            ],
            name="fk_document_enrichment_jobs_scope_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_version_id",
            "embedding_deployment_id",
            "model_id",
            "model_probe_revision",
            "prompt_contract_version",
            "evidence_start_ordinal",
            name="uq_document_enrichment_jobs_generation",
        ),
    )
    op.create_index(
        "ix_document_enrichment_jobs_claim",
        "document_enrichment_jobs",
        ["status", "lease_expires_at", "created_at", "id"],
    )
    for column in (
        "org_id",
        "workspace_id",
        "document_version_id",
        "embedding_deployment_id",
        "model_id",
        "status",
        "lease_token",
        "lease_expires_at",
    ):
        op.create_index(
            f"ix_document_enrichment_jobs_{column}",
            "document_enrichment_jobs",
            [column],
        )


def downgrade() -> None:
    op.drop_table("document_enrichment_jobs")
    op.drop_column("workspaces", "enrichment_enabled")
