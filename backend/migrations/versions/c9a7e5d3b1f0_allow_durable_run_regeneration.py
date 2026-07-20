"""allow durable run regeneration

Revision ID: c9a7e5d3b1f0
Revises: f8a1c3d5e7b9
Create Date: 2026-07-20 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c9a7e5d3b1f0"
down_revision: str | Sequence[str] | None = "f8a1c3d5e7b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "agent_runs_input_message_id_key",
        "agent_runs",
        type_="unique",
    )
    op.create_index(
        op.f("ix_agent_runs_input_message_id"),
        "agent_runs",
        ["input_message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_agent_runs_input_message_id"),
        table_name="agent_runs",
    )
    op.create_unique_constraint(
        "agent_runs_input_message_id_key",
        "agent_runs",
        ["input_message_id"],
    )
