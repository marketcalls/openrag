"""The single tenant-filtered Qdrant retrieval and deletion path."""

import asyncio
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from qdrant_client import models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from openrag.core.config import get_settings
from openrag.modules.documents.authority_storage import AuthorityCollectionSpec
from openrag.modules.documents.models import DocumentVersion
from openrag.modules.embeddings.models import EmbeddingDeployment, EmbeddingProfile
from openrag.modules.embeddings.runtime import resolve_profile_runtime
from openrag.modules.retrieval.authority import (
    MAX_CANDIDATES,
    AuthorizedEvidence,
    CandidateIdentity,
    candidate_from_payload,
    revalidate_candidates,
)
from openrag.modules.retrieval.client import COLLECTION, get_qdrant
from openrag.modules.retrieval.embeddings import (
    DenseEmbedder,
    embed_sparse,
    get_dense_embedder,
)
from openrag.modules.retrieval.sufficiency import (
    EvidenceDecision,
    EvidenceStatus,
    SufficiencyPolicy,
    evaluate_evidence,
)
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.service import get_workspace_checked


@dataclass(frozen=True)
class RetrievedChunk:
    document_id: UUID
    page: int
    chunk_index: int
    text: str
    score: float


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
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
    rerank_score: float | None = None

    def __post_init__(self) -> None:
        if not 1 <= len(self.document_name) <= 500:
            raise ValueError("evidence_document_name_invalid")
        if not 1 <= len(self.version_label) <= 200:
            raise ValueError("evidence_version_label_invalid")
        if not 1 <= len(self.section_path) <= 8 or any(
            not 1 <= len(part) <= 200 for part in self.section_path
        ):
            raise ValueError("evidence_section_path_invalid")
        if not 1 <= len(self.locator_kind) <= 32:
            raise ValueError("evidence_locator_kind_invalid")
        if not 1 <= len(self.locator_label) <= 200 or self.page_number <= 0:
            raise ValueError("evidence_locator_invalid")
        if len(self.content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_hash
        ):
            raise ValueError("evidence_content_hash_invalid")
        if not self.text:
            raise ValueError("evidence_text_invalid")
        scores = (
            self.dense_score,
            self.sparse_score,
            self.fused_score,
            self.rerank_score,
        )
        if any(value is not None and not math.isfinite(value) for value in scores):
            raise ValueError("evidence_score_invalid")


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    no_answer: bool
    evidence: tuple[RetrievedEvidence, ...] = ()
    decision: EvidenceDecision | None = None


@dataclass(frozen=True, slots=True)
class CitationEvidenceIdentity:
    """Bounded historic citation identity; never carries trusted source text."""

    document_id: UUID
    document_version_id: UUID
    evidence_span_id: UUID | None
    chunk_ref: str
    content_hash: str | None

    def __post_init__(self) -> None:
        if not 1 <= len(self.chunk_ref) <= 500:
            raise ValueError("citation_chunk_ref_invalid")


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    """Immutable handoff from database authorization to external retrieval."""

    org_id: UUID
    workspace_id: UUID
    query: str
    top_k: int
    min_score: float
    authority_mode: bool
    embedding_model: str
    collection: str
    dense_embedder: DenseEmbedder
    filtered_document_ids: tuple[UUID, ...] | None
    current_approved: bool | None


@dataclass(frozen=True, slots=True)
class ExternalRetrievalResult:
    """Bounded untrusted vector results awaiting SQL authority revalidation."""

    chunks: tuple[RetrievedChunk, ...]
    fused_candidates: tuple[CandidateIdentity, ...]
    dense_candidates: tuple[CandidateIdentity, ...]
    best_cosine: float


def _retrieved_evidence(item: AuthorizedEvidence) -> RetrievedEvidence:
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


def select_final_evidence(
    evidence: list[RetrievedEvidence],
    *,
    top_k: int,
    max_per_document: int = 4,
    max_per_section: int = 2,
) -> tuple[RetrievedEvidence, ...]:
    if not 1 <= top_k <= 32:
        raise ValueError("top_k must be between 1 and 32")
    if not 1 <= max_per_document <= top_k:
        raise ValueError("max_per_document must be between 1 and top_k")
    if not 1 <= max_per_section <= max_per_document:
        raise ValueError("max_per_section must be between 1 and max_per_document")

    selected: list[RetrievedEvidence] = []
    seen_hashes: set[str] = set()
    per_document: dict[UUID, int] = {}
    per_section: dict[tuple[UUID, tuple[str, ...]], int] = {}
    for item in evidence:
        section_key = (item.document_id, item.section_path)
        if (
            item.content_hash in seen_hashes
            or per_document.get(item.document_id, 0) >= max_per_document
            or per_section.get(section_key, 0) >= max_per_section
        ):
            continue
        selected.append(item)
        seen_hashes.add(item.content_hash)
        per_document[item.document_id] = per_document.get(item.document_id, 0) + 1
        per_section[section_key] = per_section.get(section_key, 0) + 1
        if len(selected) == top_k:
            break
    return tuple(selected)


def attach_dense_scores(
    evidence: tuple[RetrievedEvidence, ...],
    scores: Mapping[UUID, float],
) -> tuple[RetrievedEvidence, ...]:
    return tuple(
        replace(item, dense_score=scores.get(item.evidence_span_id, item.dense_score))
        for item in evidence
    )


def retrieval_candidate_limit(*, top_k: int, authority_mode: bool) -> int:
    """Keep authority retrieval broad enough to deduplicate enriched point aliases."""

    if not 1 <= top_k <= 32:
        raise ValueError("top_k must be between 1 and 32")
    return min(MAX_CANDIDATES, top_k * 4) if authority_mode else top_k


def _tenant_filter(
    *,
    org_id: UUID,
    workspace_id: UUID | None = None,
    document_id: UUID | None = None,
    document_version_id: UUID | None = None,
    document_ids: list[UUID] | None = None,
    current_approved: bool | None = None,
) -> models.Filter:
    must: list[models.Condition] = [
        models.FieldCondition(
            key="tenant_id",
            match=models.MatchValue(value=str(org_id)),
        )
    ]
    if workspace_id is not None:
        must.append(
            models.FieldCondition(
                key="workspace_id",
                match=models.MatchValue(value=str(workspace_id)),
            )
        )
    if document_id is not None:
        must.append(
            models.FieldCondition(
                key="document_id",
                match=models.MatchValue(value=str(document_id)),
            )
        )
    if document_version_id is not None:
        must.append(
            models.FieldCondition(
                key="document_version_id",
                match=models.MatchValue(value=str(document_version_id)),
            )
        )
    if document_ids is not None:
        must.append(
            models.FieldCondition(
                key="document_id",
                match=models.MatchAny(any=[str(value) for value in document_ids]),
            )
        )
    if current_approved is not None:
        must.append(
            models.FieldCondition(
                key="is_current_approved",
                match=models.MatchValue(value=current_approved),
            )
        )
    return models.Filter(must=must)


async def ensure_collection(embedding_model: str = "bge-m3") -> str:
    if embedding_model != "bge-m3":
        raise ValueError(f"unsupported embedding model: {embedding_model}")

    client = get_qdrant()
    if not await client.collection_exists(COLLECTION):
        await client.create_collection(
            COLLECTION,
            vectors_config={
                "dense": models.VectorParams(
                    size=get_settings().embedding_dim,
                    distance=models.Distance.COSINE,
                )
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        for field in ("tenant_id", "workspace_id", "document_id"):
            await client.create_payload_index(
                COLLECTION,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
    return COLLECTION


def _document_eligibility(
    *,
    context: TenantContext,
    workspace_id: UUID,
    authority_mode: bool,
    now: datetime,
) -> list[ColumnElement[bool]]:
    eligibility: list[ColumnElement[bool]] = [
        DocumentVersion.org_id == context.org_id,
        DocumentVersion.workspace_id == workspace_id,
        DocumentVersion.state == "approved",
        DocumentVersion.superseded_by_id.is_(None),
    ]
    if authority_mode:
        database_now = now.replace(tzinfo=None)
        eligibility.extend(
            [
                DocumentVersion.provenance_state == "ready",
                DocumentVersion.source_deleted_at.is_(None),
                DocumentVersion.source_storage_key.is_not(None),
                (
                    DocumentVersion.effective_at.is_(None)
                    | (DocumentVersion.effective_at <= database_now)
                ),
                (
                    DocumentVersion.expires_at.is_(None)
                    | (DocumentVersion.expires_at > database_now)
                ),
            ]
        )
    return eligibility


async def prepare_retrieval(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    query: str,
    top_k: int = 8,
) -> RetrievalPlan | RetrievalResult:
    """Authorize and resolve configuration using database work only."""

    if not 1 <= top_k <= 32:
        raise ValueError("top_k must be between 1 and 32")
    workspace = await get_workspace_checked(
        session,
        context,
        workspace_id,
    )
    deployment = await session.scalar(
        select(EmbeddingDeployment).where(EmbeddingDeployment.status == "active")
    )
    now = datetime.now(UTC)
    authority_mode = workspace.document_authority_enabled
    if authority_mode and deployment is None:
        decision = evaluate_evidence(
            query,
            [],
            SufficiencyPolicy(min_dense_score=workspace.min_score),
        )
        return RetrievalResult(
            chunks=[],
            no_answer=True,
            decision=decision,
        )
    eligibility = _document_eligibility(
        context=context,
        workspace_id=workspace_id,
        authority_mode=authority_mode,
        now=now,
    )
    if not authority_mode:
        approved_document_ids = tuple(
            (
                await session.execute(select(DocumentVersion.document_id).where(*eligibility))
            ).scalars()
        )
        if not approved_document_ids:
            return RetrievalResult(chunks=[], no_answer=True)
    else:
        approved_document_ids = ()
        eligible_version = await session.scalar(
            select(DocumentVersion.id).where(*eligibility).limit(1)
        )
        if eligible_version is None:
            decision = evaluate_evidence(
                query,
                [],
                SufficiencyPolicy(min_dense_score=workspace.min_score),
            )
            return RetrievalResult(
                chunks=[],
                no_answer=True,
                decision=decision,
            )
    if not authority_mode:
        collection = COLLECTION
        dense_embedder = get_dense_embedder()
        filtered_document_ids: tuple[UUID, ...] | None = approved_document_ids
        current_approved: bool | None = None
    else:
        assert deployment is not None
        profile = await session.get(EmbeddingProfile, deployment.profile_id)
        if profile is None or not profile.enabled:
            decision = evaluate_evidence(
                query,
                [],
                SufficiencyPolicy(min_dense_score=workspace.min_score),
            )
            return RetrievalResult(
                chunks=[],
                no_answer=True,
                decision=decision,
            )
        runtime = await resolve_profile_runtime(session, profile, get_settings())
        collection = AuthorityCollectionSpec(
            generation_id=deployment.generation_id,
            dense_dimension=runtime.dimension,
        ).physical_collection
        dense_embedder = runtime.embedder
        filtered_document_ids = None
        current_approved = True
    return RetrievalPlan(
        org_id=context.org_id,
        workspace_id=workspace_id,
        query=query,
        top_k=top_k,
        min_score=workspace.min_score,
        authority_mode=authority_mode,
        embedding_model=workspace.embedding_model,
        collection=collection,
        dense_embedder=dense_embedder,
        filtered_document_ids=filtered_document_ids,
        current_approved=current_approved,
    )


async def execute_retrieval(plan: RetrievalPlan) -> ExternalRetrievalResult:
    """Run embeddings and vector I/O without accepting any SQL session."""

    collection = plan.collection
    if not plan.authority_mode:
        collection = await ensure_collection(plan.embedding_model)
    dense_result, sparse_result = await asyncio.gather(
        plan.dense_embedder.embed([plan.query]),
        asyncio.to_thread(embed_sparse, [plan.query]),
    )
    dense_vector = dense_result[0]
    sparse_vector = sparse_result[0]
    tenant_filter = _tenant_filter(
        org_id=plan.org_id,
        workspace_id=plan.workspace_id,
        document_ids=(
            list(plan.filtered_document_ids) if plan.filtered_document_ids is not None else None
        ),
        current_approved=plan.current_approved,
    )
    client = get_qdrant()
    candidate_limit = min(MAX_CANDIDATES, plan.top_k * 4)
    fused_limit = retrieval_candidate_limit(
        top_k=plan.top_k,
        authority_mode=plan.authority_mode,
    )
    fused, top_dense = await asyncio.gather(
        client.query_points(
            collection,
            prefetch=[
                models.Prefetch(
                    query=dense_vector,
                    using="dense",
                    filter=tenant_filter,
                    limit=candidate_limit,
                ),
                models.Prefetch(
                    query=sparse_vector,
                    using="sparse",
                    filter=tenant_filter,
                    limit=candidate_limit,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=tenant_filter,
            limit=fused_limit,
            with_payload=True,
        ),
        client.query_points(
            collection,
            query=dense_vector,
            using="dense",
            query_filter=tenant_filter,
            limit=(1 if not plan.authority_mode else candidate_limit),
            with_payload=plan.authority_mode,
        ),
    )
    chunks: list[RetrievedChunk] = []
    fused_candidates: list[CandidateIdentity] = []
    dense_candidates: list[CandidateIdentity] = []
    best_cosine = 0.0
    if not plan.authority_mode:
        for point in fused.points:
            payload = point.payload or {}
            page_value = payload.get("page")
            if page_value is None:
                page_value = payload["page_number"]
            chunks.append(
                RetrievedChunk(
                    document_id=UUID(str(payload["document_id"])),
                    page=int(page_value),
                    chunk_index=int(payload.get("chunk_index", 0)),
                    text=str(payload["text"]),
                    score=float(point.score),
                )
            )
        best_cosine = float(top_dense.points[0].score) if top_dense.points else 0.0
    else:
        fused_candidates = [
            candidate
            for point in fused.points
            if (
                candidate := candidate_from_payload(
                    point.payload or {},
                    fused_score=float(point.score),
                )
            )
            is not None
        ]
        dense_candidates = [
            candidate
            for point in top_dense.points
            if (
                candidate := candidate_from_payload(
                    point.payload or {},
                    fused_score=float(point.score),
                    dense_score=float(point.score),
                )
            )
            is not None
        ]
    return ExternalRetrievalResult(
        chunks=tuple(chunks),
        fused_candidates=tuple(fused_candidates),
        dense_candidates=tuple(dense_candidates),
        best_cosine=best_cosine,
    )


async def finalize_retrieval(
    session: AsyncSession,
    context: TenantContext,
    plan: RetrievalPlan,
    external: ExternalRetrievalResult,
) -> RetrievalResult:
    """Revalidate vector identities against current PostgreSQL authority."""

    revalidation_now = datetime.now(UTC)
    if not plan.authority_mode:
        current_document_ids = set(
            (
                await session.execute(
                    select(DocumentVersion.document_id).where(
                        *_document_eligibility(
                            context=context,
                            workspace_id=plan.workspace_id,
                            authority_mode=False,
                            now=revalidation_now,
                        )
                    )
                )
            ).scalars()
        )
        chunks = [chunk for chunk in external.chunks if chunk.document_id in current_document_ids]
        return RetrievalResult(
            chunks=chunks,
            no_answer=not chunks or external.best_cosine < plan.min_score,
        )

    authorized = await revalidate_candidates(
        session,
        context,
        plan.workspace_id,
        external.fused_candidates,
        now=revalidation_now,
    )
    final_evidence = select_final_evidence(
        [_retrieved_evidence(item) for item in authorized],
        top_k=plan.top_k,
        max_per_document=min(4, plan.top_k),
        max_per_section=min(2, plan.top_k),
    )
    if final_evidence:
        dense_authority = await revalidate_candidates(
            session,
            context,
            plan.workspace_id,
            external.dense_candidates,
            now=revalidation_now,
        )
        final_evidence = attach_dense_scores(
            final_evidence,
            {
                item.evidence_span_id: item.dense_score
                for item in dense_authority
                if item.dense_score is not None
            },
        )
    decision = evaluate_evidence(
        plan.query,
        final_evidence,
        SufficiencyPolicy(min_dense_score=plan.min_score),
    )
    chunks = [
        RetrievedChunk(
            document_id=evidence.document_id,
            page=evidence.page_number,
            chunk_index=evidence.chunk_index,
            text=evidence.text,
            score=evidence.fused_score,
        )
        for evidence in final_evidence
    ]
    return RetrievalResult(
        chunks=chunks,
        no_answer=decision.status is not EvidenceStatus.SUFFICIENT,
        evidence=final_evidence,
        decision=decision,
    )


def _parse_legacy_chunk_ref(value: str) -> tuple[UUID, int, int] | None:
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        document_id = UUID(parts[0])
        page = int(parts[1])
        chunk_index = int(parts[2])
    except ValueError:
        return None
    if page <= 0 or chunk_index < 0:
        return None
    return document_id, page, chunk_index


async def _backfill_legacy_citations(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    identities: Sequence[CitationEvidenceIdentity],
    *,
    top_k: int,
) -> list[RetrievedChunk]:
    parsed: list[tuple[UUID, int, int]] = []
    seen: set[tuple[UUID, int, int]] = set()
    for identity in identities:
        key = _parse_legacy_chunk_ref(identity.chunk_ref)
        if key is None or key[0] != identity.document_id or key in seen:
            continue
        seen.add(key)
        parsed.append(key)
    if not parsed or not await get_qdrant().collection_exists(COLLECTION):
        return []

    eligible_document_ids = set(
        (
            await session.execute(
                select(DocumentVersion.document_id).where(
                    *_document_eligibility(
                        context=context,
                        workspace_id=workspace_id,
                        authority_mode=False,
                        now=datetime.now(UTC),
                    ),
                    DocumentVersion.document_id.in_([key[0] for key in parsed]),
                )
            )
        ).scalars()
    )
    wanted_by_document: dict[UUID, set[tuple[int, int]]] = {}
    for document_id, page, chunk_index in parsed:
        if document_id in eligible_document_ids:
            wanted_by_document.setdefault(document_id, set()).add((page, chunk_index))

    found: dict[tuple[UUID, int, int], RetrievedChunk] = {}
    client = get_qdrant()
    for document_id, wanted in wanted_by_document.items():
        offset: models.ExtendedPointId | None = None
        for _ in range(40):
            points, offset = await client.scroll(
                COLLECTION,
                scroll_filter=_tenant_filter(
                    org_id=context.org_id,
                    workspace_id=workspace_id,
                    document_id=document_id,
                ),
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                try:
                    payload_document_id = UUID(str(payload["document_id"]))
                    page_value = payload.get("page", payload.get("page_number"))
                    page = int(page_value)  # type: ignore[arg-type]
                    chunk_index = int(payload.get("chunk_index", 0))
                    text = str(payload["text"])
                except (KeyError, TypeError, ValueError):
                    continue
                key = (payload_document_id, page, chunk_index)
                if key in seen:
                    found[key] = RetrievedChunk(
                        document_id=payload_document_id,
                        page=page,
                        chunk_index=chunk_index,
                        text=text,
                        score=0.0,
                    )
            if offset is None or {
                (page, chunk_index)
                for found_document, page, chunk_index in found
                if found_document == document_id
            } >= wanted:
                break
    return [found[key] for key in parsed if key in found][:top_k]


async def backfill_citation_evidence(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    identities: Sequence[CitationEvidenceIdentity],
    top_k: int = 8,
) -> RetrievalResult:
    """Rehydrate historic citation identities through current tenant gates."""

    if not 1 <= top_k <= 32:
        raise ValueError("top_k must be between 1 and 32")
    if len(identities) > 32:
        raise ValueError("citation_identity_limit_exceeded")
    await get_workspace_checked(session, context, workspace_id)

    candidates: list[CandidateIdentity] = []
    expected_documents: dict[UUID, UUID] = {}
    for identity in identities:
        if identity.evidence_span_id is None or identity.content_hash is None:
            continue
        try:
            candidate = CandidateIdentity(
                document_version_id=identity.document_version_id,
                evidence_span_id=identity.evidence_span_id,
                content_hash=identity.content_hash,
                fused_score=0.0,
            )
        except ValueError:
            continue
        candidates.append(candidate)
        expected_documents.setdefault(candidate.evidence_span_id, identity.document_id)
    if candidates:
        authorized = await revalidate_candidates(
            session,
            context,
            workspace_id,
            candidates,
            now=datetime.now(UTC),
        )
        evidence = [
            replace(
                _retrieved_evidence(item),
                dense_score=None,
                sparse_score=None,
                fused_score=0.0,
            )
            for item in authorized
            if expected_documents.get(item.evidence_span_id) == item.document_id
        ]
        selected = select_final_evidence(
            evidence,
            top_k=top_k,
            max_per_document=min(4, top_k),
            max_per_section=min(2, top_k),
        )
        if selected:
            return RetrievalResult(
                chunks=[
                    RetrievedChunk(
                        document_id=item.document_id,
                        page=item.page_number,
                        chunk_index=item.chunk_index,
                        text=item.text,
                        score=0.0,
                    )
                    for item in selected
                ],
                no_answer=False,
                evidence=selected,
            )

    chunks = await _backfill_legacy_citations(
        session,
        context,
        workspace_id,
        identities,
        top_k=top_k,
    )
    return RetrievalResult(chunks=chunks, no_answer=not chunks)


async def retrieve(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    query: str,
    top_k: int = 8,
) -> RetrievalResult:
    """Compose short SQL phases around an explicitly session-free network phase."""

    prepared = await prepare_retrieval(
        session,
        context,
        workspace_id,
        query,
        top_k,
    )
    await session.rollback()
    if isinstance(prepared, RetrievalResult):
        return prepared
    external = await execute_retrieval(prepared)
    return await finalize_retrieval(session, context, prepared, external)


async def delete_document_points(org_id: UUID, document_id: UUID) -> None:
    await get_qdrant().delete(
        COLLECTION,
        points_selector=models.FilterSelector(
            filter=_tenant_filter(org_id=org_id, document_id=document_id)
        ),
        wait=True,
    )


async def delete_document_version_points(
    org_id: UUID,
    document_version_id: UUID,
) -> None:
    await get_qdrant().delete(
        COLLECTION,
        points_selector=models.FilterSelector(
            filter=_tenant_filter(
                org_id=org_id,
                document_version_id=document_version_id,
            )
        ),
        wait=True,
    )
