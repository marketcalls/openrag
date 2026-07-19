"""fence durable ingest stages

Revision ID: d8a4f2c91e37
Revises: a4f87e62b913
Create Date: 2026-07-20 08:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8a4f2c91e37"
down_revision: str | Sequence[str] | None = "a4f87e62b913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _preflight() -> None:
    invalid = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM ingest_stage_attempts WHERE "
            "pipeline_kind NOT IN ('ingestion','rebuild') "
            "OR stage NOT IN ('parse','chunk','embed','authority_upsert') "
            "OR state NOT IN ('queued','running','succeeded','failed','cancelled') "
            "OR attempts NOT BETWEEN 0 AND 8 "
            "OR lease_owner IS NOT NULL OR lease_expires_at IS NOT NULL "
            "OR (error_code IS NOT NULL "
            "AND char_length(error_code) NOT BETWEEN 1 AND 100))"
        )
    )
    if invalid:
        op.execute(
            "DO $$ BEGIN RAISE EXCEPTION "
            "'OPENRAG_INGEST_STAGE_PREFLIGHT_FAILED'; END $$"
        )


def upgrade() -> None:
    _preflight()
    op.add_column(
        "ingest_stage_attempts",
        sa.Column("lease_token", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "ingest_stage_attempts",
        sa.Column(
            "available_at",
            sa.DateTime(),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
    )
    op.add_column(
        "ingest_stage_attempts",
        sa.Column("output_digest", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_ingest_stage_attempts_lease_token",
        "ingest_stage_attempts",
        ["lease_token"],
        unique=False,
    )
    op.create_index(
        "ix_ingest_stage_attempts_claimable",
        "ingest_stage_attempts",
        ["available_at", "created_at"],
        unique=False,
        postgresql_where=sa.text("state IN ('queued','running')"),
    )
    op.drop_constraint(
        "ck_ingest_stage_attempts_attempts",
        "ingest_stage_attempts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ingest_stage_attempts_pipeline_kind",
        "ingest_stage_attempts",
        "pipeline_kind IN ('ingestion','rebuild')",
    )
    op.create_check_constraint(
        "ck_ingest_stage_attempts_stage",
        "ingest_stage_attempts",
        "stage IN ('parse','chunk','embed','authority_upsert')",
    )
    op.create_check_constraint(
        "ck_ingest_stage_attempts_state",
        "ingest_stage_attempts",
        "state IN ('queued','running','succeeded','failed','cancelled')",
    )
    op.create_check_constraint(
        "ck_ingest_stage_attempts_attempts_bounded",
        "ingest_stage_attempts",
        "attempts BETWEEN 0 AND 8",
    )
    op.create_check_constraint(
        "ck_ingest_stage_attempts_lease_fenced",
        "ingest_stage_attempts",
        "(state = 'running' AND lease_owner IS NOT NULL "
        "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
        "(state <> 'running' AND lease_owner IS NULL "
        "AND lease_token IS NULL AND lease_expires_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_ingest_stage_attempts_output_digest",
        "ingest_stage_attempts",
        "output_digest IS NULL OR output_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_ingest_stage_attempts_error_code_bounded",
        "ingest_stage_attempts",
        "error_code IS NULL OR char_length(error_code) BETWEEN 1 AND 100",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_ingest_stage_attempts_error_code_bounded",
        "ingest_stage_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_ingest_stage_attempts_output_digest",
        "ingest_stage_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_ingest_stage_attempts_lease_fenced",
        "ingest_stage_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_ingest_stage_attempts_attempts_bounded",
        "ingest_stage_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_ingest_stage_attempts_state",
        "ingest_stage_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_ingest_stage_attempts_stage",
        "ingest_stage_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_ingest_stage_attempts_pipeline_kind",
        "ingest_stage_attempts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ingest_stage_attempts_attempts",
        "ingest_stage_attempts",
        "attempts >= 0",
    )
    op.drop_index(
        "ix_ingest_stage_attempts_claimable",
        table_name="ingest_stage_attempts",
    )
    op.drop_index(
        "ix_ingest_stage_attempts_lease_token",
        table_name="ingest_stage_attempts",
    )
    op.drop_column("ingest_stage_attempts", "output_digest")
    op.drop_column("ingest_stage_attempts", "available_at")
    op.drop_column("ingest_stage_attempts", "lease_token")
