"""Tenant-bound execution seam for allowlisted read-only retrieval tools."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.modules.documents import service as documents_service
from openrag.modules.documents.models import (
    Document,
    DocumentEvidenceSpan,
    DocumentVersion,
)
from openrag.modules.orchestration.agent_loop import (
    AgentObservation,
    AgentToolCall,
    AgentToolResult,
    MetadataScalar,
    wrap_untrusted_data,
)
from openrag.modules.retrieval.authority import (
    AuthorizedEvidence,
    CandidateIdentity,
    revalidate_candidates,
)
from openrag.modules.retrieval.service import (
    RetrievalResult,
    RetrievedChunk,
    RetrievedEvidence,
    execute_retrieval,
    finalize_retrieval,
    prepare_retrieval,
    select_final_evidence,
)
from openrag.modules.retrieval.sufficiency import (
    EvidenceStatus,
    SufficiencyPolicy,
    evaluate_evidence,
)
from openrag.modules.tenancy.context import TenantContext

_MAX_RESULTS_PER_CALL = 16
_MAX_EVIDENCE_TEXT_CHARS = 4_000


def merge_authoritative_evidence(
    query: str,
    evidence: Sequence[RetrievedEvidence],
    *,
    top_k: int,
    min_score: float,
) -> RetrievalResult:
    """Deduplicate agent evidence and rerun the deterministic release gate."""

    if len(evidence) > 128:
        raise ValueError("agent_evidence_limit_exceeded")
    by_span: dict[UUID, RetrievedEvidence] = {}
    for item in evidence:
        current = by_span.get(item.evidence_span_id)
        item_rank = (
            item.dense_score if item.dense_score is not None else -1.0,
            item.fused_score,
        )
        current_rank = (
            current.dense_score if current and current.dense_score is not None else -1.0,
            current.fused_score if current else -1.0,
        )
        if current is None or item_rank > current_rank:
            by_span[item.evidence_span_id] = item
    ranked = sorted(
        by_span.values(),
        key=lambda item: (
            item.dense_score if item.dense_score is not None else -1.0,
            item.fused_score,
        ),
        reverse=True,
    )
    selected = select_final_evidence(
        ranked,
        top_k=top_k,
        max_per_document=min(4, top_k),
        max_per_section=min(2, top_k),
    )
    decision = evaluate_evidence(
        query,
        selected,
        SufficiencyPolicy(min_dense_score=min_score),
    )
    return RetrievalResult(
        chunks=[
            RetrievedChunk(
                document_id=item.document_id,
                page=item.page_number,
                chunk_index=item.chunk_index,
                text=item.text,
                score=item.fused_score,
            )
            for item in selected
        ],
        no_answer=decision.status is not EvidenceStatus.SUFFICIENT,
        evidence=selected,
        decision=decision,
    )


@dataclass(frozen=True, slots=True)
class AuthorizedToolEvidence:
    """Evidence already authorized by a tenant-bound backend operation."""

    org_id: UUID
    workspace_id: UUID
    evidence: RetrievedEvidence


class AuthorizedToolBackend(Protocol):
    async def search(
        self,
        query: str,
        metadata: Mapping[str, MetadataScalar] | None,
    ) -> Sequence[AuthorizedToolEvidence]: ...

    async def get_document(
        self,
        document_id: UUID,
    ) -> Sequence[AuthorizedToolEvidence]: ...


def _retrieved(item: AuthorizedEvidence) -> RetrievedEvidence:
    return RetrievedEvidence(
        document_id=item.document_id,
        document_version_id=item.document_version_id,
        evidence_span_id=item.evidence_span_id,
        document_name=item.document_name,
        version_label=item.version_label,
        section_path=item.section_path,
        locator_kind=item.locator_kind,
        locator_label=item.locator_label,
        page_number=item.page_number,
        chunk_ref=item.chunk_ref,
        content_hash=item.content_hash,
        text=item.text,
        chunk_index=item.chunk_index,
        dense_score=item.dense_score,
        sparse_score=item.sparse_score,
        fused_score=item.fused_score,
    )


def _parse_date(value: MetadataScalar, key: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("tool_metadata_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{key}_invalid") from exc
    return parsed.replace(tzinfo=None)


def _metadata_document_query(
    *,
    org_id: UUID,
    workspace_id: UUID,
    metadata: Mapping[str, MetadataScalar],
) -> Select[tuple[UUID]]:
    statement = (
        select(Document.id)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(
            Document.org_id == org_id,
            Document.workspace_id == workspace_id,
            DocumentVersion.org_id == org_id,
            DocumentVersion.workspace_id == workspace_id,
            DocumentVersion.state == "approved",
            DocumentVersion.superseded_by_id.is_(None),
        )
    )
    if value := metadata.get("document_name"):
        statement = statement.where(Document.name == value)
    if value := metadata.get("department"):
        statement = statement.where(Document.department == value)
    if value := metadata.get("document_type"):
        statement = statement.where(Document.document_type == value)
    if value := metadata.get("version_label"):
        statement = statement.where(DocumentVersion.version_label == value)
    if value := metadata.get("revision_date_from"):
        statement = statement.where(
            DocumentVersion.revision_date >= _parse_date(value, "revision_date_from")
        )
    if value := metadata.get("revision_date_to"):
        statement = statement.where(
            DocumentVersion.revision_date <= _parse_date(value, "revision_date_to")
        )
    if value := metadata.get("section"):
        statement = statement.join(
            DocumentEvidenceSpan,
            DocumentEvidenceSpan.document_version_id == DocumentVersion.id,
        ).where(DocumentEvidenceSpan.section_path.contains([value]))
    return statement.distinct().order_by(Document.id).limit(1_001)


class TenantRetrievalBackend:
    """Concrete short-transaction backend for one authorized run scope."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        context: TenantContext,
        workspace_id: UUID,
    ) -> None:
        self._session_factory = session_factory
        self._context = context
        self._workspace_id = workspace_id

    def _wrap(
        self,
        evidence: Sequence[RetrievedEvidence],
    ) -> tuple[AuthorizedToolEvidence, ...]:
        return tuple(
            AuthorizedToolEvidence(
                org_id=self._context.org_id,
                workspace_id=self._workspace_id,
                evidence=item,
            )
            for item in evidence
        )

    async def search(
        self,
        query: str,
        metadata: Mapping[str, MetadataScalar] | None,
    ) -> tuple[AuthorizedToolEvidence, ...]:
        async with self._session_factory() as session:
            prepared = await prepare_retrieval(
                session,
                self._context,
                self._workspace_id,
                query,
            )
            document_ids: tuple[UUID, ...] | None = None
            if metadata:
                document_ids = tuple(
                    (
                        await session.execute(
                            _metadata_document_query(
                                org_id=self._context.org_id,
                                workspace_id=self._workspace_id,
                                metadata=metadata,
                            )
                        )
                    ).scalars()
                )
                if len(document_ids) > 1_000:
                    raise ValueError("tool_metadata_scope_too_broad")
                if not document_ids:
                    return ()
        if isinstance(prepared, RetrievalResult):
            return self._wrap(prepared.evidence)
        if document_ids is not None:
            if prepared.filtered_document_ids is not None:
                allowed = set(prepared.filtered_document_ids)
                document_ids = tuple(value for value in document_ids if value in allowed)
            if not document_ids:
                return ()
            prepared = replace(prepared, filtered_document_ids=document_ids)

        external = await execute_retrieval(prepared)
        async with self._session_factory() as session:
            result = await finalize_retrieval(
                session,
                self._context,
                prepared,
                external,
            )
        return self._wrap(result.evidence)

    async def get_document(
        self,
        document_id: UUID,
    ) -> tuple[AuthorizedToolEvidence, ...]:
        async with self._session_factory() as session:
            document = await documents_service.get_document_checked(
                session,
                self._context,
                document_id,
            )
            if document.workspace_id != self._workspace_id:
                raise ValueError("tool_document_scope_mismatch")
            rows = (
                await session.execute(
                    select(
                        DocumentVersion.id,
                        DocumentEvidenceSpan.id,
                        DocumentEvidenceSpan.content_hash,
                    )
                    .join(
                        DocumentEvidenceSpan,
                        DocumentEvidenceSpan.document_version_id == DocumentVersion.id,
                    )
                    .where(
                        DocumentVersion.org_id == self._context.org_id,
                        DocumentVersion.workspace_id == self._workspace_id,
                        DocumentVersion.document_id == document_id,
                        DocumentVersion.state == "approved",
                        DocumentVersion.superseded_by_id.is_(None),
                    )
                    .order_by(DocumentEvidenceSpan.ordinal)
                    .limit(32)
                )
            ).all()
            candidates = [
                CandidateIdentity(
                    document_version_id=version_id,
                    evidence_span_id=span_id,
                    content_hash=content_hash,
                    fused_score=1.0,
                )
                for version_id, span_id, content_hash in rows
            ]
            authorized = await revalidate_candidates(
                session,
                self._context,
                self._workspace_id,
                candidates,
                now=datetime.now(UTC),
            )
        return self._wrap([_retrieved(item) for item in authorized])

def _render(rows: Sequence[RetrievedEvidence]) -> str:
    if not rows:
        return "No authorized evidence found."
    parts: list[str] = []
    for row in rows:
        parts.append(
            "[source "
            f"ref={row.evidence_span_id} "
            f"document={row.document_name} "
            f"version={row.version_label} "
            f"section={' / '.join(row.section_path)} "
            f"{row.locator_kind}={row.locator_label} "
            f"page={row.page_number}]\n"
            f"{row.text[:_MAX_EVIDENCE_TEXT_CHARS]}"
        )
    return "\n\n".join(parts)


class RetrievalToolExecutor:
    """Dispatch tools through one scope-pinned backend and retain citations."""

    def __init__(
        self,
        backend: AuthorizedToolBackend,
        *,
        org_id: UUID,
        workspace_id: UUID,
    ) -> None:
        self._backend = backend
        self._org_id = org_id
        self._workspace_id = workspace_id
        self._evidence: dict[UUID, RetrievedEvidence] = {}

    @property
    def collected_evidence(self) -> tuple[RetrievedEvidence, ...]:
        return tuple(self._evidence.values())

    def seed(
        self,
        *,
        query: str,
        evidence: Sequence[RetrievedEvidence],
    ) -> AgentObservation:
        call = AgentToolCall(name="search", query=query)
        bounded: list[RetrievedEvidence] = []
        for item in evidence[:_MAX_RESULTS_PER_CALL]:
            if item.evidence_span_id in self._evidence:
                continue
            self._evidence[item.evidence_span_id] = item
            bounded.append(item)
        return AgentObservation(
            call=call,
            text=wrap_untrusted_data(_render(bounded), max_chars=15_000),
            provenance_refs=tuple(str(item.evidence_span_id) for item in bounded),
        )

    async def __call__(self, call: AgentToolCall) -> AgentToolResult:
        if call.name == "get_document":
            assert call.document_id is not None
            rows = await self._backend.get_document(UUID(call.document_id))
        else:
            assert call.query is not None
            rows = await self._backend.search(
                call.query,
                call.metadata if call.name == "search_by_metadata" else None,
            )

        bounded: list[RetrievedEvidence] = []
        for row in rows[:_MAX_RESULTS_PER_CALL]:
            if row.org_id != self._org_id or row.workspace_id != self._workspace_id:
                raise ValueError("tool_evidence_scope_mismatch")
            evidence = row.evidence
            if evidence.evidence_span_id in self._evidence:
                continue
            self._evidence[evidence.evidence_span_id] = evidence
            bounded.append(evidence)

        return AgentToolResult(
            text=_render(bounded),
            provenance_refs=tuple(str(row.evidence_span_id) for row in bounded),
        )
