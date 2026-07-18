from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.config import Settings, get_settings
from openrag.modules.secrets import service
from openrag.modules.secrets.schemas import SecretOut, SecretWrite
from openrag.modules.tenancy.context import TenantContext, require_role

router = APIRouter(prefix="/admin/secrets", tags=["admin"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SuperadminDep = Annotated[TenantContext, Depends(require_role())]


@router.put("/{name}", response_model=SecretOut)
async def put_secret(
    name: str,
    body: SecretWrite,
    session: SessionDep,
    settings: SettingsDep,
    context: SuperadminDep,
) -> SecretOut:
    row = await service.set_secret(
        session,
        actor_id=context.user_id,
        name=name,
        value=body.value,
        settings=settings,
    )
    return SecretOut.model_validate(row)


@router.get("", response_model=list[SecretOut])
async def get_secrets(
    session: SessionDep,
    context: SuperadminDep,
) -> list[SecretOut]:
    return [
        SecretOut.model_validate(secret)
        for secret in await service.list_secrets(session)
    ]
