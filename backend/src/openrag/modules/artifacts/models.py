"""Persistence models for immutable, user-facing message artifacts."""

from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class MessageArtifact(UUIDPk, Base):
    """One immutable, validated presentation artifact owned by a message."""

    __tablename__ = "message_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "message_id",
            "kind",
            name="uq_message_artifacts_message_kind",
        ),
        CheckConstraint(
            "kind = 'analytics'",
            name="ck_message_artifacts_kind",
        ),
        CheckConstraint(
            "schema_version = 'analytics.v1'",
            name="ck_message_artifacts_schema_version",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object' "
            "AND payload->>'schema_version' = schema_version "
            "AND pg_column_size(payload) <= 49152",
            name="ck_message_artifacts_payload",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_message_artifacts_content_hash",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "message_id"],
            ["messages.org_id", "messages.workspace_id", "messages.id"],
            name="fk_message_artifacts_org_workspace_message",
            ondelete="CASCADE",
        ),
        Index(
            "ix_message_artifacts_message",
            "org_id",
            "workspace_id",
            "message_id",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    message_id: Mapped[UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(32))
    schema_version: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
