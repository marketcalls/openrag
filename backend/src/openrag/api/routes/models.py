from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.config import Settings, get_settings
from openrag.modules.models import catalog, service
from openrag.modules.models.schemas import (
    CatalogCapability,
    ModelCatalogPageOut,
    ModelCreate,
    ModelOut,
    ModelPatch,
    ModelProbeOut,
    ModelPublic,
)
from openrag.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    require_platform_superadmin,
)

router = APIRouter(tags=["models"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ContextDep = Annotated[TenantContext, Depends(get_tenant_context)]
SuperadminDep = Annotated[
    TenantContext,
    Depends(require_platform_superadmin()),
]


@router.get("/admin/model-catalog", response_model=ModelCatalogPageOut)
async def list_model_catalog(
    context: SuperadminDep,
    capability: CatalogCapability | None = None,
    query: Annotated[str | None, Query(max_length=200)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
) -> ModelCatalogPageOut:
    del context
    return catalog.search_catalog(
        capability=capability,
        query=query,
        offset=offset,
        limit=limit,
    )


@router.get("/admin/models", response_model=list[ModelOut])
async def list_models(
    session: SessionDep,
    context: SuperadminDep,
) -> list[ModelOut]:
    return await service.to_model_out(
        session,
        await service.list_models(session),
    )


@router.post("/admin/models", status_code=201, response_model=ModelOut)
async def create_model(
    body: ModelCreate,
    session: SessionDep,
    settings: SettingsDep,
    context: SuperadminDep,
) -> ModelOut:
    model = await service.create_model(
        session,
        context,
        litellm_model_name=body.litellm_model_name,
        display_name=body.display_name,
        provider_kind=body.provider_kind,
        base_url=body.base_url,
        api_key=body.api_key,
        settings=settings,
    )
    return (await service.to_model_out(session, [model]))[0]


@router.patch("/admin/models/{model_id}", response_model=ModelOut)
async def patch_model(
    model_id: UUID,
    body: ModelPatch,
    session: SessionDep,
    settings: SettingsDep,
    context: SuperadminDep,
) -> ModelOut:
    model = await service.update_model(
        session,
        context,
        model_id,
        display_name=body.display_name,
        base_url=body.base_url,
        enabled=body.enabled,
        api_key=body.api_key,
        settings=settings,
        default_reasoning_effort=body.default_reasoning_effort,
        is_utility=body.is_utility,
    )
    return (await service.to_model_out(session, [model]))[0]


@router.delete("/admin/models/{model_id}", status_code=204)
async def delete_model(
    model_id: UUID,
    session: SessionDep,
    context: SuperadminDep,
) -> None:
    await service.delete_model(
        session,
        context,
        model_id,
    )


@router.post(
    "/admin/models/{model_id}/probe",
    status_code=202,
    response_model=ModelProbeOut,
)
async def probe_model(
    model_id: UUID,
    session: SessionDep,
    context: SuperadminDep,
) -> ModelProbeOut:
    return ModelProbeOut.model_validate(
        await service.request_model_probe(session, context, model_id)
    )


@router.get("/models", response_model=list[ModelPublic])
async def list_public_models(
    session: SessionDep,
    context: ContextDep,
) -> list[ModelPublic]:
    return [
        ModelPublic.model_validate(model)
        for model in await service.list_enabled_models(session)
    ]
