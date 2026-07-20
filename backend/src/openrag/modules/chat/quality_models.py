"""Content-free durable audit records for released grounded answers."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class AnswerQualityAudit(UUIDPk, Base):
    __tablename__ = "answer_quality_audits"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_answer_quality_audits_message"),
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_answer_quality_audits_scope_id",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "message_id"],
            ["messages.org_id", "messages.workspace_id", "messages.id"],
            name="fk_answer_quality_audits_scope_message",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','skipped')",
            name="ck_answer_quality_audits_status",
        ),
        CheckConstraint(
            "attempts BETWEEN 0 AND 20",
            name="ck_answer_quality_audits_attempts",
        ),
        CheckConstraint(
            "grounding_policy_version > 0",
            name="ck_answer_quality_audits_policy_version",
        ),
        CheckConstraint(
            "grounding_score IS NULL OR grounding_score BETWEEN 0 AND 1",
            name="ck_answer_quality_audits_grounding_score",
        ),
        CheckConstraint(
            "completeness_score IS NULL OR completeness_score BETWEEN 0 AND 1",
            name="ck_answer_quality_audits_completeness_score",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_answer_quality_audits_lease",
        ),
        CheckConstraint(
            "(status = 'completed' AND grounding_score IS NOT NULL "
            "AND completeness_score IS NOT NULL AND passed IS NOT NULL "
            "AND result_code IS NOT NULL) OR "
            "(status <> 'completed' AND grounding_score IS NULL "
            "AND completeness_score IS NULL AND passed IS NULL "
            "AND result_code IS NULL)",
            name="ck_answer_quality_audits_terminal_scores",
        ),
        Index(
            "ix_answer_quality_audits_claim",
            "status",
            "lease_expires_at",
            "created_at",
            "id",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    message_id: Mapped[UUID] = mapped_column(index=True)
    grounding_policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("grounding_policies.id"), index=True
    )
    grounding_policy_version: Mapped[int]
    verifier_model_id: Mapped[UUID] = mapped_column(ForeignKey("models.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(200), default=None)
    lease_token: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), default=None, index=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    grounding_score: Mapped[float | None] = mapped_column(default=None)
    completeness_score: Mapped[float | None] = mapped_column(default=None)
    passed: Mapped[bool | None] = mapped_column(Boolean, default=None)
    result_code: Mapped[str | None] = mapped_column(String(64), default=None)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
