"""Platform-superadmin embedding generation deployment routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.config import Settings, get_settings
from openrag.core.errors import ConflictError
from openrag.modules.documents.authority_storage import (
    AuthorityCollectionSpec,
    probe_authority_storage,
)
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
SettingsDep = Annotated[Settings, Depends(get_settings)]
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


@router.post(
    "/{deployment_id}/activate",
    response_model=EmbeddingDeploymentOut,
)
async def activate_embedding_deployment(
    deployment_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    context: SuperadminDep,
) -> EmbeddingDeploymentOut:
    deployment = await service.get_deployment(session, deployment_id)
    profile = await service.get_profile(session, deployment.profile_id)
    qdrant = AsyncQdrantClient(url=settings.qdrant_url)
    try:
        status = await probe_authority_storage(
            AuthorityCollectionSpec(
                generation_id=deployment.generation_id,
                dense_dimension=profile.dimension,
            ),
            client=qdrant,
        )
    finally:
        await qdrant.close()
    if not status.ready:
        raise ConflictError("embedding authority storage is not ready")
    activated = await service.activate_deployment(
        session,
        context,
        deployment.id,
    )
    return EmbeddingDeploymentOut.model_validate(activated)
