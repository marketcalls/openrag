"""The single tenant-filtered Qdrant retrieval and deletion path."""

import asyncio
from dataclasses import dataclass
from uuid import UUID

from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
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
    workspace = await get_workspace_checked(
        session,
        context,
        workspace_id,
    )
    await ensure_collection(workspace.embedding_model)
    dense_vector = (await get_dense_embedder().embed([query]))[0]
    sparse_vector = (await asyncio.to_thread(embed_sparse, [query]))[0]
    tenant_filter = _tenant_filter(
        org_id=context.org_id,
        workspace_id=workspace_id,
    )
    client = get_qdrant()
    fused = await client.query_points(
        COLLECTION,
        prefetch=[
            models.Prefetch(
                query=dense_vector,
                using="dense",
                filter=tenant_filter,
                limit=top_k * 4,
            ),
            models.Prefetch(
                query=sparse_vector,
                using="sparse",
                filter=tenant_filter,
                limit=top_k * 4,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=tenant_filter,
        limit=top_k,
        with_payload=True,
    )
    chunks = []
    for point in fused.points:
        payload = point.payload or {}
        chunks.append(
            RetrievedChunk(
                document_id=UUID(str(payload["document_id"])),
                page=int(payload["page"]),
                chunk_index=int(payload["chunk_index"]),
                text=str(payload["text"]),
                score=float(point.score),
            )
        )
    if not chunks:
        return RetrievalResult(chunks=[], no_answer=True)

    top_dense = await client.query_points(
        COLLECTION,
        query=dense_vector,
        using="dense",
        query_filter=tenant_filter,
        limit=1,
        with_payload=False,
    )
    best_cosine = float(top_dense.points[0].score) if top_dense.points else 0.0
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
