"""Designate one measured utility model for background AI work.

Revision ID: e4f6a8c0d2b3
Revises: d1e3f5a7b9c2
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4f6a8c0d2b3"
down_revision: str | None = "d1e3f5a7b9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column(
            "is_utility",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_models_utility_measured",
        "models",
        "NOT is_utility OR (enabled AND probe_status = 'passed' "
        "AND supports_chat_completion AND supports_streaming)",
    )
    op.create_index("ix_models_is_utility", "models", ["is_utility"])
    op.create_index(
        "uq_models_single_utility",
        "models",
        ["is_utility"],
        unique=True,
        postgresql_where=sa.text("is_utility"),
    )


def downgrade() -> None:
    op.drop_index("uq_models_single_utility", table_name="models")
    op.drop_index("ix_models_is_utility", table_name="models")
    op.drop_constraint("ck_models_utility_measured", "models", type_="check")
    op.drop_column("models", "is_utility")
