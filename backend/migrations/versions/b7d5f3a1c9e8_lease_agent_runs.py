"""lease agent runs

Revision ID: b7d5f3a1c9e8
Revises: c9a7e5d3b1f0
Create Date: 2026-07-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d5f3a1c9e8"
down_revision: str | Sequence[str] | None = "c9a7e5d3b1f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("lease_owner", sa.String(), nullable=True))
    op.add_column("agent_runs", sa.Column("lease_token", sa.Uuid(), nullable=True))
    op.add_column("agent_runs", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_agent_runs_attempts",
        "agent_runs",
        "attempts BETWEEN 0 AND 1000",
    )
    op.create_check_constraint(
        "ck_agent_runs_lease",
        "agent_runs",
        "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
        "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
        "AND lease_expires_at IS NOT NULL)",
    )
    op.create_index(
        op.f("ix_agent_runs_lease_token"),
        "agent_runs",
        ["lease_token"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_runs_lease_expires_at"),
        "agent_runs",
        ["lease_expires_at"],
        unique=False,
    )
    op.alter_column("agent_runs", "attempts", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_runs_lease_expires_at"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_lease_token"), table_name="agent_runs")
    op.drop_constraint("ck_agent_runs_lease", "agent_runs", type_="check")
    op.drop_constraint("ck_agent_runs_attempts", "agent_runs", type_="check")
    op.drop_column("agent_runs", "attempts")
    op.drop_column("agent_runs", "lease_expires_at")
    op.drop_column("agent_runs", "lease_token")
    op.drop_column("agent_runs", "lease_owner")
