"""Add content-free durable audits for released grounded answers.

Revision ID: d1e3f5a7b9c2
Revises: c7b9d1e3f5a6
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e3f5a7b9c2"
down_revision: str | None = "c7b9d1e3f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_quality_audits",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("grounding_policy_id", sa.Uuid(), nullable=False),
        sa.Column("grounding_policy_version", sa.Integer(), nullable=False),
        sa.Column("verifier_model_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("grounding_score", sa.Float(), nullable=True),
        sa.Column("completeness_score", sa.Float(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("result_code", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','skipped')",
            name="ck_answer_quality_audits_status",
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND 20",
            name="ck_answer_quality_audits_attempts",
        ),
        sa.CheckConstraint(
            "grounding_policy_version > 0",
            name="ck_answer_quality_audits_policy_version",
        ),
        sa.CheckConstraint(
            "grounding_score IS NULL OR grounding_score BETWEEN 0 AND 1",
            name="ck_answer_quality_audits_grounding_score",
        ),
        sa.CheckConstraint(
            "completeness_score IS NULL OR completeness_score BETWEEN 0 AND 1",
            name="ck_answer_quality_audits_completeness_score",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_answer_quality_audits_lease",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND grounding_score IS NOT NULL "
            "AND completeness_score IS NOT NULL AND passed IS NOT NULL "
            "AND result_code IS NOT NULL) OR "
            "(status <> 'completed' AND grounding_score IS NULL "
            "AND completeness_score IS NULL AND passed IS NULL "
            "AND result_code IS NULL)",
            name="ck_answer_quality_audits_terminal_scores",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["grounding_policy_id"],
            ["grounding_policies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["verifier_model_id"],
            ["models.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "message_id"],
            ["messages.org_id", "messages.workspace_id", "messages.id"],
            name="fk_answer_quality_audits_scope_message",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            name="uq_answer_quality_audits_message",
        ),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_answer_quality_audits_scope_id",
        ),
    )
    for column in (
        "org_id",
        "workspace_id",
        "message_id",
        "grounding_policy_id",
        "verifier_model_id",
        "status",
        "lease_token",
        "lease_expires_at",
    ):
        op.create_index(
            f"ix_answer_quality_audits_{column}",
            "answer_quality_audits",
            [column],
        )
    op.create_index(
        "ix_answer_quality_audits_claim",
        "answer_quality_audits",
        ["status", "lease_expires_at", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("answer_quality_audits")
