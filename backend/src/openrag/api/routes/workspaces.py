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
from openrag.modules.tenancy.schemas import (
    MemberAdd,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspacePatch,
)

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


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def patch_workspace(
    workspace_id: UUID,
    body: WorkspacePatch,
    session: SessionDep,
    context: AdminDep,
) -> WorkspaceOut:
    if "default_model_id" in body.model_fields_set:
        workspace = await service.set_default_model(
            session,
            context,
            workspace_id,
            body.default_model_id,
        )
    else:
        workspace = await service.get_workspace(
            session,
            context,
            workspace_id,
        )
    return WorkspaceOut.model_validate(workspace)


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
