"""Persisted immutable embedding configurations and generation cutovers."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk, naive_utc


class EmbeddingProfile(UUIDPk, Base):
    __tablename__ = "embedding_profiles"
    __table_args__ = (
        UniqueConstraint("name_key", name="uq_embedding_profiles_name_key"),
        CheckConstraint(
            "provider_kind IN ('litellm','tei','hash')",
            name="ck_embedding_profiles_provider_kind",
        ),
        CheckConstraint(
            "dimension BETWEEN 1 AND 32768",
            name="ck_embedding_profiles_dimension",
        ),
        CheckConstraint(
            "max_input_tokens BETWEEN 1 AND 2000000",
            name="ck_embedding_profiles_max_input_tokens",
        ),
        CheckConstraint(
            "batch_size BETWEEN 1 AND 1024",
            name="ck_embedding_profiles_batch_size",
        ),
        CheckConstraint(
            "config_digest ~ '^[0-9a-f]{64}$'",
            name="ck_embedding_profiles_config_digest",
        ),
    )

    name: Mapped[str] = mapped_column(String(120))
    name_key: Mapped[str] = mapped_column(String(120))
    provider_kind: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    dimension: Mapped[int]
    max_input_tokens: Mapped[int]
    batch_size: Mapped[int]
    config_digest: Mapped[str] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column(default=True, index=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class EmbeddingDeployment(UUIDPk, Base):
    """A globally governed, immutable-profile authority generation."""

    __tablename__ = "embedding_deployments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('building','ready','active','failed','retired')",
            name="ck_embedding_deployments_status",
        ),
        CheckConstraint(
            "total_versions >= 0 AND completed_versions >= 0 "
            "AND failed_versions >= 0 "
            "AND completed_versions + failed_versions <= total_versions",
            name="ck_embedding_deployments_counts",
        ),
        CheckConstraint(
            "failure_code IS NULL OR char_length(failure_code) BETWEEN 1 AND 100",
            name="ck_embedding_deployments_failure_code",
        ),
        CheckConstraint(
            "attempts BETWEEN 0 AND 1000000",
            name="ck_embedding_deployments_attempts",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_embedding_deployments_lease",
        ),
        Index(
            "uq_embedding_deployments_one_active",
            text("(true)"),
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_embedding_deployments_one_pending",
            text("(true)"),
            unique=True,
            postgresql_where=text("status IN ('building','ready')"),
        ),
    )

    profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("embedding_profiles.id", ondelete="RESTRICT"),
        index=True,
    )
    generation_id: Mapped[UUID] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(String(32), default="building", index=True)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    activated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"),
        default=None,
    )
    activated_at: Mapped[datetime | None] = mapped_column(default=None)
    failure_code: Mapped[str | None] = mapped_column(String(100), default=None)
    total_versions: Mapped[int] = mapped_column(default=0)
    completed_versions: Mapped[int] = mapped_column(default=0)
    failed_versions: Mapped[int] = mapped_column(default=0)
    scan_complete: Mapped[bool] = mapped_column(default=False)
    scan_cursor_document_version_id: Mapped[UUID | None] = mapped_column(default=None)
    lease_owner: Mapped[str | None] = mapped_column(String(200), default=None)
    lease_token: Mapped[UUID | None] = mapped_column(default=None, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None)
    attempts: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)
