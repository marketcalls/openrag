from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk, naive_utc


class Model(UUIDPk, Base):
    __tablename__ = "models"
    __table_args__ = (
        CheckConstraint(
            "(NOT supports_structured_json OR supports_chat_completion) "
            "AND (NOT supports_verifier OR supports_structured_json)",
            name="ck_models_capability_hierarchy",
        ),
        CheckConstraint(
            "default_reasoning_effort IN ('off','low','medium','high') "
            "AND (supports_reasoning OR default_reasoning_effort = 'off')",
            name="ck_models_default_reasoning_effort",
        ),
        CheckConstraint(
            "probe_status IN ('pending','passed','failed')",
            name="ck_models_probe_status",
        ),
        CheckConstraint("probe_revision > 0", name="ck_models_probe_revision"),
        CheckConstraint(
            "(probe_status = 'passed' AND supports_chat_completion "
            "AND supports_streaming) OR "
            "(probe_status IN ('pending','failed') "
            "AND NOT supports_chat_completion AND NOT supports_streaming "
            "AND NOT supports_structured_json AND NOT supports_verifier "
            "AND NOT supports_tools AND NOT supports_vision "
            "AND NOT supports_reasoning)",
            name="ck_models_measured_capabilities",
        ),
        CheckConstraint(
            "context_window IS NULL OR context_window BETWEEN 1 AND 10000000",
            name="ck_models_context_window",
        ),
        CheckConstraint(
            "probe_latency_ms IS NULL OR probe_latency_ms BETWEEN 0 AND 120000",
            name="ck_models_probe_latency",
        ),
        CheckConstraint(
            "NOT is_utility OR (enabled AND probe_status = 'passed' "
            "AND supports_chat_completion AND supports_streaming)",
            name="ck_models_utility_measured",
        ),
        Index(
            "uq_models_single_utility",
            "is_utility",
            unique=True,
            postgresql_where=text("is_utility"),
        ),
    )

    litellm_model_name: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str]
    provider_kind: Mapped[str]
    base_url: Mapped[str | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=True)
    is_utility: Mapped[bool] = mapped_column(
        default=False,
        server_default="false",
        index=True,
    )
    supports_chat_completion: Mapped[bool] = mapped_column(default=False)
    supports_streaming: Mapped[bool] = mapped_column(default=False)
    supports_structured_json: Mapped[bool] = mapped_column(default=False)
    supports_verifier: Mapped[bool] = mapped_column(default=False)
    supports_tools: Mapped[bool] = mapped_column(default=False)
    supports_vision: Mapped[bool] = mapped_column(default=False)
    context_window: Mapped[int | None] = mapped_column(default=None)
    supports_reasoning: Mapped[bool] = mapped_column(default=False)
    default_reasoning_effort: Mapped[str] = mapped_column(
        String(16),
        default="off",
    )
    provider_preset_version: Mapped[str | None] = mapped_column(String(100), default=None)
    probe_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    probe_revision: Mapped[int] = mapped_column(default=1)
    probe_latency_ms: Mapped[int | None] = mapped_column(default=None)
    last_probe_error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    last_probed_at: Mapped[datetime | None] = mapped_column(default=None)


class ModelProbe(UUIDPk, Base):
    """One revision-fenced, secret-free live capability probe request."""

    __tablename__ = "model_probes"
    __table_args__ = (
        UniqueConstraint("model_id", "revision", name="uq_model_probes_revision"),
        CheckConstraint("revision > 0", name="ck_model_probes_revision"),
        CheckConstraint(
            "status IN ('queued','running','passed','failed','stale')",
            name="ck_model_probes_status",
        ),
        CheckConstraint(
            "configuration_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_model_probes_configuration_fingerprint",
        ),
        CheckConstraint(
            "attempts BETWEEN 0 AND 10",
            name="ck_model_probes_attempts",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_model_probes_lease",
        ),
        CheckConstraint(
            "context_window IS NULL OR context_window BETWEEN 1 AND 10000000",
            name="ck_model_probes_context_window",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms BETWEEN 0 AND 120000",
            name="ck_model_probes_latency",
        ),
        Index("ix_model_probes_claim", "status", "lease_expires_at", "created_at", "id"),
    )

    model_id: Mapped[UUID] = mapped_column(
        ForeignKey("models.id", ondelete="CASCADE"), index=True
    )
    requested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    revision: Mapped[int]
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(200), default=None)
    lease_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None)
    supports_chat_completion: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_streaming: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_structured_json: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_reasoning: Mapped[bool] = mapped_column(Boolean, default=False)
    context_window: Mapped[int | None] = mapped_column(default=None)
    latency_ms: Mapped[int | None] = mapped_column(default=None)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    completed_at: Mapped[datetime | None] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)
