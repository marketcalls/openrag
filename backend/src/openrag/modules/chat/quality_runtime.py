"""Lease-fenced asynchronous quality auditing for grounded answers."""

import asyncio
import hashlib
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings
from openrag.core.db import naive_utc
from openrag.core.errors import OpenRAGError, UpstreamError
from openrag.modules.chat.models import Citation, Message
from openrag.modules.chat.quality_models import AnswerQualityAudit
from openrag.modules.documents.models import DocumentChunk, DocumentEvidenceSpan
from openrag.modules.grounding.models import GroundingPolicy
from openrag.modules.models.models import Model
from openrag.modules.orchestration.agno_validator import AgnoStructuredVerifierStreamer
from openrag.modules.orchestration.answer_validation import StrictAnswerValidator
from openrag.modules.orchestration.model_gateway import resolve_model_runtime

MAX_QUALITY_AUDIT_ATTEMPTS = 8
QualityObservationStatus = Literal["passed", "failed"]


@dataclass(frozen=True, slots=True)
class QualityAuditObservation:
    status: QualityObservationStatus
    grounding_score: float
    completeness_score: float
    reason_code: str

    def __post_init__(self) -> None:
        if not 0 <= self.grounding_score <= 1 or not 0 <= self.completeness_score <= 1:
            raise ValueError("quality_audit_score_invalid")
        if not 1 <= len(self.reason_code) <= 64:
            raise ValueError("quality_audit_reason_invalid")

    @property
    def passed(self) -> bool:
        return self.status == "passed"


@dataclass(frozen=True, slots=True)
class QualityAuditEvidenceRow:
    """Ephemeral evidence fields; never persisted in the audit record."""

    org_id: UUID
    workspace_id: UUID
    message_id: UUID
    marker: int
    grounding_policy_id: UUID | None
    grounding_policy_version: int | None
    verifier_model_id: UUID | None
    citation_content_hash: str | None
    span_content_hash: str
    artifact_byte_start: int
    artifact_byte_end: int
    chunk_text: str


@dataclass(frozen=True, slots=True)
class QualityAuditLeaseClaim:
    audit_id: UUID
    token: UUID
    owner: str
    attempt: int
    recovered: bool


@dataclass(frozen=True, slots=True)
class PreparedQualityAudit:
    claim: QualityAuditLeaseClaim
    question: str
    answer: str
    evidence: tuple[str, ...]
    validator: StrictAnswerValidator


def build_quality_claim_query(now: datetime) -> Select[tuple[AnswerQualityAudit]]:
    return (
        select(AnswerQualityAudit)
        .where(
            AnswerQualityAudit.attempts < MAX_QUALITY_AUDIT_ATTEMPTS,
            or_(
                AnswerQualityAudit.status == "queued",
                and_(
                    AnswerQualityAudit.status == "running",
                    AnswerQualityAudit.lease_expires_at.is_not(None),
                    AnswerQualityAudit.lease_expires_at <= now,
                ),
            ),
        )
        .order_by(AnswerQualityAudit.created_at, AnswerQualityAudit.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def extract_cited_evidence(
    audit: AnswerQualityAudit,
    rows: list[QualityAuditEvidenceRow],
) -> tuple[str, ...]:
    """Reconstruct exact cited UTF-8 spans and reject any snapshot drift."""

    if not 1 <= len(rows) <= 8:
        raise ValueError("quality_evidence_count_invalid")
    markers = [row.marker for row in rows]
    if len(markers) != len(set(markers)) or any(not 1 <= marker <= 8 for marker in markers):
        raise ValueError("quality_evidence_markers_invalid")
    evidence: list[str] = []
    for row in sorted(rows, key=lambda item: item.marker):
        if (
            row.org_id != audit.org_id
            or row.workspace_id != audit.workspace_id
            or row.message_id != audit.message_id
        ):
            raise ValueError("quality_evidence_scope_invalid")
        if (
            row.grounding_policy_id != audit.grounding_policy_id
            or row.grounding_policy_version != audit.grounding_policy_version
            or row.verifier_model_id != audit.verifier_model_id
        ):
            raise ValueError("quality_evidence_policy_invalid")
        if (
            row.citation_content_hash is None
            or row.citation_content_hash != row.span_content_hash
        ):
            raise ValueError("quality_evidence_hash_invalid")
        encoded = row.chunk_text.encode("utf-8")
        if (
            row.artifact_byte_start < 0
            or row.artifact_byte_end <= row.artifact_byte_start
            or row.artifact_byte_end > len(encoded)
        ):
            raise ValueError("quality_evidence_range_invalid")
        payload = encoded[row.artifact_byte_start : row.artifact_byte_end]
        if hashlib.sha256(payload).hexdigest() != row.span_content_hash:
            raise ValueError("quality_evidence_hash_invalid")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("quality_evidence_encoding_invalid") from exc
        if not text.strip():
            raise ValueError("quality_evidence_text_invalid")
        evidence.append(f"[{row.marker}] {text[:3_980]}")
    return tuple(evidence)


def _validate_lease(owner: str, lease_seconds: int) -> None:
    if not 1 <= len(owner) <= 200:
        raise ValueError("quality_audit_lease_owner_invalid")
    if not 30 <= lease_seconds <= 600:
        raise ValueError("quality_audit_lease_seconds_invalid")


async def claim_next_quality_audit(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    lease_seconds: int,
) -> QualityAuditLeaseClaim | None:
    _validate_lease(owner, lease_seconds)
    now = naive_utc()
    async with session_factory.begin() as session:
        audit = await session.scalar(build_quality_claim_query(now))
        if audit is None:
            return None
        recovered = audit.status == "running"
        token = uuid4()
        audit.status = "running"
        audit.attempts += 1
        audit.started_at = audit.started_at or now
        audit.finished_at = None
        audit.lease_owner = owner
        audit.lease_token = token
        audit.lease_expires_at = now + timedelta(seconds=lease_seconds)
        audit.error_code = None
        await session.flush()
        return QualityAuditLeaseClaim(audit.id, token, owner, audit.attempts, recovered)


async def renew_quality_audit_lease(
    session_factory: async_sessionmaker[AsyncSession],
    claim: QualityAuditLeaseClaim,
    *,
    lease_seconds: int,
) -> bool:
    _validate_lease(claim.owner, lease_seconds)
    async with session_factory.begin() as session:
        result = await session.execute(
            update(AnswerQualityAudit)
            .where(
                AnswerQualityAudit.id == claim.audit_id,
                AnswerQualityAudit.status == "running",
                AnswerQualityAudit.lease_owner == claim.owner,
                AnswerQualityAudit.lease_token == claim.token,
            )
            .values(lease_expires_at=naive_utc() + timedelta(seconds=lease_seconds))
            .returning(AnswerQualityAudit.id)
        )
        return result.scalar_one_or_none() == claim.audit_id


async def _prepare_quality_audit(
    session_factory: async_sessionmaker[AsyncSession],
    claim: QualityAuditLeaseClaim,
    settings: Settings,
) -> PreparedQualityAudit | str:
    async with session_factory() as session:
        audit = await session.scalar(
            select(AnswerQualityAudit).where(
                AnswerQualityAudit.id == claim.audit_id,
                AnswerQualityAudit.status == "running",
                AnswerQualityAudit.lease_owner == claim.owner,
                AnswerQualityAudit.lease_token == claim.token,
            )
        )
        if audit is None:
            return "contested"
        message = await session.scalar(
            select(Message).where(
                Message.id == audit.message_id,
                Message.org_id == audit.org_id,
                Message.workspace_id == audit.workspace_id,
                Message.role == "assistant",
                Message.answer_status == "grounded",
            )
        )
        if (
            message is None
            or message.parent_message_id is None
            or message.grounding_policy_id != audit.grounding_policy_id
            or message.grounding_policy_version != audit.grounding_policy_version
            or message.verifier_model_id != audit.verifier_model_id
        ):
            return "snapshot_unavailable"
        question = await session.scalar(
            select(Message.content).where(
                Message.id == message.parent_message_id,
                Message.org_id == audit.org_id,
                Message.workspace_id == audit.workspace_id,
                Message.chat_id == message.chat_id,
                Message.role == "user",
            )
        )
        if question is None:
            return "question_unavailable"
        policy = await session.scalar(
            select(GroundingPolicy).where(
                GroundingPolicy.id == audit.grounding_policy_id,
                GroundingPolicy.org_id == audit.org_id,
                GroundingPolicy.workspace_id == audit.workspace_id,
                GroundingPolicy.policy_version == audit.grounding_policy_version,
                GroundingPolicy.verifier_model_id == audit.verifier_model_id,
            )
        )
        model = await session.get(Model, audit.verifier_model_id)
        if policy is None or model is None:
            return "policy_unavailable"
        if (
            not model.enabled
            or model.probe_status != "passed"
            or not model.supports_structured_json
            or not model.supports_verifier
        ):
            return "verifier_capability_unavailable"
        raw_rows = (
            await session.execute(
                select(
                    Citation.org_id,
                    Citation.workspace_id,
                    Citation.message_id,
                    Citation.marker,
                    Citation.grounding_policy_id,
                    Citation.grounding_policy_version,
                    Citation.verifier_model_id,
                    Citation.content_hash,
                    DocumentEvidenceSpan.content_hash,
                    DocumentEvidenceSpan.artifact_byte_start,
                    DocumentEvidenceSpan.artifact_byte_end,
                    DocumentChunk.text,
                )
                .join(
                    DocumentEvidenceSpan,
                    and_(
                        DocumentEvidenceSpan.org_id == Citation.org_id,
                        DocumentEvidenceSpan.document_version_id
                        == Citation.document_version_id,
                        DocumentEvidenceSpan.id == Citation.evidence_span_id,
                    ),
                )
                .join(
                    DocumentChunk,
                    and_(
                        DocumentChunk.org_id == DocumentEvidenceSpan.org_id,
                        DocumentChunk.document_version_id
                        == DocumentEvidenceSpan.document_version_id,
                        DocumentChunk.id == DocumentEvidenceSpan.chunk_id,
                    ),
                )
                .where(
                    Citation.org_id == audit.org_id,
                    Citation.workspace_id == audit.workspace_id,
                    Citation.message_id == audit.message_id,
                )
                .order_by(Citation.marker, Citation.id)
                .limit(9)
            )
        ).all()
        rows = [QualityAuditEvidenceRow(*row) for row in raw_rows]
        try:
            evidence = extract_cited_evidence(audit, rows)
        except ValueError:
            return "evidence_snapshot_invalid"
        runtime = await resolve_model_runtime(session, model, settings)
        validator = StrictAnswerValidator(
            AgnoStructuredVerifierStreamer(runtime),
            model_name=model.litellm_model_name,
            entailment_threshold=policy.entailment_threshold,
        )
        await session.rollback()
        return PreparedQualityAudit(
            claim=claim,
            question=question,
            answer=message.content,
            evidence=evidence,
            validator=validator,
        )


async def _set_terminal(
    session_factory: async_sessionmaker[AsyncSession],
    claim: QualityAuditLeaseClaim,
    *,
    status: str,
    error_code: str | None = None,
) -> bool:
    async with session_factory.begin() as session:
        result = await session.execute(
            update(AnswerQualityAudit)
            .where(
                AnswerQualityAudit.id == claim.audit_id,
                AnswerQualityAudit.status == "running",
                AnswerQualityAudit.lease_owner == claim.owner,
                AnswerQualityAudit.lease_token == claim.token,
            )
            .values(
                status=status,
                error_code=error_code,
                finished_at=naive_utc(),
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
            .returning(AnswerQualityAudit.id)
        )
        return result.scalar_one_or_none() == claim.audit_id


async def _retry_or_fail(
    session_factory: async_sessionmaker[AsyncSession],
    claim: QualityAuditLeaseClaim,
    error_code: str,
) -> str:
    status = "queued" if claim.attempt < MAX_QUALITY_AUDIT_ATTEMPTS else "failed"
    async with session_factory.begin() as session:
        result = await session.execute(
            update(AnswerQualityAudit)
            .where(
                AnswerQualityAudit.id == claim.audit_id,
                AnswerQualityAudit.status == "running",
                AnswerQualityAudit.lease_owner == claim.owner,
                AnswerQualityAudit.lease_token == claim.token,
            )
            .values(
                status=status,
                error_code=error_code,
                finished_at=naive_utc() if status == "failed" else None,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
            .returning(AnswerQualityAudit.id)
        )
        return status if result.scalar_one_or_none() else "contested"


async def _complete_quality_audit(
    session_factory: async_sessionmaker[AsyncSession],
    claim: QualityAuditLeaseClaim,
    observation: QualityAuditObservation,
) -> str:
    async with session_factory.begin() as session:
        result = await session.execute(
            update(AnswerQualityAudit)
            .where(
                AnswerQualityAudit.id == claim.audit_id,
                AnswerQualityAudit.status == "running",
                AnswerQualityAudit.lease_owner == claim.owner,
                AnswerQualityAudit.lease_token == claim.token,
            )
            .values(
                status="completed",
                grounding_score=observation.grounding_score,
                completeness_score=observation.completeness_score,
                passed=observation.passed,
                result_code=observation.reason_code,
                error_code=None,
                finished_at=naive_utc(),
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
            .returning(AnswerQualityAudit.id)
        )
        return "completed" if result.scalar_one_or_none() else "contested"


async def _heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    claim: QualityAuditLeaseClaim,
    lease_seconds: int,
) -> None:
    while True:
        await asyncio.sleep(max(10, lease_seconds // 3))
        if not await renew_quality_audit_lease(
            session_factory,
            claim,
            lease_seconds=lease_seconds,
        ):
            return


async def run_quality_audit_once(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    owner: str,
) -> str:
    claim = await claim_next_quality_audit(
        session_factory,
        owner=owner,
        lease_seconds=settings.quality_audit_lease_seconds,
    )
    if claim is None:
        return "idle"
    try:
        prepared = await _prepare_quality_audit(session_factory, claim, settings)
        if isinstance(prepared, str):
            if prepared == "contested":
                return "contested"
            return (
                "skipped"
                if await _set_terminal(
                    session_factory,
                    claim,
                    status="skipped",
                    error_code=prepared,
                )
                else "contested"
            )
        heartbeat = asyncio.create_task(
            _heartbeat(session_factory, claim, settings.quality_audit_lease_seconds)
        )
        try:
            validation = await prepared.validator.validate(
                question=prepared.question,
                answer=prepared.answer,
                evidence=prepared.evidence,
            )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        if (
            validation.status == "unavailable"
            or validation.grounding_score is None
            or validation.completeness_score is None
        ):
            return await _retry_or_fail(
                session_factory,
                claim,
                validation.reason_code,
            )
        return await _complete_quality_audit(
            session_factory,
            claim,
            QualityAuditObservation(
                status=validation.status,
                grounding_score=validation.grounding_score,
                completeness_score=validation.completeness_score,
                reason_code=validation.reason_code,
            ),
        )
    except (OpenRAGError, UpstreamError):
        return await _retry_or_fail(session_factory, claim, "provider_or_config_error")
    except Exception:
        return await _retry_or_fail(session_factory, claim, "internal_error")
