from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.modules.retrieval.schemas import (
    ChunkOut,
    SearchRequest,
    SearchResponse,
)
from openrag.modules.retrieval.service import retrieve
from openrag.modules.tenancy.context import TenantContext, get_tenant_context

router = APIRouter(tags=["search"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContextDep = Annotated[TenantContext, Depends(get_tenant_context)]


@router.post(
    "/workspaces/{workspace_id}/search",
    response_model=SearchResponse,
)
async def search_workspace(
    workspace_id: UUID,
    body: SearchRequest,
    session: SessionDep,
    context: ContextDep,
) -> SearchResponse:
    result = await retrieve(
        session,
        context,
        workspace_id,
        body.query,
        top_k=body.top_k,
    )
    return SearchResponse(
        no_answer=result.no_answer,
        chunks=[
            ChunkOut(
                document_id=chunk.document_id,
                page=chunk.page,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                score=chunk.score,
            )
            for chunk in result.chunks
        ],
    )
