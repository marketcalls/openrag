"""add reasoning effort controls

Revision ID: f3b5d7e9a1c2
Revises: e2a4c6d8f0b1
Create Date: 2026-07-20 14:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3b5d7e9a1c2"
down_revision: str | None = "e2a4c6d8f0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column(
            "supports_reasoning",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "models",
        sa.Column(
            "default_reasoning_effort",
            sa.String(length=16),
            nullable=False,
            server_default="off",
        ),
    )
    op.create_check_constraint(
        "ck_models_default_reasoning_effort",
        "models",
        "default_reasoning_effort IN ('off','low','medium','high') "
        "AND (supports_reasoning OR default_reasoning_effort = 'off')",
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "reasoning_effort",
            sa.String(length=16),
            nullable=False,
            server_default="off",
        ),
    )
    op.create_check_constraint(
        "ck_agent_runs_reasoning_effort",
        "agent_runs",
        "reasoning_effort IN ('off','low','medium','high')",
    )
    op.alter_column("models", "supports_reasoning", server_default=None)
    op.alter_column("models", "default_reasoning_effort", server_default=None)
    op.alter_column("agent_runs", "reasoning_effort", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "ck_agent_runs_reasoning_effort",
        "agent_runs",
        type_="check",
    )
    op.drop_column("agent_runs", "reasoning_effort")
    op.drop_constraint(
        "ck_models_default_reasoning_effort",
        "models",
        type_="check",
    )
    op.drop_column("models", "default_reasoning_effort")
    op.drop_column("models", "supports_reasoning")
