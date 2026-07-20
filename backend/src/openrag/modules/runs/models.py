from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk, naive_utc


class AgentRun(UUIDPk, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "client_request_id",
            name="uq_agent_runs_user_request",
        ),
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_agent_runs_org_workspace_id",
        ),
        CheckConstraint(
            "status IN ('accepted','queued','running','completed','failed','cancelled')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint(
            "attempts BETWEEN 0 AND 1000",
            name="ck_agent_runs_attempts",
        ),
        CheckConstraint(
            "reasoning_effort IN ('off','low','medium','high')",
            name="ck_agent_runs_reasoning_effort",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_agent_runs_lease",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"),
        index=True,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id"),
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
        index=True,
    )
    chat_id: Mapped[UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"),
        index=True,
    )
    input_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id"),
        index=True,
    )
    assistant_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id"),
        default=None,
    )
    model_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("models.id"),
        default=None,
    )
    reasoning_effort: Mapped[str] = mapped_column(String(16), default="off")
    client_request_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(default="accepted", index=True)
    route: Mapped[str | None] = mapped_column(default=None)
    error_code: Mapped[str | None] = mapped_column(default=None)
    trace_id: Mapped[str | None] = mapped_column(default=None, index=True)
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    lease_owner: Mapped[str | None] = mapped_column(default=None)
    lease_token: Mapped[UUID | None] = mapped_column(default=None, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    accepted_at: Mapped[datetime] = mapped_column(default=naive_utc)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    first_token_at: Mapped[datetime | None] = mapped_column(default=None)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class RunContextLedger(UUIDPk, Base):
    __tablename__ = "run_context_ledgers"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "run_id",
            "attempt",
            name="uq_run_context_ledgers_attempt",
        ),
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_run_context_ledgers_org_workspace_id",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "run_id"],
            ["agent_runs.org_id", "agent_runs.workspace_id", "agent_runs.id"],
            name="fk_run_context_ledgers_org_workspace_run",
            ondelete="CASCADE",
        ),
        CheckConstraint("attempt BETWEEN 1 AND 1000", name="ck_run_context_attempt"),
        CheckConstraint(
            "budget_tokens > 0 AND estimated_prompt_tokens >= 0 "
            "AND memory_tokens >= 0 AND history_tokens >= 0 "
            "AND retrieval_tokens >= 0",
            name="ck_run_context_token_counts",
        ),
        CheckConstraint(
            "memory_items BETWEEN 0 AND 8 AND history_messages >= 0 "
            "AND retrieval_items >= 0",
            name="ck_run_context_item_counts",
        ),
        CheckConstraint(
            "char_length(selection_digest) = 64",
            name="ck_run_context_selection_digest",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    run_id: Mapped[UUID] = mapped_column(index=True)
    attempt: Mapped[int]
    route: Mapped[str] = mapped_column(String(32), default="unknown")
    budget_tokens: Mapped[int]
    estimated_prompt_tokens: Mapped[int]
    memory_tokens: Mapped[int]
    memory_items: Mapped[int]
    history_tokens: Mapped[int]
    history_messages: Mapped[int]
    retrieval_tokens: Mapped[int]
    retrieval_items: Mapped[int]
    selection_digest: Mapped[str] = mapped_column(String(64))


class RunMemorySelection(UUIDPk, Base):
    __tablename__ = "run_memory_selections"
    __table_args__ = (
        UniqueConstraint("ledger_id", "rank", name="uq_run_memory_selection_rank"),
        UniqueConstraint("ledger_id", "memory_id", name="uq_run_memory_selection_memory"),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "ledger_id"],
            [
                "run_context_ledgers.org_id",
                "run_context_ledgers.workspace_id",
                "run_context_ledgers.id",
            ],
            name="fk_run_memory_selection_org_workspace_ledger",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "memory_id"],
            ["memory_records.org_id", "memory_records.workspace_id", "memory_records.id"],
            name="fk_run_memory_selection_org_workspace_memory",
            ondelete="CASCADE",
        ),
        CheckConstraint("rank BETWEEN 1 AND 8", name="ck_run_memory_selection_rank"),
        CheckConstraint(
            "estimated_tokens > 0",
            name="ck_run_memory_selection_tokens",
        ),
        CheckConstraint(
            "char_length(content_hash) = 64",
            name="ck_run_memory_selection_content_hash",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    ledger_id: Mapped[UUID] = mapped_column(index=True)
    memory_id: Mapped[UUID] = mapped_column(index=True)
    rank: Mapped[int]
    estimated_tokens: Mapped[int]
    content_hash: Mapped[str] = mapped_column(String(64))
