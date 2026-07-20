"""Add bounded evaluation automation policies and trigger provenance.

Revision ID: c6e8a0b2d4f5
Revises: b5d7f9a1c3e4
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6e8a0b2d4f5"
down_revision: str | None = "b5d7f9a1c3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("evaluator_model_id", sa.Uuid(), nullable=True),
        sa.Column("use_llm_judge", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("trigger_on_config_change", sa.Boolean(), nullable=False),
        sa.Column("interval_hours", sa.Integer(), nullable=False),
        sa.Column("max_cases", sa.Integer(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("max_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=False),
        sa.Column("last_enqueued_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "interval_hours BETWEEN 1 AND 720",
            name="ck_evaluation_policies_interval",
        ),
        sa.CheckConstraint(
            "max_cases BETWEEN 1 AND 10000 AND max_tokens BETWEEN 1 AND 50000000 "
            "AND max_cost_microusd BETWEEN 1 AND 100000000000",
            name="ck_evaluation_policies_budgets",
        ),
        sa.CheckConstraint(
            "(use_llm_judge AND evaluator_model_id IS NOT NULL) OR "
            "(NOT use_llm_judge AND evaluator_model_id IS NULL)",
            name="ck_evaluation_policies_judge",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(["evaluator_model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "dataset_id"],
            [
                "evaluation_datasets.org_id",
                "evaluation_datasets.workspace_id",
                "evaluation_datasets.id",
            ],
            name="fk_evaluation_policies_scope_dataset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_evaluation_policies_scope_creator",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_evaluation_policies_scope_id",
        ),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "dataset_id",
            name="uq_evaluation_policies_scope_dataset",
        ),
    )
    for column in (
        "org_id",
        "workspace_id",
        "dataset_id",
        "model_id",
        "enabled",
        "next_run_at",
    ):
        op.create_index(
            f"ix_evaluation_policies_{column}",
            "evaluation_policies",
            [column],
        )
    op.create_index(
        "ix_evaluation_policies_due",
        "evaluation_policies",
        ["enabled", "next_run_at", "id"],
    )

    op.add_column(
        "evaluation_runs",
        sa.Column("policy_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "trigger_kind",
            sa.String(length=20),
            server_default="manual",
            nullable=False,
        ),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("trigger_key", sa.String(length=120), nullable=True),
    )
    op.create_foreign_key(
        "fk_evaluation_runs_scope_policy",
        "evaluation_runs",
        "evaluation_policies",
        ["org_id", "workspace_id", "policy_id"],
        ["org_id", "workspace_id", "id"],
    )
    op.create_unique_constraint(
        "uq_evaluation_runs_policy_trigger",
        "evaluation_runs",
        ["policy_id", "trigger_key"],
    )
    op.create_check_constraint(
        "ck_evaluation_runs_trigger",
        "evaluation_runs",
        "(trigger_kind = 'manual' AND policy_id IS NULL AND trigger_key IS NULL) OR "
        "(trigger_kind IN ('scheduled','config_change') AND policy_id IS NOT NULL "
        "AND trigger_key IS NOT NULL)",
    )
    op.create_index("ix_evaluation_runs_policy_id", "evaluation_runs", ["policy_id"])
    op.create_index(
        "ix_evaluation_runs_trigger_kind",
        "evaluation_runs",
        ["trigger_kind"],
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_runs_trigger_kind", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_policy_id", table_name="evaluation_runs")
    op.drop_constraint(
        "ck_evaluation_runs_trigger",
        "evaluation_runs",
        type_="check",
    )
    op.drop_constraint(
        "uq_evaluation_runs_policy_trigger",
        "evaluation_runs",
        type_="unique",
    )
    op.drop_constraint(
        "fk_evaluation_runs_scope_policy",
        "evaluation_runs",
        type_="foreignkey",
    )
    op.drop_column("evaluation_runs", "trigger_key")
    op.drop_column("evaluation_runs", "trigger_kind")
    op.drop_column("evaluation_runs", "policy_id")

    op.drop_index("ix_evaluation_policies_due", table_name="evaluation_policies")
    for column in reversed(
        (
            "org_id",
            "workspace_id",
            "dataset_id",
            "model_id",
            "enabled",
            "next_run_at",
        )
    ):
        op.drop_index(
            f"ix_evaluation_policies_{column}",
            table_name="evaluation_policies",
        )
    op.drop_table("evaluation_policies")
