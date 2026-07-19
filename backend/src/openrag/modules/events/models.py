from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk, naive_utc


class OutboxEvent(UUIDPk, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_outbox_events_attempts_nonnegative"),
        CheckConstraint(
            "NOT (published_at IS NOT NULL AND dead_lettered_at IS NOT NULL)",
            name="ck_outbox_events_terminal_exclusive",
        ),
        CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN "
            "('legacy_dispatch_failure','contract_invalid','schema_not_registered',"
            "'event_not_authoritative','event_transport_unavailable',"
            "'event_durability_unconfirmed')",
            name="ck_outbox_events_safe_error_code",
        ),
        CheckConstraint(
            "envelope_digest IS NULL OR envelope_digest ~ '^[0-9a-f]{64}$'",
            name="ck_outbox_events_envelope_digest",
        ),
        CheckConstraint(
            "json_typeof(payload) = 'object' AND pg_column_size(payload) <= 16384",
            name="ck_outbox_events_payload_bounded",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_outbox_events_lease_complete",
        ),
        Index(
            "ix_outbox_events_claimable",
            "dispatch_after",
            "created_at",
            postgresql_where=text("published_at IS NULL AND dead_lettered_at IS NULL"),
        ),
    )

    event_id: Mapped[UUID] = mapped_column(unique=True)
    aggregate_type: Mapped[str] = mapped_column(String(120), index=True)
    aggregate_id: Mapped[UUID] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(200), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True)
    attempts: Mapped[int] = mapped_column(default=0)
    dispatch_after: Mapped[datetime] = mapped_column(default=naive_utc, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    lease_token: Mapped[UUID | None] = mapped_column(default=None, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    published_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    envelope_digest: Mapped[str | None] = mapped_column(String(64), default=None)
    published_stream: Mapped[str | None] = mapped_column(String(200), default=None)
    published_message_id: Mapped[str | None] = mapped_column(String(128), default=None)


class InboxEvent(UUIDPk, Base):
    __tablename__ = "inbox_events"
    __table_args__ = (
        UniqueConstraint("consumer", "event_id", name="uq_inbox_consumer_event"),
        CheckConstraint(
            "char_length(event_type) BETWEEN 1 AND 200",
            name="ck_inbox_events_event_type_bounded",
        ),
    )

    consumer: Mapped[str] = mapped_column(index=True)
    event_id: Mapped[UUID]
    event_type: Mapped[str] = mapped_column(String(200), index=True)
