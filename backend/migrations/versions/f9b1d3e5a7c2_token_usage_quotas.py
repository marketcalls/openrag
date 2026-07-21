"""Add organization and user token quotas.

Revision ID: f9b1d3e5a7c2
Revises: a7c9e1f3b5d8
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f9b1d3e5a7c2"
down_revision: str | None = "a7c9e1f3b5d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_quotas",
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("monthly_tokens", sa.Integer(), nullable=False),
        sa.Column("default_user_monthly_tokens", sa.Integer(), nullable=True),
        sa.Column("reset_day", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "monthly_tokens >= 0",
            name="ck_org_quotas_monthly_tokens",
        ),
        sa.CheckConstraint(
            "default_user_monthly_tokens IS NULL "
            "OR default_user_monthly_tokens >= 0",
            name="ck_org_quotas_default_user_tokens",
        ),
        sa.CheckConstraint(
            "reset_day BETWEEN 1 AND 31",
            name="ck_org_quotas_reset_day",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("org_id"),
    )
    op.create_table(
        "user_quotas",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("monthly_tokens", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "monthly_tokens >= 0",
            name="ck_user_quotas_monthly_tokens",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_user_quotas_org_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint(
            "org_id",
            "user_id",
            name="uq_user_quotas_org_user",
        ),
    )
    op.create_index(
        "ix_agent_runs_user_accepted",
        "agent_runs",
        ["user_id", "accepted_at"],
    )
    op.create_index(
        "ix_agent_runs_org_accepted",
        "agent_runs",
        ["org_id", "accepted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_org_accepted", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_accepted", table_name="agent_runs")
    op.drop_table("user_quotas")
    op.drop_table("org_quotas")
