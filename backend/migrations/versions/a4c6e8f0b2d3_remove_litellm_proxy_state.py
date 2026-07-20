"""Remove proxy state and add governed embedding provider endpoints.

Revision ID: a4c6e8f0b2d3
Revises: f3b5d7e9a1c2
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c6e8f0b2d3"
down_revision: str | None = "f3b5d7e9a1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("models", "sync_status")
    op.add_column(
        "embedding_profiles",
        sa.Column("base_url", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("embedding_profiles", "base_url")
    op.add_column(
        "models",
        sa.Column(
            "sync_status",
            sa.String(),
            nullable=False,
            server_default="pending",
        ),
    )
