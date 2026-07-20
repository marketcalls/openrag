"""Tenant-safe memory records, provenance, preferences, and suppression."""

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk, naive_utc


class MemoryRecord(UUIDPk, Base):
    __tablename__ = "memory_records"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_memory_records_org_workspace_id",
        ),
        UniqueConstraint(
            "org_id",
            "user_id",
            "client_request_id",
            name="uq_memory_records_user_request",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_memory_records_org_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "superseded_by_id"],
            ["memory_records.org_id", "memory_records.workspace_id", "memory_records.id"],
            name="fk_memory_records_same_workspace_successor",
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_memory_records_org_user",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "memory_type IN ('semantic','episodic','procedural')",
            name="ck_memory_records_type",
        ),
        CheckConstraint(
            "scope IN ('user_workspace','user_org','workspace_shared')",
            name="ck_memory_records_scope",
        ),
        CheckConstraint(
            "status IN ('candidate','active','conflicted','superseded',"
            "'retracted','expired','quarantined')",
            name="ck_memory_records_status",
        ),
        CheckConstraint(
            "sensitivity IN ('public','internal','confidential','restricted')",
            name="ck_memory_records_sensitivity",
        ),
        CheckConstraint(
            "confidence BETWEEN 0 AND 1 AND importance BETWEEN 0 AND 1",
            name="ck_memory_records_scores",
        ),
        CheckConstraint(
            "char_length(content_hash) = 64 AND "
            "char_length(suppression_fingerprint) = 64",
            name="ck_memory_records_hashes",
        ),
        CheckConstraint(
            "char_length(canonical_key) BETWEEN 1 AND 120 "
            "AND char_length(content) BETWEEN 1 AND 4000 "
            "AND char_length(policy_version) BETWEEN 1 AND 100 "
            "AND (model_version IS NULL OR char_length(model_version) BETWEEN 1 AND 200)",
            name="ck_memory_records_strings_bounded",
        ),
        CheckConstraint(
            "structured_value IS NULL OR (jsonb_typeof(structured_value) = 'object' "
            "AND pg_column_size(structured_value) <= 8192)",
            name="ck_memory_records_structured_value",
        ),
        Index(
            "ix_memory_records_active_selection",
            "org_id",
            "workspace_id",
            "user_id",
            "status",
            "updated_at",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    client_request_id: Mapped[UUID]
    canonical_key: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text())
    structured_value: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        default=None,
    )
    memory_type: Mapped[str] = mapped_column(String(32))
    scope: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    confidence: Mapped[float] = mapped_column(default=1.0)
    importance: Mapped[float] = mapped_column(default=0.5)
    sensitivity: Mapped[str] = mapped_column(String(32), default="internal")
    valid_from: Mapped[datetime | None] = mapped_column(default=None)
    expires_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    conflict_group: Mapped[str | None] = mapped_column(String(120), default=None)
    superseded_by_id: Mapped[UUID | None] = mapped_column(default=None)
    policy_version: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[str | None] = mapped_column(String(200), default=None)
    source_trust: Mapped[str] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64))
    suppression_fingerprint: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)


class MemoryProvenance(UUIDPk, Base):
    __tablename__ = "memory_provenance"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "actor_user_id",
            "source_event_id",
            name="uq_memory_provenance_actor_event",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "memory_id"],
            ["memory_records.org_id", "memory_records.workspace_id", "memory_records.id"],
            name="fk_memory_provenance_org_workspace_memory",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "actor_user_id"],
            ["users.org_id", "users.id"],
            name="fk_memory_provenance_org_actor",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "source_message_id"],
            ["messages.org_id", "messages.workspace_id", "messages.id"],
            name="fk_memory_provenance_org_workspace_message",
        ),
        CheckConstraint(
            "source_kind IN ('explicit_user_action','user_message',"
            "'verified_event','approved_procedure')",
            name="ck_memory_provenance_source_kind",
        ),
        CheckConstraint(
            "char_length(source_hash) = 64",
            name="ck_memory_provenance_source_hash",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    memory_id: Mapped[UUID] = mapped_column(index=True)
    actor_user_id: Mapped[UUID] = mapped_column(index=True)
    source_kind: Mapped[str] = mapped_column(String(32))
    source_event_id: Mapped[UUID]
    source_message_id: Mapped[UUID | None] = mapped_column(default=None)
    source_hash: Mapped[str] = mapped_column(String(64))


class MemorySuppression(UUIDPk, Base):
    __tablename__ = "memory_suppressions"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "user_id",
            "fingerprint",
            name="uq_memory_suppressions_scope_fingerprint",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_memory_suppressions_org_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_memory_suppressions_org_user",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "char_length(fingerprint) = 64",
            name="ck_memory_suppressions_fingerprint",
        ),
        CheckConstraint(
            "char_length(reason) BETWEEN 1 AND 64",
            name="ck_memory_suppressions_reason",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(64), default="user_forgot")


class MemoryPreference(UUIDPk, Base):
    __tablename__ = "memory_preferences"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "user_id",
            name="uq_memory_preferences_scope",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_memory_preferences_org_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_memory_preferences_org_user",
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    extraction_enabled: Mapped[bool] = mapped_column(default=False)
    semantic_enabled: Mapped[bool] = mapped_column(default=True)
    episodic_enabled: Mapped[bool] = mapped_column(default=False)
    procedural_enabled: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)
