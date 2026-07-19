from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk, naive_utc


class GroundingPolicy(UUIDPk, Base):
    """Immutable, secret-free grounding policy and calibrated verifier binding."""

    __tablename__ = "grounding_policies"
    __table_args__ = (
        UniqueConstraint("org_id", "workspace_id", "id", name="uq_grounding_policies_scope_id"),
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "policy_version",
            name="uq_grounding_policies_scope_version",
        ),
        Index(
            "uq_grounding_policies_active_workspace",
            "org_id",
            "workspace_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        CheckConstraint("policy_version > 0", name="ck_grounding_policies_version"),
        CheckConstraint(
            "entailment_threshold >= 0 AND entailment_threshold <= 1",
            name="ck_grounding_policies_entailment_threshold",
        ),
        CheckConstraint("calibration_sample_count >= 0", name="ck_grounding_policies_sample_count"),
        CheckConstraint(
            "char_length(calibration_dataset_hash) = 64",
            name="ck_grounding_policies_dataset_hash",
        ),
        CheckConstraint(
            "char_length(calibration_dataset_version) BETWEEN 1 AND 100",
            name="ck_grounding_policies_dataset_version",
        ),
        CheckConstraint(
            "char_length(provider_preset_version) BETWEEN 1 AND 100",
            name="ck_grounding_policies_preset_version",
        ),
        CheckConstraint(
            "char_length(binding_revision) BETWEEN 1 AND 100 "
            "AND char_length(credential_fingerprint) BETWEEN 1 AND 128",
            name="ck_grounding_policies_binding_snapshot",
        ),
        CheckConstraint(
            "measured_false_support_rate IS NULL OR "
            "(measured_false_support_rate >= 0 AND measured_false_support_rate <= 1)",
            name="ck_grounding_policies_false_support_rate",
        ),
        CheckConstraint(
            "measured_false_refusal_rate IS NULL OR "
            "(measured_false_refusal_rate >= 0 AND measured_false_refusal_rate <= 1)",
            name="ck_grounding_policies_false_refusal_rate",
        ),
        CheckConstraint(
            "status IN ('draft','passed','active','retired')",
            name="ck_grounding_policies_status",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_grounding_policies_org_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_grounding_policies_org_creator",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    policy_version: Mapped[int]
    verifier_model_id: Mapped[UUID] = mapped_column(ForeignKey("models.id"), index=True)
    binding_revision: Mapped[str] = mapped_column(String(100))
    provider_preset_version: Mapped[str] = mapped_column(String(100))
    credential_fingerprint: Mapped[str] = mapped_column(String(128))
    entailment_threshold: Mapped[float]
    calibration_dataset_version: Mapped[str] = mapped_column(String(100))
    calibration_dataset_hash: Mapped[str] = mapped_column(String(64))
    calibration_sample_count: Mapped[int]
    measured_false_support_rate: Mapped[float | None] = mapped_column(default=None)
    measured_false_refusal_rate: Mapped[float | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="draft", index=True)
    effective_at: Mapped[datetime | None] = mapped_column(default=None)
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    created_by: Mapped[UUID]
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)


class GroundingCalibrationRun(UUIDPk, Base):
    """Replay-safe aggregate calibration result; raw provider data is never stored."""

    __tablename__ = "grounding_calibration_runs"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "policy_id",
            "generation_id",
            name="uq_grounding_calibration_runs_generation",
        ),
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_grounding_calibration_runs_scope_id",
        ),
        CheckConstraint(
            "state IN ('queued','running','passed','failed')",
            name="ck_grounding_calibration_runs_state",
        ),
        CheckConstraint("attempts >= 0", name="ck_grounding_calibration_runs_attempts"),
        CheckConstraint(
            "sample_count >= 0 AND supported_count >= 0 AND refused_count >= 0 "
            "AND supported_count + refused_count = sample_count",
            name="ck_grounding_calibration_runs_counts",
        ),
        CheckConstraint(
            "char_length(idempotency_digest) = 64",
            name="ck_grounding_calibration_runs_idempotency_digest",
        ),
        CheckConstraint(
            "result_digest IS NULL OR char_length(result_digest) = 64",
            name="ck_grounding_calibration_runs_result_digest",
        ),
        CheckConstraint(
            "char_length(requested_binding_revision) BETWEEN 1 AND 100 "
            "AND char_length(requested_preset_version) BETWEEN 1 AND 100 "
            "AND char_length(requested_credential_fingerprint) BETWEEN 1 AND 128",
            name="ck_grounding_calibration_runs_binding_snapshot",
        ),
        CheckConstraint(
            "false_support_rate IS NULL OR (false_support_rate >= 0 AND false_support_rate <= 1)",
            name="ck_grounding_calibration_runs_false_support_rate",
        ),
        CheckConstraint(
            "false_refusal_rate IS NULL OR (false_refusal_rate >= 0 AND false_refusal_rate <= 1)",
            name="ck_grounding_calibration_runs_false_refusal_rate",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_grounding_calibration_runs_org_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "policy_id"],
            [
                "grounding_policies.org_id",
                "grounding_policies.workspace_id",
                "grounding_policies.id",
            ],
            name="fk_grounding_calibration_runs_scope_policy",
            ondelete="CASCADE",
        ),
    )

    generation_id: Mapped[UUID]
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    policy_id: Mapped[UUID] = mapped_column(index=True)
    idempotency_digest: Mapped[str] = mapped_column(String(64))
    requested_binding_revision: Mapped[str] = mapped_column(String(100))
    requested_preset_version: Mapped[str] = mapped_column(String(100))
    requested_credential_fingerprint: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(default="queued", index=True)
    checkpoint: Mapped[str | None] = mapped_column(String(128), default=None)
    lease_owner: Mapped[str | None] = mapped_column(String(200), default=None)
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None)
    attempts: Mapped[int] = mapped_column(default=0)
    sample_count: Mapped[int] = mapped_column(default=0)
    supported_count: Mapped[int] = mapped_column(default=0)
    refused_count: Mapped[int] = mapped_column(default=0)
    false_support_rate: Mapped[float | None] = mapped_column(default=None)
    false_refusal_rate: Mapped[float | None] = mapped_column(default=None)
    result_digest: Mapped[str | None] = mapped_column(String(64), default=None)
    error_code: Mapped[str | None] = mapped_column(String(100), default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)
