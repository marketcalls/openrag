"""unique active memory keys

Revision ID: f4b7d9e2a5c3
Revises: e3a6c8f1b4d2
Create Date: 2026-07-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4b7d9e2a5c3"
down_revision: str | Sequence[str] | None = "e3a6c8f1b4d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_memory_records_active_key",
        "memory_records",
        ["org_id", "workspace_id", "user_id", "canonical_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_memory_records_active_key",
        table_name="memory_records",
    )
