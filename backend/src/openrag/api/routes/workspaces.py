from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.modules.tenancy import service
from openrag.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    require_role,
)
from openrag.modules.tenancy.schemas import MemberAdd, WorkspaceCreate, WorkspaceOut

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContextDep = Annotated[TenantContext, Depends(get_tenant_context)]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]


@router.post("", status_code=201, response_model=WorkspaceOut)
async def create(
    body: WorkspaceCreate,
    session: SessionDep,
    context: AdminDep,
) -> WorkspaceOut:
    workspace = await service.create_workspace(session, context, body.name)
    return WorkspaceOut.model_validate(workspace)


@router.get("", response_model=list[WorkspaceOut])
async def list_(
    session: SessionDep,
    context: ContextDep,
) -> list[WorkspaceOut]:
    workspaces = await service.list_workspaces(session, context)
    return [WorkspaceOut.model_validate(workspace) for workspace in workspaces]


@router.post("/{workspace_id}/members", status_code=204)
async def add_member(
    workspace_id: UUID,
    body: MemberAdd,
    session: SessionDep,
    context: AdminDep,
) -> None:
    await service.add_member(
        session,
        context,
        workspace_id,
        body.user_id,
        body.role,
    )
