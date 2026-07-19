from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk, naive_utc


class Chat(UUIDPk, Base):
    __tablename__ = "chats"
    __table_args__ = (
        UniqueConstraint("org_id", "workspace_id", "id", name="uq_chats_org_workspace_id"),
        ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_chats_org_workspace",
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_chats_org_user",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(default="New chat")
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)


class Message(UUIDPk, Base):
    """One immutable node in a branchable conversation tree."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "chat_id", "parent_message_id", "sibling_index", name="uq_messages_sibling"
        ),
        UniqueConstraint("org_id", "workspace_id", "id", name="uq_messages_org_workspace_id"),
        UniqueConstraint("chat_id", "id", name="uq_messages_chat_id"),
        CheckConstraint(
            "answer_status IS NULL OR answer_status IN ('verified','refused')",
            name="ck_messages_answer_status",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "chat_id"],
            ["chats.org_id", "chats.workspace_id", "chats.id"],
            name="fk_messages_org_workspace_chat",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["chat_id", "parent_message_id"],
            ["messages.chat_id", "messages.id"],
            name="fk_messages_same_chat_parent",
            ondelete="CASCADE",
        ),
    )

    # Nullable through the Task 1 expand model so existing rows remain writable;
    # Task 2 backfills and makes both columns non-null at the database boundary.
    org_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    workspace_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    chat_id: Mapped[UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"),
        index=True,
    )
    parent_message_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    sibling_index: Mapped[int] = mapped_column(default=0)
    role: Mapped[str]
    content: Mapped[str] = mapped_column(Text())
    model_id: Mapped[UUID | None] = mapped_column(default=None)
    prompt_tokens: Mapped[int | None] = mapped_column(default=None)
    completion_tokens: Mapped[int | None] = mapped_column(default=None)
    answer_status: Mapped[str | None] = mapped_column(String(32), default=None)
    refusal_reason: Mapped[str | None] = mapped_column(String(64), default=None)
    grounding_policy_id: Mapped[UUID | None] = mapped_column(default=None)
    verifier_model_id: Mapped[UUID | None] = mapped_column(default=None)


class Citation(UUIDPk, Base):
    __tablename__ = "citations"
    __table_args__ = (
        CheckConstraint("page > 0", name="ck_citations_page_positive"),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "message_id"],
            ["messages.org_id", "messages.workspace_id", "messages.id"],
            name="fk_citations_org_workspace_message",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "document_id"],
            ["documents.org_id", "documents.workspace_id", "documents.id"],
            name="fk_citations_org_workspace_document",
        ),
        ForeignKeyConstraint(
            ["org_id", "document_id", "document_version_id"],
            ["document_versions.org_id", "document_versions.document_id", "document_versions.id"],
            name="fk_citations_org_document_version",
        ),
        ForeignKeyConstraint(
            ["org_id", "document_version_id", "evidence_span_id"],
            [
                "document_evidence_spans.org_id",
                "document_evidence_spans.document_version_id",
                "document_evidence_spans.id",
            ],
            name="fk_citations_org_version_evidence_span",
        ),
    )

    # Authority fields stay nullable until Task 2 backfills legacy citation rows.
    org_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    workspace_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
    )
    document_id: Mapped[UUID]
    document_version_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    evidence_span_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    chunk_ref: Mapped[str]
    page: Mapped[int]
    score: Mapped[float]
    marker: Mapped[int]
    document_name: Mapped[str | None] = mapped_column(String(500), default=None)
    version_label: Mapped[str | None] = mapped_column(String(200), default=None)
    section_label: Mapped[str | None] = mapped_column(String(500), default=None)
    locator_kind: Mapped[str | None] = mapped_column(String(32), default=None)
    locator_label: Mapped[str | None] = mapped_column(String(200), default=None)
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    dense_score: Mapped[float | None] = mapped_column(default=None)
    sparse_score: Mapped[float | None] = mapped_column(default=None)
    fused_score: Mapped[float | None] = mapped_column(default=None)
    rerank_score: Mapped[float | None] = mapped_column(default=None)
    claim_id: Mapped[UUID | None] = mapped_column(default=None)
    verification_state: Mapped[str | None] = mapped_column(String(32), default=None)
