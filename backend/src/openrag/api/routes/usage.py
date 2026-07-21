from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    require_permission,
)
from openrag.modules.usage import service
from openrag.modules.usage.schemas import (
    OrgQuotaIn,
    OrgQuotaOut,
    UsageMeterOut,
    UserQuotaIn,
    UserQuotaOut,
)

router = APIRouter(tags=["usage"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContextDep = Annotated[TenantContext, Depends(get_tenant_context)]
AdminDep = Annotated[TenantContext, Depends(require_permission("user.manage"))]


@router.get("/usage/me", response_model=UsageMeterOut)
async def usage_me(session: SessionDep, context: ContextDep) -> UsageMeterOut:
    status = await service.get_usage_status(
        session,
        org_id=context.org_id,
        user_id=context.user_id,
    )
    return UsageMeterOut(
        used_tokens=status.used_tokens,
        allocated_tokens=status.allocated_tokens,
        org_used_tokens=status.org_used_tokens,
        org_allocated_tokens=status.org_allocated_tokens,
        resets_at=status.resets_at,
        warning=status.warning,
        blocked=status.blocked,
    )


@router.get("/usage/org/quota", response_model=OrgQuotaOut | None)
async def get_org_quota(session: SessionDep, context: AdminDep) -> OrgQuotaOut | None:
    row = await service.get_org_quota(session, context.org_id)
    return None if row is None else OrgQuotaOut.model_validate(row)


@router.put("/usage/org/quota", response_model=OrgQuotaOut)
async def put_org_quota(
    body: OrgQuotaIn,
    session: SessionDep,
    context: AdminDep,
) -> OrgQuotaOut:
    row = await service.set_org_quota(
        session,
        context,
        monthly_tokens=body.monthly_tokens,
        default_user_monthly_tokens=body.default_user_monthly_tokens,
        reset_day=body.reset_day,
    )
    return OrgQuotaOut.model_validate(row)


@router.get("/users/{user_id}/quota", response_model=UserQuotaOut)
async def get_user_quota(
    user_id: UUID,
    session: SessionDep,
    context: AdminDep,
) -> UserQuotaOut:
    return await service.get_user_quota_with_usage(session, context, user_id)


@router.put("/users/{user_id}/quota", status_code=204)
async def put_user_quota(
    user_id: UUID,
    body: UserQuotaIn,
    session: SessionDep,
    context: AdminDep,
) -> Response:
    await service.set_user_quota(session, context, user_id, body.monthly_tokens)
    return Response(status_code=204)
