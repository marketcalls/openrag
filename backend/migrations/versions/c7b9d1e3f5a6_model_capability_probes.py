"""Add revision-fenced measured model capability probes.

Revision ID: c7b9d1e3f5a6
Revises: c6e8a0b2d4f5
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7b9d1e3f5a6"
down_revision: str | None = "c6e8a0b2d4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column("supports_streaming", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "models",
        sa.Column("supports_tools", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "models",
        sa.Column("supports_vision", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("models", sa.Column("context_window", sa.Integer(), nullable=True))
    op.add_column(
        "models",
        sa.Column(
            "probe_status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
    )
    op.add_column(
        "models",
        sa.Column("probe_revision", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("models", sa.Column("probe_latency_ms", sa.Integer(), nullable=True))
    op.add_column(
        "models",
        sa.Column("last_probe_error_code", sa.String(length=64), nullable=True),
    )
    op.add_column("models", sa.Column("last_probed_at", sa.DateTime(), nullable=True))
    op.execute(
        "UPDATE models SET supports_chat_completion=false, "
        "supports_structured_json=false, supports_verifier=false, "
        "supports_reasoning=false, default_reasoning_effort='off'"
    )
    op.create_check_constraint(
        "ck_models_probe_status",
        "models",
        "probe_status IN ('pending','passed','failed')",
    )
    op.create_check_constraint(
        "ck_models_probe_revision",
        "models",
        "probe_revision > 0",
    )
    op.create_check_constraint(
        "ck_models_measured_capabilities",
        "models",
        "(probe_status = 'passed' AND supports_chat_completion AND supports_streaming) OR "
        "(probe_status IN ('pending','failed') AND NOT supports_chat_completion "
        "AND NOT supports_streaming AND NOT supports_structured_json "
        "AND NOT supports_verifier AND NOT supports_tools AND NOT supports_vision "
        "AND NOT supports_reasoning)",
    )
    op.create_check_constraint(
        "ck_models_context_window",
        "models",
        "context_window IS NULL OR context_window BETWEEN 1 AND 10000000",
    )
    op.create_check_constraint(
        "ck_models_probe_latency",
        "models",
        "probe_latency_ms IS NULL OR probe_latency_ms BETWEEN 0 AND 120000",
    )
    op.create_index("ix_models_probe_status", "models", ["probe_status"])

    op.create_table(
        "model_probes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("supports_chat_completion", sa.Boolean(), nullable=False),
        sa.Column("supports_streaming", sa.Boolean(), nullable=False),
        sa.Column("supports_structured_json", sa.Boolean(), nullable=False),
        sa.Column("supports_tools", sa.Boolean(), nullable=False),
        sa.Column("supports_vision", sa.Boolean(), nullable=False),
        sa.Column("supports_reasoning", sa.Boolean(), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_model_probes_revision"),
        sa.CheckConstraint(
            "status IN ('queued','running','passed','failed','stale')",
            name="ck_model_probes_status",
        ),
        sa.CheckConstraint(
            "configuration_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_model_probes_configuration_fingerprint",
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND 10",
            name="ck_model_probes_attempts",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_model_probes_lease",
        ),
        sa.CheckConstraint(
            "context_window IS NULL OR context_window BETWEEN 1 AND 10000000",
            name="ck_model_probes_context_window",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms BETWEEN 0 AND 120000",
            name="ck_model_probes_latency",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["models.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_id", "revision", name="uq_model_probes_revision"),
    )
    op.create_index("ix_model_probes_model_id", "model_probes", ["model_id"])
    op.create_index("ix_model_probes_status", "model_probes", ["status"])
    op.create_index(
        "ix_model_probes_claim",
        "model_probes",
        ["status", "lease_expires_at", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_probes_claim", table_name="model_probes")
    op.drop_index("ix_model_probes_status", table_name="model_probes")
    op.drop_index("ix_model_probes_model_id", table_name="model_probes")
    op.drop_table("model_probes")
    op.drop_index("ix_models_probe_status", table_name="models")
    for name in (
        "ck_models_probe_latency",
        "ck_models_context_window",
        "ck_models_measured_capabilities",
        "ck_models_probe_revision",
        "ck_models_probe_status",
    ):
        op.drop_constraint(name, "models", type_="check")
    for column in (
        "last_probed_at",
        "last_probe_error_code",
        "probe_latency_ms",
        "probe_revision",
        "probe_status",
        "context_window",
        "supports_vision",
        "supports_tools",
        "supports_streaming",
    ):
        op.drop_column("models", column)
