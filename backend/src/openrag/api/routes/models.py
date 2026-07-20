from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.config import Settings, get_settings
from openrag.modules.models import service
from openrag.modules.models.schemas import (
    ModelCreate,
    ModelOut,
    ModelPatch,
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
        supports_chat_completion=body.supports_chat_completion,
        supports_structured_json=body.supports_structured_json,
        supports_verifier=body.supports_verifier,
        supports_reasoning=body.supports_reasoning,
        default_reasoning_effort=body.default_reasoning_effort,
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
        supports_chat_completion=body.supports_chat_completion,
        supports_structured_json=body.supports_structured_json,
        supports_verifier=body.supports_verifier,
        supports_reasoning=body.supports_reasoning,
        default_reasoning_effort=body.default_reasoning_effort,
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


@router.get("/models", response_model=list[ModelPublic])
async def list_public_models(
    session: SessionDep,
    context: ContextDep,
) -> list[ModelPublic]:
    return [
        ModelPublic.model_validate(model)
        for model in await service.list_enabled_models(session)
    ]
