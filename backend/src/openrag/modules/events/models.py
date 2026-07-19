from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class OutboxEvent(UUIDPk, Base):
    __tablename__ = "outbox_events"

    event_id: Mapped[UUID] = mapped_column(unique=True)
    aggregate_type: Mapped[str] = mapped_column(index=True)
    aggregate_id: Mapped[UUID] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    dedupe_key: Mapped[str] = mapped_column(unique=True)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_owner: Mapped[str | None] = mapped_column(default=None, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    published_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    last_error: Mapped[str | None] = mapped_column(default=None)


class InboxEvent(UUIDPk, Base):
    __tablename__ = "inbox_events"
    __table_args__ = (
        UniqueConstraint("consumer", "event_id", name="uq_inbox_consumer_event"),
    )

    consumer: Mapped[str] = mapped_column(index=True)
    event_id: Mapped[UUID]
