"""Add immutable, tenant-scoped analytics message artifacts.

Revision ID: a7c9e1f3b5d8
Revises: f5a7b9c1d3e4
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c9e1f3b5d8"
down_revision: str | None = "f5a7b9c1d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "message_artifacts",
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind = 'analytics'",
            name="ck_message_artifacts_kind",
        ),
        sa.CheckConstraint(
            "schema_version = 'analytics.v1'",
            name="ck_message_artifacts_schema_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object' "
            "AND payload->>'schema_version' = schema_version "
            "AND pg_column_size(payload) <= 49152",
            name="ck_message_artifacts_payload",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_message_artifacts_content_hash",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "message_id"],
            ["messages.org_id", "messages.workspace_id", "messages.id"],
            name="fk_message_artifacts_org_workspace_message",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "message_id",
            "kind",
            name="uq_message_artifacts_message_kind",
        ),
    )
    op.create_index(
        "ix_message_artifacts_message",
        "message_artifacts",
        ["org_id", "workspace_id", "message_id"],
    )
    for column in ("org_id", "workspace_id", "message_id"):
        op.create_index(
            f"ix_message_artifacts_{column}",
            "message_artifacts",
            [column],
        )

    op.execute(
        """
        CREATE FUNCTION openrag_reject_message_artifact_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'message artifact is immutable' USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_message_artifacts_immutable
        BEFORE UPDATE ON message_artifacts
        FOR EACH ROW EXECUTE FUNCTION openrag_reject_message_artifact_update()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_message_artifacts_immutable ON message_artifacts"
    )
    op.execute("DROP FUNCTION IF EXISTS openrag_reject_message_artifact_update()")
    op.drop_table("message_artifacts")
