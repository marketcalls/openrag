"""Platform-superadmin RAG operations endpoints."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.errors import InvalidRequestError
from openrag.modules.operations import queries
from openrag.modules.operations.schemas import (
    RagOperationsFilter,
    RagOperationsOverview,
    RagRoute,
    RagRunOutcome,
)
from openrag.modules.tenancy.context import TenantContext, require_platform_superadmin

router = APIRouter(prefix="/admin/rag-operations", tags=["rag-operations"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SuperadminDep = Annotated[TenantContext, Depends(require_platform_superadmin())]


@router.get("/overview", response_model=RagOperationsOverview)
async def overview(
    session: SessionDep,
    context: SuperadminDep,
    from_at: Annotated[datetime, Query(alias="from")],
    to_at: Annotated[datetime, Query(alias="to")],
    org_id: UUID | None = None,
    workspace_id: UUID | None = None,
    route: RagRoute | None = None,
    outcome: RagRunOutcome | None = None,
    model_id: UUID | None = None,
    environment: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    release: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> RagOperationsOverview:
    del context
    try:
        filters = RagOperationsFilter(
            from_at=from_at,
            to_at=to_at,
            org_id=org_id,
            workspace_id=workspace_id,
            route=route,
            outcome=outcome,
            model_id=model_id,
            environment=environment,
            release=release,
        )
    except ValidationError as exc:
        raise InvalidRequestError("invalid RAG operations filters") from exc
    return await queries.get_overview(session, filters)
