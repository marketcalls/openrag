from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
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
        CheckConstraint(
            "status IN ('accepted','queued','running','completed','failed','cancelled')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint(
            "attempts BETWEEN 0 AND 1000",
            name="ck_agent_runs_attempts",
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
