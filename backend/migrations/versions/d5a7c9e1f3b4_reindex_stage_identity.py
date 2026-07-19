"""reindex stage identity

Revision ID: d5a7c9e1f3b4
Revises: c4f6a8b0d2e3
Create Date: 2026-07-20 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a7c9e1f3b4"
down_revision: str | Sequence[str] | None = "c4f6a8b0d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingest_stage_attempts",
        sa.Column("embedding_deployment_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "ingest_stage_attempts",
        sa.Column("embedding_profile_version", sa.String(length=100), nullable=True),
    )
    op.create_foreign_key(
        "fk_ingest_stage_attempts_embedding_deployment",
        "ingest_stage_attempts",
        "embedding_deployments",
        ["embedding_deployment_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_ingest_stage_attempts_embedding_deployment_id"),
        "ingest_stage_attempts",
        ["embedding_deployment_id"],
        unique=False,
    )
    op.drop_constraint(
        "ck_ingest_stage_attempts_pipeline_kind",
        "ingest_stage_attempts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ingest_stage_attempts_pipeline_kind",
        "ingest_stage_attempts",
        "pipeline_kind IN ('ingestion','rebuild','reindex')",
    )
    op.create_check_constraint(
        "ck_ingest_stage_attempts_reindex_identity",
        "ingest_stage_attempts",
        "(pipeline_kind = 'reindex' "
        "AND embedding_deployment_id IS NOT NULL "
        "AND embedding_profile_version IS NOT NULL) OR "
        "(pipeline_kind <> 'reindex' "
        "AND embedding_deployment_id IS NULL "
        "AND embedding_profile_version IS NULL)",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE ingest_stage_attempts IN ACCESS EXCLUSIVE MODE")
    )
    if connection.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM ingest_stage_attempts "
            "WHERE pipeline_kind = 'reindex')"
        )
    ):
        raise RuntimeError(
            "reindex stage downgrade aborted: governed attempts exist"
        )
    op.drop_constraint(
        "ck_ingest_stage_attempts_reindex_identity",
        "ingest_stage_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_ingest_stage_attempts_pipeline_kind",
        "ingest_stage_attempts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ingest_stage_attempts_pipeline_kind",
        "ingest_stage_attempts",
        "pipeline_kind IN ('ingestion','rebuild')",
    )
    op.drop_index(
        op.f("ix_ingest_stage_attempts_embedding_deployment_id"),
        table_name="ingest_stage_attempts",
    )
    op.drop_constraint(
        "fk_ingest_stage_attempts_embedding_deployment",
        "ingest_stage_attempts",
        type_="foreignkey",
    )
    op.drop_column("ingest_stage_attempts", "embedding_profile_version")
    op.drop_column("ingest_stage_attempts", "embedding_deployment_id")
