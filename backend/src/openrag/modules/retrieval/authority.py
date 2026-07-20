"""PostgreSQL-authoritative revalidation for bounded vector candidates."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.documents.models import (
    Document,
    DocumentChunk,
    DocumentEvidenceSpan,
    DocumentVersion,
)
from openrag.modules.tenancy.context import TenantContext

MAX_CANDIDATES = 128


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    document_version_id: UUID
    evidence_span_id: UUID
    content_hash: str
    fused_score: float
    dense_score: float | None = None
    sparse_score: float | None = None

    def __post_init__(self) -> None:
        if (
            len(self.content_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.content_hash)
        ):
            raise ValueError("candidate_content_hash_invalid")
        scores = (self.fused_score, self.dense_score, self.sparse_score)
        if any(value is not None and not math.isfinite(value) for value in scores):
            raise ValueError("candidate_score_invalid")


@dataclass(frozen=True, slots=True)
class AuthoritySnapshot:
    org_id: UUID
    workspace_id: UUID
    document_version_id: UUID
    evidence_span_id: UUID
    state: str
    provenance_state: str
    superseded_by_id: UUID | None
    effective_at: datetime | None
    expires_at: datetime | None
    source_deleted_at: datetime | None
    source_storage_key: str | None
    acl_policy: Mapping[str, object] | None
    content_hash: str


@dataclass(frozen=True, slots=True)
class AuthorizedEvidence:
    document_id: UUID
    document_version_id: UUID
    evidence_span_id: UUID
    document_name: str
    version_label: str
    section_path: tuple[str, ...]
    locator_kind: str
    locator_label: str
    page_number: int
    chunk_ref: str
    content_hash: str
    text: str
    chunk_index: int
    dense_score: float | None
    sparse_score: float | None
    fused_score: float


def candidate_from_payload(
    payload: Mapping[str, object],
    *,
    fused_score: float,
    dense_score: float | None = None,
    sparse_score: float | None = None,
) -> CandidateIdentity | None:
    """Parse only the bounded derived identity required for SQL revalidation."""

    try:
        return CandidateIdentity(
            document_version_id=UUID(str(payload["document_version_id"])),
            evidence_span_id=UUID(str(payload["evidence_span_id"])),
            content_hash=str(payload["content_hash"]),
            fused_score=fused_score,
            dense_score=dense_score,
            sparse_score=sparse_score,
        )
    except (KeyError, TypeError, ValueError):
        return None


def validate_candidate_batch(
    candidates: Sequence[CandidateIdentity],
) -> tuple[CandidateIdentity, ...]:
    if len(candidates) > MAX_CANDIDATES:
        raise ValueError("candidate_limit_exceeded")
    unique: dict[UUID, CandidateIdentity] = {}
    for candidate in candidates:
        unique.setdefault(candidate.evidence_span_id, candidate)
    return tuple(unique.values())


def _instant(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _acl_allows_workspace(policy: Mapping[str, object] | None) -> bool:
    if policy is None:
        return True
    return set(policy) == {"mode"} and policy.get("mode") == "workspace"


def candidate_is_authorized(
    snapshot: AuthoritySnapshot,
    *,
    org_id: UUID,
    workspace_id: UUID,
    now: datetime,
    expected_content_hash: str,
) -> bool:
    current = _instant(now)
    return (
        snapshot.org_id == org_id
        and snapshot.workspace_id == workspace_id
        and snapshot.state == "approved"
        and snapshot.provenance_state == "ready"
        and snapshot.superseded_by_id is None
        and (
            snapshot.effective_at is None
            or _instant(snapshot.effective_at) <= current
        )
        and (
            snapshot.expires_at is None
            or _instant(snapshot.expires_at) > current
        )
        and snapshot.source_deleted_at is None
        and snapshot.source_storage_key is not None
        and _acl_allows_workspace(snapshot.acl_policy)
        and snapshot.content_hash == expected_content_hash
    )


async def revalidate_candidates(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    candidates: Sequence[CandidateIdentity],
    *,
    now: datetime,
) -> list[AuthorizedEvidence]:
    """Load exact evidence authority once and preserve safe vector rank order."""

    bounded = validate_candidate_batch(candidates)
    if not bounded:
        return []
    span_ids = [candidate.evidence_span_id for candidate in bounded]
    version_ids = [candidate.document_version_id for candidate in bounded]
    database_now = _instant(now).replace(tzinfo=None)
    rows = (
        await session.execute(
            select(Document, DocumentVersion, DocumentEvidenceSpan, DocumentChunk)
            .join(
                DocumentVersion,
                DocumentVersion.document_id == Document.id,
            )
            .join(
                DocumentEvidenceSpan,
                DocumentEvidenceSpan.document_version_id == DocumentVersion.id,
            )
            .join(
                DocumentChunk,
                DocumentChunk.id == DocumentEvidenceSpan.chunk_id,
            )
            .where(
                Document.org_id == context.org_id,
                Document.workspace_id == workspace_id,
                DocumentVersion.org_id == context.org_id,
                DocumentVersion.workspace_id == workspace_id,
                DocumentVersion.id.in_(version_ids),
                DocumentVersion.state == "approved",
                DocumentVersion.provenance_state == "ready",
                DocumentVersion.superseded_by_id.is_(None),
                or_(
                    DocumentVersion.effective_at.is_(None),
                    DocumentVersion.effective_at <= database_now,
                ),
                or_(
                    DocumentVersion.expires_at.is_(None),
                    DocumentVersion.expires_at > database_now,
                ),
                DocumentVersion.source_deleted_at.is_(None),
                DocumentVersion.source_storage_key.is_not(None),
                DocumentEvidenceSpan.org_id == context.org_id,
                DocumentEvidenceSpan.id.in_(span_ids),
                DocumentChunk.org_id == context.org_id,
                DocumentChunk.document_version_id == DocumentVersion.id,
            )
        )
    ).all()
    by_span = {
        evidence.id: (document, version, evidence, chunk)
        for document, version, evidence, chunk in rows
    }
    authorized: list[AuthorizedEvidence] = []
    for candidate in bounded:
        row = by_span.get(candidate.evidence_span_id)
        if row is None:
            continue
        document, version, evidence, chunk = row
        snapshot = AuthoritySnapshot(
            org_id=version.org_id,
            workspace_id=version.workspace_id,
            document_version_id=version.id,
            evidence_span_id=evidence.id,
            state=version.state,
            provenance_state=version.provenance_state,
            superseded_by_id=version.superseded_by_id,
            effective_at=version.effective_at,
            expires_at=version.expires_at,
            source_deleted_at=version.source_deleted_at,
            source_storage_key=version.source_storage_key,
            acl_policy=document.acl_policy,
            content_hash=evidence.content_hash,
        )
        if (
            version.id != candidate.document_version_id
            or not candidate_is_authorized(
                snapshot,
                org_id=context.org_id,
                workspace_id=workspace_id,
                now=now,
                expected_content_hash=candidate.content_hash,
            )
        ):
            continue
        authorized.append(
            AuthorizedEvidence(
                document_id=document.id,
                document_version_id=version.id,
                evidence_span_id=evidence.id,
                document_name=document.name,
                version_label=version.version_label,
                section_path=tuple(evidence.section_path),
                locator_kind=evidence.locator_kind,
                locator_label=evidence.locator_label,
                page_number=evidence.page_number,
                chunk_ref=str(evidence.id),
                content_hash=evidence.content_hash,
                text=chunk.text,
                chunk_index=evidence.ordinal,
                dense_score=candidate.dense_score,
                sparse_score=candidate.sparse_score,
                fused_score=candidate.fused_score,
            )
        )
    return authorized
