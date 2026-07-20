"""run context ledger

Revision ID: a6c8e1f3b5d7
Revises: f4b7d9e2a5c3
Create Date: 2026-07-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6c8e1f3b5d7"
down_revision: str | Sequence[str] | None = "f4b7d9e2a5c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_agent_runs_org_workspace_id",
        "agent_runs",
        ["org_id", "workspace_id", "id"],
    )
    op.create_table(
        "run_context_ledgers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("route", sa.String(length=32), nullable=False),
        sa.Column("budget_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("memory_tokens", sa.Integer(), nullable=False),
        sa.Column("memory_items", sa.Integer(), nullable=False),
        sa.Column("history_tokens", sa.Integer(), nullable=False),
        sa.Column("history_messages", sa.Integer(), nullable=False),
        sa.Column("retrieval_tokens", sa.Integer(), nullable=False),
        sa.Column("retrieval_items", sa.Integer(), nullable=False),
        sa.Column("selection_digest", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "attempt BETWEEN 1 AND 1000",
            name="ck_run_context_attempt",
        ),
        sa.CheckConstraint(
            "budget_tokens > 0 AND estimated_prompt_tokens >= 0 "
            "AND memory_tokens >= 0 AND history_tokens >= 0 "
            "AND retrieval_tokens >= 0",
            name="ck_run_context_token_counts",
        ),
        sa.CheckConstraint(
            "memory_items BETWEEN 0 AND 8 AND history_messages >= 0 "
            "AND retrieval_items >= 0",
            name="ck_run_context_item_counts",
        ),
        sa.CheckConstraint(
            "char_length(selection_digest) = 64",
            name="ck_run_context_selection_digest",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "run_id"],
            ["agent_runs.org_id", "agent_runs.workspace_id", "agent_runs.id"],
            name="fk_run_context_ledgers_org_workspace_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "run_id",
            "attempt",
            name="uq_run_context_ledgers_attempt",
        ),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_run_context_ledgers_org_workspace_id",
        ),
    )
    op.create_index("ix_run_context_ledgers_org_id", "run_context_ledgers", ["org_id"])
    op.create_index(
        "ix_run_context_ledgers_workspace_id",
        "run_context_ledgers",
        ["workspace_id"],
    )
    op.create_index("ix_run_context_ledgers_run_id", "run_context_ledgers", ["run_id"])

    op.create_table(
        "run_memory_selections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("ledger_id", sa.UUID(), nullable=False),
        sa.Column("memory_id", sa.UUID(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "rank BETWEEN 1 AND 8",
            name="ck_run_memory_selection_rank",
        ),
        sa.CheckConstraint(
            "estimated_tokens > 0",
            name="ck_run_memory_selection_tokens",
        ),
        sa.CheckConstraint(
            "char_length(content_hash) = 64",
            name="ck_run_memory_selection_content_hash",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "ledger_id"],
            [
                "run_context_ledgers.org_id",
                "run_context_ledgers.workspace_id",
                "run_context_ledgers.id",
            ],
            name="fk_run_memory_selection_org_workspace_ledger",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "memory_id"],
            ["memory_records.org_id", "memory_records.workspace_id", "memory_records.id"],
            name="fk_run_memory_selection_org_workspace_memory",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ledger_id",
            "rank",
            name="uq_run_memory_selection_rank",
        ),
        sa.UniqueConstraint(
            "ledger_id",
            "memory_id",
            name="uq_run_memory_selection_memory",
        ),
    )
    op.create_index("ix_run_memory_selections_org_id", "run_memory_selections", ["org_id"])
    op.create_index(
        "ix_run_memory_selections_workspace_id",
        "run_memory_selections",
        ["workspace_id"],
    )
    op.create_index(
        "ix_run_memory_selections_ledger_id",
        "run_memory_selections",
        ["ledger_id"],
    )
    op.create_index(
        "ix_run_memory_selections_memory_id",
        "run_memory_selections",
        ["memory_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_memory_selections_memory_id", table_name="run_memory_selections")
    op.drop_index("ix_run_memory_selections_ledger_id", table_name="run_memory_selections")
    op.drop_index("ix_run_memory_selections_workspace_id", table_name="run_memory_selections")
    op.drop_index("ix_run_memory_selections_org_id", table_name="run_memory_selections")
    op.drop_table("run_memory_selections")
    op.drop_index("ix_run_context_ledgers_run_id", table_name="run_context_ledgers")
    op.drop_index("ix_run_context_ledgers_workspace_id", table_name="run_context_ledgers")
    op.drop_index("ix_run_context_ledgers_org_id", table_name="run_context_ledgers")
    op.drop_table("run_context_ledgers")
    op.drop_constraint(
        "uq_agent_runs_org_workspace_id",
        "agent_runs",
        type_="unique",
    )
