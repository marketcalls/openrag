from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.modules.auth import service as auth_service
from openrag.modules.auth.schemas import UserOut
from openrag.modules.tenancy import service
from openrag.modules.tenancy.context import TenantContext, require_permission
from openrag.modules.tenancy.permissions import PERMISSION_CATALOG
from openrag.modules.tenancy.schemas import (
    PermissionCatalogOut,
    RoleBindingReplace,
    RoleCreate,
    RoleOut,
    RolePatch,
)

router = APIRouter(tags=["roles"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
RoleManagerDep = Annotated[
    TenantContext,
    Depends(require_permission("role.manage")),
]


@router.get("/roles/catalog", response_model=list[PermissionCatalogOut])
async def permission_catalog(
    context: RoleManagerDep,
) -> list[PermissionCatalogOut]:
    return [
        PermissionCatalogOut(
            code=item.code,
            label=item.label,
            group=item.group,
            description=item.description,
        )
        for item in PERMISSION_CATALOG
    ]


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(
    session: SessionDep,
    context: RoleManagerDep,
) -> list[RoleOut]:
    return await service.list_roles(session, context)


@router.post("/roles", status_code=201, response_model=RoleOut)
async def create_role(
    body: RoleCreate,
    session: SessionDep,
    context: RoleManagerDep,
) -> RoleOut:
    return await service.create_role(
        session,
        context,
        name=body.name,
        description=body.description,
        permissions=set(body.permissions),
    )


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def patch_role(
    role_id: UUID,
    body: RolePatch,
    session: SessionDep,
    context: RoleManagerDep,
) -> RoleOut:
    return await service.update_role(
        session,
        context,
        role_id,
        name=body.name,
        description=body.description,
        permissions=set(body.permissions) if body.permissions is not None else None,
    )


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(
    role_id: UUID,
    session: SessionDep,
    context: RoleManagerDep,
) -> None:
    await service.delete_role(session, context, role_id)


@router.put(
    "/users/{user_id}/role-bindings",
    response_model=UserOut,
)
async def replace_role_bindings(
    user_id: UUID,
    body: RoleBindingReplace,
    session: SessionDep,
    context: RoleManagerDep,
) -> UserOut:
    user = await service.replace_user_role_bindings(
        session,
        context,
        user_id,
        body.role_ids,
    )
    return await auth_service.user_to_out(session, user)
