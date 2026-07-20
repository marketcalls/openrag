"""govern user memory

Revision ID: e3a6c8f1b4d2
Revises: d1f9b7e5c3a2
Create Date: 2026-07-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e3a6c8f1b4d2"
down_revision: str | Sequence[str] | None = "d1f9b7e5c3a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_pk_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "memory_records",
        *_uuid_pk_columns(),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("client_request_id", sa.UUID(), nullable=False),
        sa.Column("canonical_key", sa.String(length=120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("sensitivity", sa.String(length=32), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("conflict_group", sa.String(length=120), nullable=True),
        sa.Column("superseded_by_id", sa.UUID(), nullable=True),
        sa.Column("policy_version", sa.String(length=100), nullable=False),
        sa.Column("model_version", sa.String(length=200), nullable=True),
        sa.Column("source_trust", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("suppression_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "memory_type IN ('semantic','episodic','procedural')",
            name="ck_memory_records_type",
        ),
        sa.CheckConstraint(
            "scope IN ('user_workspace','user_org','workspace_shared')",
            name="ck_memory_records_scope",
        ),
        sa.CheckConstraint(
            "status IN ('candidate','active','conflicted','superseded',"
            "'retracted','expired','quarantined')",
            name="ck_memory_records_status",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('public','internal','confidential','restricted')",
            name="ck_memory_records_sensitivity",
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1 AND importance BETWEEN 0 AND 1",
            name="ck_memory_records_scores",
        ),
        sa.CheckConstraint(
            "char_length(content_hash) = 64 AND "
            "char_length(suppression_fingerprint) = 64",
            name="ck_memory_records_hashes",
        ),
        sa.CheckConstraint(
            "char_length(canonical_key) BETWEEN 1 AND 120 "
            "AND char_length(content) BETWEEN 1 AND 4000 "
            "AND char_length(policy_version) BETWEEN 1 AND 100 "
            "AND (model_version IS NULL OR char_length(model_version) BETWEEN 1 AND 200)",
            name="ck_memory_records_strings_bounded",
        ),
        sa.CheckConstraint(
            "structured_value IS NULL OR (jsonb_typeof(structured_value) = 'object' "
            "AND pg_column_size(structured_value) <= 8192)",
            name="ck_memory_records_structured_value",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_memory_records_org_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_memory_records_org_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_memory_records_org_workspace_id",
        ),
        sa.UniqueConstraint(
            "org_id",
            "user_id",
            "client_request_id",
            name="uq_memory_records_user_request",
        ),
    )
    op.create_foreign_key(
        "fk_memory_records_same_workspace_successor",
        "memory_records",
        "memory_records",
        ["org_id", "workspace_id", "superseded_by_id"],
        ["org_id", "workspace_id", "id"],
    )
    op.create_index("ix_memory_records_org_id", "memory_records", ["org_id"])
    op.create_index("ix_memory_records_workspace_id", "memory_records", ["workspace_id"])
    op.create_index("ix_memory_records_user_id", "memory_records", ["user_id"])
    op.create_index("ix_memory_records_status", "memory_records", ["status"])
    op.create_index("ix_memory_records_expires_at", "memory_records", ["expires_at"])
    op.create_index(
        "ix_memory_records_active_selection",
        "memory_records",
        ["org_id", "workspace_id", "user_id", "status", "updated_at"],
    )

    op.create_table(
        "memory_provenance",
        *_uuid_pk_columns(),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("memory_id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_event_id", sa.UUID(), nullable=False),
        sa.Column("source_message_id", sa.UUID(), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "source_kind IN ('explicit_user_action','user_message',"
            "'verified_event','approved_procedure')",
            name="ck_memory_provenance_source_kind",
        ),
        sa.CheckConstraint(
            "char_length(source_hash) = 64",
            name="ck_memory_provenance_source_hash",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "memory_id"],
            ["memory_records.org_id", "memory_records.workspace_id", "memory_records.id"],
            name="fk_memory_provenance_org_workspace_memory",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "actor_user_id"],
            ["users.org_id", "users.id"],
            name="fk_memory_provenance_org_actor",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "source_message_id"],
            ["messages.org_id", "messages.workspace_id", "messages.id"],
            name="fk_memory_provenance_org_workspace_message",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "actor_user_id",
            "source_event_id",
            name="uq_memory_provenance_actor_event",
        ),
    )
    op.create_index("ix_memory_provenance_org_id", "memory_provenance", ["org_id"])
    op.create_index("ix_memory_provenance_memory_id", "memory_provenance", ["memory_id"])
    op.create_index(
        "ix_memory_provenance_actor_user_id",
        "memory_provenance",
        ["actor_user_id"],
    )

    op.create_table(
        "memory_suppressions",
        *_uuid_pk_columns(),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "char_length(fingerprint) = 64",
            name="ck_memory_suppressions_fingerprint",
        ),
        sa.CheckConstraint(
            "char_length(reason) BETWEEN 1 AND 64",
            name="ck_memory_suppressions_reason",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_memory_suppressions_org_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_memory_suppressions_org_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "user_id",
            "fingerprint",
            name="uq_memory_suppressions_scope_fingerprint",
        ),
    )
    op.create_index("ix_memory_suppressions_org_id", "memory_suppressions", ["org_id"])
    op.create_index(
        "ix_memory_suppressions_workspace_id",
        "memory_suppressions",
        ["workspace_id"],
    )
    op.create_index("ix_memory_suppressions_user_id", "memory_suppressions", ["user_id"])

    op.create_table(
        "memory_preferences",
        *_uuid_pk_columns(),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("extraction_enabled", sa.Boolean(), nullable=False),
        sa.Column("semantic_enabled", sa.Boolean(), nullable=False),
        sa.Column("episodic_enabled", sa.Boolean(), nullable=False),
        sa.Column("procedural_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_memory_preferences_org_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_memory_preferences_org_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "user_id",
            name="uq_memory_preferences_scope",
        ),
    )
    op.create_index("ix_memory_preferences_org_id", "memory_preferences", ["org_id"])
    op.create_index(
        "ix_memory_preferences_workspace_id",
        "memory_preferences",
        ["workspace_id"],
    )
    op.create_index("ix_memory_preferences_user_id", "memory_preferences", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_preferences_user_id", table_name="memory_preferences")
    op.drop_index("ix_memory_preferences_workspace_id", table_name="memory_preferences")
    op.drop_index("ix_memory_preferences_org_id", table_name="memory_preferences")
    op.drop_table("memory_preferences")
    op.drop_index("ix_memory_suppressions_user_id", table_name="memory_suppressions")
    op.drop_index("ix_memory_suppressions_workspace_id", table_name="memory_suppressions")
    op.drop_index("ix_memory_suppressions_org_id", table_name="memory_suppressions")
    op.drop_table("memory_suppressions")
    op.drop_index("ix_memory_provenance_actor_user_id", table_name="memory_provenance")
    op.drop_index("ix_memory_provenance_memory_id", table_name="memory_provenance")
    op.drop_index("ix_memory_provenance_org_id", table_name="memory_provenance")
    op.drop_table("memory_provenance")
    op.drop_index("ix_memory_records_active_selection", table_name="memory_records")
    op.drop_index("ix_memory_records_expires_at", table_name="memory_records")
    op.drop_index("ix_memory_records_status", table_name="memory_records")
    op.drop_index("ix_memory_records_user_id", table_name="memory_records")
    op.drop_index("ix_memory_records_workspace_id", table_name="memory_records")
    op.drop_index("ix_memory_records_org_id", table_name="memory_records")
    op.drop_table("memory_records")
