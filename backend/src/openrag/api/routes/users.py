from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.modules.auth import service
from openrag.modules.auth.schemas import UserOut, UserPatch
from openrag.modules.tenancy.context import TenantContext, require_permission

router = APIRouter(prefix="/users", tags=["users"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[
    TenantContext,
    Depends(require_permission("user.manage")),
]


@router.get("", response_model=list[UserOut])
async def list_users(
    session: SessionDep,
    context: AdminDep,
) -> list[UserOut]:
    users = await service.list_users(session, context)
    return [UserOut.model_validate(user) for user in users]


@router.patch("/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: UUID,
    body: UserPatch,
    session: SessionDep,
    context: AdminDep,
) -> UserOut:
    user = await service.get_user(session, context, user_id)
    if body.active is not None:
        user = await service.set_user_active(
            session,
            context,
            user_id,
            body.active,
        )
    if body.role is not None:
        user = await service.set_user_role(
            session,
            context,
            user_id,
            body.role,
        )
    return UserOut.model_validate(user)
