"""branch summary jobs

Revision ID: b7d9f2a4c6e8
Revises: a6c8e1f3b5d7
Create Date: 2026-07-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d9f2a4c6e8"
down_revision: str | Sequence[str] | None = "a6c8e1f3b5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_summary_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("chat_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("branch_head_message_id", sa.UUID(), nullable=False),
        sa.Column("requested_model_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_token", sa.UUID(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','skipped','failed')",
            name="ck_conversation_summary_jobs_status",
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND 1000",
            name="ck_conversation_summary_jobs_attempts",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_conversation_summary_jobs_lease",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["requested_model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "chat_id"],
            ["chats.org_id", "chats.workspace_id", "chats.id"],
            name="fk_conversation_summary_jobs_org_workspace_chat",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "branch_head_message_id"],
            ["messages.org_id", "messages.workspace_id", "messages.id"],
            name="fk_conversation_summary_jobs_org_workspace_head",
        ),
        sa.ForeignKeyConstraint(
            ["chat_id", "branch_head_message_id"],
            ["messages.chat_id", "messages.id"],
            name="fk_conversation_summary_jobs_same_chat_head",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_conversation_summary_jobs_org_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_conversation_summary_jobs_org_workspace_id",
        ),
        sa.UniqueConstraint(
            "branch_head_message_id",
            name="uq_conversation_summary_jobs_branch_head",
        ),
    )
    op.create_index(
        "ix_conversation_summary_jobs_org_id",
        "conversation_summary_jobs",
        ["org_id"],
    )
    op.create_index(
        "ix_conversation_summary_jobs_workspace_id",
        "conversation_summary_jobs",
        ["workspace_id"],
    )
    op.create_index(
        "ix_conversation_summary_jobs_chat_id",
        "conversation_summary_jobs",
        ["chat_id"],
    )
    op.create_index(
        "ix_conversation_summary_jobs_user_id",
        "conversation_summary_jobs",
        ["user_id"],
    )
    op.create_index(
        "ix_conversation_summary_jobs_branch_head_message_id",
        "conversation_summary_jobs",
        ["branch_head_message_id"],
    )
    op.create_index(
        "ix_conversation_summary_jobs_status",
        "conversation_summary_jobs",
        ["status"],
    )
    op.create_index(
        "ix_conversation_summary_jobs_lease_token",
        "conversation_summary_jobs",
        ["lease_token"],
    )
    op.create_index(
        "ix_conversation_summary_jobs_lease_expires_at",
        "conversation_summary_jobs",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_conversation_summary_jobs_claim",
        "conversation_summary_jobs",
        ["status", "lease_expires_at", "created_at", "id"],
    )

    op.create_table(
        "conversation_branch_summaries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("chat_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("branch_head_message_id", sa.UUID(), nullable=False),
        sa.Column("covers_through_message_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_message_count", sa.Integer(), nullable=False),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("summary_tokens", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.UUID(), nullable=False),
        sa.Column("prompt_contract_version", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','superseded','quarantined')",
            name="ck_conversation_branch_summaries_status",
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 8000",
            name="ck_conversation_branch_summaries_content",
        ),
        sa.CheckConstraint(
            "source_message_count > 0 AND summary_tokens > 0",
            name="ck_conversation_branch_summaries_counts",
        ),
        sa.CheckConstraint(
            "char_length(source_digest) = 64",
            name="ck_conversation_branch_summaries_digest",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "chat_id"],
            ["chats.org_id", "chats.workspace_id", "chats.id"],
            name="fk_conversation_branch_summaries_org_workspace_chat",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "branch_head_message_id"],
            ["messages.org_id", "messages.workspace_id", "messages.id"],
            name="fk_conversation_branch_summaries_org_workspace_head",
        ),
        sa.ForeignKeyConstraint(
            ["chat_id", "branch_head_message_id"],
            ["messages.chat_id", "messages.id"],
            name="fk_conversation_branch_summaries_same_chat_head",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "covers_through_message_id"],
            ["messages.org_id", "messages.workspace_id", "messages.id"],
            name="fk_conversation_branch_summaries_org_workspace_cover",
        ),
        sa.ForeignKeyConstraint(
            ["chat_id", "covers_through_message_id"],
            ["messages.chat_id", "messages.id"],
            name="fk_conversation_branch_summaries_same_chat_cover",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_conversation_branch_summaries_org_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_conversation_branch_summaries_org_workspace_id",
        ),
        sa.UniqueConstraint(
            "branch_head_message_id",
            name="uq_conversation_branch_summaries_branch_head",
        ),
    )
    op.create_index(
        "ix_conversation_branch_summaries_org_id",
        "conversation_branch_summaries",
        ["org_id"],
    )
    op.create_index(
        "ix_conversation_branch_summaries_workspace_id",
        "conversation_branch_summaries",
        ["workspace_id"],
    )
    op.create_index(
        "ix_conversation_branch_summaries_chat_id",
        "conversation_branch_summaries",
        ["chat_id"],
    )
    op.create_index(
        "ix_conversation_branch_summaries_user_id",
        "conversation_branch_summaries",
        ["user_id"],
    )
    op.create_index(
        "ix_conversation_branch_summaries_branch_head_message_id",
        "conversation_branch_summaries",
        ["branch_head_message_id"],
    )
    op.create_index(
        "ix_conversation_branch_summaries_covers_through_message_id",
        "conversation_branch_summaries",
        ["covers_through_message_id"],
    )
    op.create_index(
        "ix_conversation_branch_summaries_status",
        "conversation_branch_summaries",
        ["status"],
    )
    op.create_index(
        "ix_conversation_branch_summaries_lookup",
        "conversation_branch_summaries",
        ["org_id", "workspace_id", "chat_id", "status", "source_message_count"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_branch_summaries_lookup",
        table_name="conversation_branch_summaries",
    )
    op.drop_index(
        "ix_conversation_branch_summaries_status",
        table_name="conversation_branch_summaries",
    )
    op.drop_index(
        "ix_conversation_branch_summaries_covers_through_message_id",
        table_name="conversation_branch_summaries",
    )
    op.drop_index(
        "ix_conversation_branch_summaries_branch_head_message_id",
        table_name="conversation_branch_summaries",
    )
    op.drop_index(
        "ix_conversation_branch_summaries_user_id",
        table_name="conversation_branch_summaries",
    )
    op.drop_index(
        "ix_conversation_branch_summaries_chat_id",
        table_name="conversation_branch_summaries",
    )
    op.drop_index(
        "ix_conversation_branch_summaries_workspace_id",
        table_name="conversation_branch_summaries",
    )
    op.drop_index(
        "ix_conversation_branch_summaries_org_id",
        table_name="conversation_branch_summaries",
    )
    op.drop_table("conversation_branch_summaries")
    op.drop_index(
        "ix_conversation_summary_jobs_claim",
        table_name="conversation_summary_jobs",
    )
    op.drop_index(
        "ix_conversation_summary_jobs_lease_expires_at",
        table_name="conversation_summary_jobs",
    )
    op.drop_index(
        "ix_conversation_summary_jobs_lease_token",
        table_name="conversation_summary_jobs",
    )
    op.drop_index(
        "ix_conversation_summary_jobs_status",
        table_name="conversation_summary_jobs",
    )
    op.drop_index(
        "ix_conversation_summary_jobs_branch_head_message_id",
        table_name="conversation_summary_jobs",
    )
    op.drop_index(
        "ix_conversation_summary_jobs_user_id",
        table_name="conversation_summary_jobs",
    )
    op.drop_index(
        "ix_conversation_summary_jobs_chat_id",
        table_name="conversation_summary_jobs",
    )
    op.drop_index(
        "ix_conversation_summary_jobs_workspace_id",
        table_name="conversation_summary_jobs",
    )
    op.drop_index(
        "ix_conversation_summary_jobs_org_id",
        table_name="conversation_summary_jobs",
    )
    op.drop_table("conversation_summary_jobs")
