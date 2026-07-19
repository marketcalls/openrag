"""Platform-superadmin embedding generation deployment routes."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.modules.embeddings import service
from openrag.modules.embeddings.schemas import (
    EmbeddingDeploymentCreate,
    EmbeddingDeploymentOut,
)
from openrag.modules.tenancy.context import TenantContext, require_platform_superadmin

router = APIRouter(
    prefix="/admin/embedding-deployments",
    tags=["embedding-deployments"],
)
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SuperadminDep = Annotated[
    TenantContext,
    Depends(require_platform_superadmin()),
]


@router.get("", response_model=list[EmbeddingDeploymentOut])
async def list_embedding_deployments(
    session: SessionDep,
    context: SuperadminDep,
) -> list[EmbeddingDeploymentOut]:
    del context
    return [
        EmbeddingDeploymentOut.model_validate(deployment)
        for deployment in await service.list_deployments(session)
    ]


@router.post("", status_code=202, response_model=EmbeddingDeploymentOut)
async def request_embedding_deployment(
    body: EmbeddingDeploymentCreate,
    session: SessionDep,
    context: SuperadminDep,
) -> EmbeddingDeploymentOut:
    deployment = await service.request_deployment(
        session,
        context,
        body.profile_id,
    )
    return EmbeddingDeploymentOut.model_validate(deployment)
