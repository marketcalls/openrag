"""embedding profiles

Revision ID: b3e5f7a9c1d2
Revises: a2d4f6b8c0e1
Create Date: 2026-07-20 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e5f7a9c1d2"
down_revision: str | Sequence[str] | None = "a2d4f6b8c0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "embedding_profiles",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("name_key", sa.String(length=120), nullable=False),
        sa.Column("provider_kind", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("max_input_tokens", sa.Integer(), nullable=False),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "batch_size BETWEEN 1 AND 1024",
            name="ck_embedding_profiles_batch_size",
        ),
        sa.CheckConstraint(
            "config_digest ~ '^[0-9a-f]{64}$'",
            name="ck_embedding_profiles_config_digest",
        ),
        sa.CheckConstraint(
            "dimension BETWEEN 1 AND 32768",
            name="ck_embedding_profiles_dimension",
        ),
        sa.CheckConstraint(
            "max_input_tokens BETWEEN 1 AND 2000000",
            name="ck_embedding_profiles_max_input_tokens",
        ),
        sa.CheckConstraint(
            "provider_kind IN ('litellm','tei','hash')",
            name="ck_embedding_profiles_provider_kind",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("config_digest"),
        sa.UniqueConstraint("name_key", name="uq_embedding_profiles_name_key"),
    )
    op.create_index(
        op.f("ix_embedding_profiles_enabled"),
        "embedding_profiles",
        ["enabled"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE embedding_profiles IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM embedding_profiles)")):
        raise RuntimeError(
            "embedding profile downgrade aborted: governed profiles exist"
        )
    op.drop_index(
        op.f("ix_embedding_profiles_enabled"),
        table_name="embedding_profiles",
    )
    op.drop_table("embedding_profiles")
