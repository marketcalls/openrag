"""Platform-superadmin embedding profile registry routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.config import Settings, get_settings
from openrag.modules.embeddings import service
from openrag.modules.embeddings.schemas import (
    EmbeddingProfileCreate,
    EmbeddingProfileOut,
    EmbeddingProfilePatch,
)
from openrag.modules.tenancy.context import TenantContext, require_platform_superadmin

router = APIRouter(prefix="/admin/embedding-profiles", tags=["embedding-profiles"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SuperadminDep = Annotated[
    TenantContext,
    Depends(require_platform_superadmin()),
]


@router.get("", response_model=list[EmbeddingProfileOut])
async def list_embedding_profiles(
    session: SessionDep,
    context: SuperadminDep,
) -> list[EmbeddingProfileOut]:
    del context
    return [
        EmbeddingProfileOut.model_validate(profile)
        for profile in await service.list_profiles(session)
    ]


@router.post("", status_code=201, response_model=EmbeddingProfileOut)
async def create_embedding_profile(
    body: EmbeddingProfileCreate,
    session: SessionDep,
    settings: SettingsDep,
    context: SuperadminDep,
) -> EmbeddingProfileOut:
    profile = await service.create_profile(session, context, body, settings)
    return EmbeddingProfileOut.model_validate(profile)


@router.patch("/{profile_id}", response_model=EmbeddingProfileOut)
async def patch_embedding_profile(
    profile_id: UUID,
    body: EmbeddingProfilePatch,
    session: SessionDep,
    context: SuperadminDep,
) -> EmbeddingProfileOut:
    profile = await service.update_profile(
        session,
        context,
        profile_id,
        name=body.name,
        enabled=body.enabled,
    )
    return EmbeddingProfileOut.model_validate(profile)
