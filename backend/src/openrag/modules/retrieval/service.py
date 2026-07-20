"""The single tenant-filtered Qdrant retrieval and deletion path."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from qdrant_client import models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.modules.documents.authority_storage import AuthorityCollectionSpec
from openrag.modules.documents.models import DocumentVersion
from openrag.modules.embeddings.models import EmbeddingDeployment, EmbeddingProfile
from openrag.modules.embeddings.runtime import build_profile_runtime
from openrag.modules.retrieval.authority import (
    MAX_CANDIDATES,
    candidate_from_payload,
    revalidate_candidates,
)
from openrag.modules.retrieval.client import COLLECTION, get_qdrant
from openrag.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.service import get_workspace_checked


@dataclass(frozen=True)
class RetrievedChunk:
    document_id: UUID
    page: int
    chunk_index: int
    text: str
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    no_answer: bool


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


async def retrieve(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    query: str,
    top_k: int = 8,
) -> RetrievalResult:
    if not 1 <= top_k <= 32:
        raise ValueError("top_k must be between 1 and 32")
    workspace = await get_workspace_checked(
        session,
        context,
        workspace_id,
    )
    deployment = await session.scalar(
        select(EmbeddingDeployment).where(
            EmbeddingDeployment.status == "active"
        )
    )
    now = datetime.now(UTC)
    eligibility = [
        DocumentVersion.org_id == context.org_id,
        DocumentVersion.workspace_id == workspace_id,
        DocumentVersion.state == "approved",
        DocumentVersion.superseded_by_id.is_(None),
    ]
    if deployment is not None:
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
    if deployment is None:
        approved_document_ids = list(
            (
                await session.execute(
                    select(DocumentVersion.document_id).where(*eligibility)
                )
            ).scalars()
        )
        if not approved_document_ids:
            return RetrievalResult(chunks=[], no_answer=True)
    else:
        approved_document_ids = []
        eligible_version = await session.scalar(
            select(DocumentVersion.id).where(*eligibility).limit(1)
        )
        if eligible_version is None:
            return RetrievalResult(chunks=[], no_answer=True)
    current_approved: bool | None = None
    if deployment is None:
        collection = await ensure_collection(workspace.embedding_model)
        dense_embedder = get_dense_embedder()
        filtered_document_ids: list[UUID] | None = approved_document_ids
    else:
        profile = await session.get(EmbeddingProfile, deployment.profile_id)
        if profile is None or not profile.enabled:
            return RetrievalResult(chunks=[], no_answer=True)
        runtime = build_profile_runtime(profile, get_settings())
        collection = AuthorityCollectionSpec(
            generation_id=deployment.generation_id,
            dense_dimension=runtime.dimension,
        ).physical_collection
        dense_embedder = runtime.embedder
        filtered_document_ids = None
        current_approved = True
    dense_vector = (await dense_embedder.embed([query]))[0]
    sparse_vector = (await asyncio.to_thread(embed_sparse, [query]))[0]
    tenant_filter = _tenant_filter(
        org_id=context.org_id,
        workspace_id=workspace_id,
        document_ids=filtered_document_ids,
        current_approved=current_approved,
    )
    client = get_qdrant()
    fused = await client.query_points(
        collection,
        prefetch=[
            models.Prefetch(
                query=dense_vector,
                using="dense",
                filter=tenant_filter,
                limit=min(MAX_CANDIDATES, top_k * 4),
            ),
            models.Prefetch(
                query=sparse_vector,
                using="sparse",
                filter=tenant_filter,
                limit=min(MAX_CANDIDATES, top_k * 4),
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=tenant_filter,
        limit=top_k,
        with_payload=True,
    )
    chunks: list[RetrievedChunk] = []
    if deployment is None:
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
        authorized = await revalidate_candidates(
            session,
            context,
            workspace_id,
            fused_candidates,
            now=now,
        )
        chunks = [
            RetrievedChunk(
                document_id=evidence.document_id,
                page=evidence.page_number,
                chunk_index=evidence.chunk_index,
                text=evidence.text,
                score=evidence.fused_score,
            )
            for evidence in authorized[:top_k]
        ]
    if not chunks:
        return RetrievalResult(chunks=[], no_answer=True)

    top_dense = await client.query_points(
        collection,
        query=dense_vector,
        using="dense",
        query_filter=tenant_filter,
        limit=(1 if deployment is None else min(MAX_CANDIDATES, top_k * 4)),
        with_payload=deployment is not None,
    )
    if deployment is None:
        best_cosine = float(top_dense.points[0].score) if top_dense.points else 0.0
    else:
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
        dense_authority = await revalidate_candidates(
            session,
            context,
            workspace_id,
            dense_candidates,
            now=now,
        )
        best_cosine = max(
            (
                item.dense_score
                for item in dense_authority
                if item.dense_score is not None
            ),
            default=0.0,
        )
    return RetrievalResult(
        chunks=chunks,
        no_answer=best_cosine < workspace.min_score,
    )


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
