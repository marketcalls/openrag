"""Durable, branch-bound conversation summaries and background jobs."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk, naive_utc


class ConversationSummaryJob(UUIDPk, Base):
    __tablename__ = "conversation_summary_jobs"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_conversation_summary_jobs_org_workspace_id",
        ),
        UniqueConstraint(
            "branch_head_message_id",
            name="uq_conversation_summary_jobs_branch_head",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "chat_id"],
            ["chats.org_id", "chats.workspace_id", "chats.id"],
            name="fk_conversation_summary_jobs_org_workspace_chat",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "branch_head_message_id"],
            ["messages.org_id", "messages.workspace_id", "messages.id"],
            name="fk_conversation_summary_jobs_org_workspace_head",
        ),
        ForeignKeyConstraint(
            ["chat_id", "branch_head_message_id"],
            ["messages.chat_id", "messages.id"],
            name="fk_conversation_summary_jobs_same_chat_head",
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_conversation_summary_jobs_org_user",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('queued','running','completed','skipped','failed')",
            name="ck_conversation_summary_jobs_status",
        ),
        CheckConstraint(
            "attempts BETWEEN 0 AND 1000",
            name="ck_conversation_summary_jobs_attempts",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_conversation_summary_jobs_lease",
        ),
        Index(
            "ix_conversation_summary_jobs_claim",
            "status",
            "lease_expires_at",
            "created_at",
            "id",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    chat_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    branch_head_message_id: Mapped[UUID] = mapped_column(index=True)
    requested_model_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("models.id"),
        default=None,
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(200), default=None)
    lease_token: Mapped[UUID | None] = mapped_column(default=None, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class ConversationBranchSummary(UUIDPk, Base):
    __tablename__ = "conversation_branch_summaries"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_conversation_branch_summaries_org_workspace_id",
        ),
        UniqueConstraint(
            "branch_head_message_id",
            name="uq_conversation_branch_summaries_branch_head",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "chat_id"],
            ["chats.org_id", "chats.workspace_id", "chats.id"],
            name="fk_conversation_branch_summaries_org_workspace_chat",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "branch_head_message_id"],
            ["messages.org_id", "messages.workspace_id", "messages.id"],
            name="fk_conversation_branch_summaries_org_workspace_head",
        ),
        ForeignKeyConstraint(
            ["chat_id", "branch_head_message_id"],
            ["messages.chat_id", "messages.id"],
            name="fk_conversation_branch_summaries_same_chat_head",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "covers_through_message_id"],
            ["messages.org_id", "messages.workspace_id", "messages.id"],
            name="fk_conversation_branch_summaries_org_workspace_cover",
        ),
        ForeignKeyConstraint(
            ["chat_id", "covers_through_message_id"],
            ["messages.chat_id", "messages.id"],
            name="fk_conversation_branch_summaries_same_chat_cover",
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_conversation_branch_summaries_org_user",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('active','superseded','quarantined')",
            name="ck_conversation_branch_summaries_status",
        ),
        CheckConstraint(
            "char_length(content) BETWEEN 1 AND 8000",
            name="ck_conversation_branch_summaries_content",
        ),
        CheckConstraint(
            "source_message_count > 0 AND summary_tokens > 0",
            name="ck_conversation_branch_summaries_counts",
        ),
        CheckConstraint(
            "char_length(source_digest) = 64",
            name="ck_conversation_branch_summaries_digest",
        ),
        Index(
            "ix_conversation_branch_summaries_lookup",
            "org_id",
            "workspace_id",
            "chat_id",
            "status",
            "source_message_count",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    chat_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    branch_head_message_id: Mapped[UUID] = mapped_column(index=True)
    covers_through_message_id: Mapped[UUID] = mapped_column(index=True)
    content: Mapped[str] = mapped_column(Text())
    source_message_count: Mapped[int]
    source_digest: Mapped[str] = mapped_column(String(64))
    summary_tokens: Mapped[int]
    model_id: Mapped[UUID] = mapped_column(ForeignKey("models.id"))
    prompt_contract_version: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)
