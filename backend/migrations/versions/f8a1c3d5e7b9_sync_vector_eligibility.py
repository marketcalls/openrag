"""sync vector eligibility

Revision ID: f8a1c3d5e7b9
Revises: e6b8d0f2a4c5
Create Date: 2026-07-20 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8a1c3d5e7b9"
down_revision: str | Sequence[str] | None = "e6b8d0f2a4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_version_projections",
        sa.Column("sync_state", sa.String(length=16), server_default="queued", nullable=False),
    )
    op.add_column(
        "document_version_projections",
        sa.Column("sync_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "document_version_projections",
        sa.Column(
            "sync_available_at",
            sa.DateTime(),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
    )
    op.add_column(
        "document_version_projections",
        sa.Column("sync_lease_owner", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "document_version_projections",
        sa.Column("sync_lease_token", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "document_version_projections",
        sa.Column("sync_lease_expires_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "document_version_projections",
        sa.Column("sync_error_code", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "document_version_projections",
        sa.Column("vector_applied_generation_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "document_version_projections",
        sa.Column("vector_applied_revision", sa.Integer(), nullable=True),
    )
    op.add_column(
        "document_version_projections",
        sa.Column("vector_applied_at", sa.DateTime(), nullable=True),
    )
    op.alter_column("document_version_projections", "sync_state", server_default=None)
    op.alter_column("document_version_projections", "sync_attempts", server_default=None)
    op.alter_column("document_version_projections", "sync_available_at", server_default=None)
    op.create_check_constraint(
        "ck_document_version_projections_sync_state",
        "document_version_projections",
        "sync_state IN ('queued','leased','retry','applied','failed')",
    )
    op.create_check_constraint(
        "ck_document_version_projections_sync_attempts",
        "document_version_projections",
        "sync_attempts BETWEEN 0 AND 1000000",
    )
    op.create_check_constraint(
        "ck_document_version_projections_sync_lease",
        "document_version_projections",
        "(sync_lease_owner IS NULL AND sync_lease_token IS NULL "
        "AND sync_lease_expires_at IS NULL) OR "
        "(sync_lease_owner IS NOT NULL AND sync_lease_token IS NOT NULL "
        "AND sync_lease_expires_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_document_version_projections_vector_revision",
        "document_version_projections",
        "vector_applied_revision IS NULL OR vector_applied_revision >= 1",
    )
    op.create_index(
        op.f("ix_document_version_projections_sync_state"),
        "document_version_projections",
        ["sync_state"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_version_projections_sync_available_at"),
        "document_version_projections",
        ["sync_available_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_version_projections_sync_lease_token"),
        "document_version_projections",
        ["sync_lease_token"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE document_version_projections IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM document_version_projections "
            "WHERE sync_state <> 'queued' OR sync_attempts <> 0 "
            "OR vector_applied_revision IS NOT NULL)"
        )
    ):
        raise RuntimeError("vector eligibility downgrade aborted: sync state exists")
    for column in (
        "sync_lease_token",
        "sync_available_at",
        "sync_state",
    ):
        op.drop_index(
            op.f(f"ix_document_version_projections_{column}"),
            table_name="document_version_projections",
        )
    for constraint in (
        "ck_document_version_projections_vector_revision",
        "ck_document_version_projections_sync_lease",
        "ck_document_version_projections_sync_attempts",
        "ck_document_version_projections_sync_state",
    ):
        op.drop_constraint(
            constraint,
            "document_version_projections",
            type_="check",
        )
    for column in (
        "vector_applied_at",
        "vector_applied_revision",
        "vector_applied_generation_id",
        "sync_error_code",
        "sync_lease_expires_at",
        "sync_lease_token",
        "sync_lease_owner",
        "sync_available_at",
        "sync_attempts",
        "sync_state",
    ):
        op.drop_column("document_version_projections", column)
