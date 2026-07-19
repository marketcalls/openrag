"""lease embedding deployments

Revision ID: e6b8d0f2a4c5
Revises: d5a7c9e1f3b4
Create Date: 2026-07-20 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6b8d0f2a4c5"
down_revision: str | Sequence[str] | None = "d5a7c9e1f3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "embedding_deployments",
        sa.Column("scan_cursor_document_version_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "embedding_deployments",
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "embedding_deployments",
        sa.Column("lease_token", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "embedding_deployments",
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "embedding_deployments",
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.alter_column("embedding_deployments", "attempts", server_default=None)
    op.create_check_constraint(
        "ck_embedding_deployments_attempts",
        "embedding_deployments",
        "attempts BETWEEN 0 AND 1000000",
    )
    op.create_check_constraint(
        "ck_embedding_deployments_lease",
        "embedding_deployments",
        "(lease_owner IS NULL AND lease_token IS NULL "
        "AND lease_expires_at IS NULL) OR "
        "(lease_owner IS NOT NULL AND lease_token IS NOT NULL "
        "AND lease_expires_at IS NOT NULL)",
    )
    op.create_index(
        op.f("ix_embedding_deployments_lease_token"),
        "embedding_deployments",
        ["lease_token"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE embedding_deployments IN ACCESS EXCLUSIVE MODE")
    )
    if connection.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM embedding_deployments "
            "WHERE scan_cursor_document_version_id IS NOT NULL "
            "OR lease_token IS NOT NULL OR attempts <> 0)"
        )
    ):
        raise RuntimeError(
            "embedding lease downgrade aborted: scanner state exists"
        )
    op.drop_index(
        op.f("ix_embedding_deployments_lease_token"),
        table_name="embedding_deployments",
    )
    op.drop_constraint(
        "ck_embedding_deployments_lease",
        "embedding_deployments",
        type_="check",
    )
    op.drop_constraint(
        "ck_embedding_deployments_attempts",
        "embedding_deployments",
        type_="check",
    )
    op.drop_column("embedding_deployments", "attempts")
    op.drop_column("embedding_deployments", "lease_expires_at")
    op.drop_column("embedding_deployments", "lease_token")
    op.drop_column("embedding_deployments", "lease_owner")
    op.drop_column("embedding_deployments", "scan_cursor_document_version_id")
