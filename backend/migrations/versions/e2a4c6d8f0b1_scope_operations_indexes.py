"""Add composite tenant-time indexes for bounded operations reads.

Revision ID: e2a4c6d8f0b1
Revises: d9f1b4c6e8a0
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e2a4c6d8f0b1"
down_revision: str | Sequence[str] | None = "d9f1b4c6e8a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_rag_run_facts_org_workspace_time",
            "rag_run_facts",
            ["org_id", "workspace_id", "accepted_at", "id"],
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_error_occurrences_org_workspace_time",
            "error_occurrences",
            ["org_id", "workspace_id", "occurred_at", "issue_id", "id"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_error_occurrences_org_workspace_time",
            table_name="error_occurrences",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_rag_run_facts_org_workspace_time",
            table_name="rag_run_facts",
            postgresql_concurrently=True,
        )
