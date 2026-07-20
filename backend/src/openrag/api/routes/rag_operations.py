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
    RagOperationsErrorDetail,
    RagOperationsErrorPage,
    RagOperationsFilter,
    RagOperationsOverview,
    RagOperationsRunOut,
    RagOperationsRunPage,
    RagOperationsSeriesPoint,
    RagRoute,
    RagRunOutcome,
    RagSeriesInterval,
)
from openrag.modules.tenancy.context import TenantContext, require_platform_superadmin

router = APIRouter(prefix="/admin/rag-operations", tags=["rag-operations"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SuperadminDep = Annotated[TenantContext, Depends(require_platform_superadmin())]


def operations_filters(
    from_at: Annotated[datetime, Query(alias="from")],
    to_at: Annotated[datetime, Query(alias="to")],
    org_id: UUID | None = None,
    workspace_id: UUID | None = None,
    route: RagRoute | None = None,
    outcome: RagRunOutcome | None = None,
    model_id: UUID | None = None,
    environment: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    release: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> RagOperationsFilter:
    try:
        return RagOperationsFilter(
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


FiltersDep = Annotated[RagOperationsFilter, Depends(operations_filters)]


def _cursor(value: str | None) -> tuple[datetime, UUID] | None:
    if value is None:
        return None
    try:
        return queries.decode_operations_cursor(value)
    except ValueError as exc:
        raise InvalidRequestError("invalid operations cursor") from exc


@router.get("/overview", response_model=RagOperationsOverview)
async def overview(
    session: SessionDep,
    context: SuperadminDep,
    filters: FiltersDep,
) -> RagOperationsOverview:
    del context
    return await queries.get_overview(session, filters)


@router.get("/series", response_model=list[RagOperationsSeriesPoint])
async def series(
    session: SessionDep,
    context: SuperadminDep,
    filters: FiltersDep,
    interval: RagSeriesInterval = "hour",
) -> list[RagOperationsSeriesPoint]:
    del context
    return await queries.get_series(session, filters, interval=interval)


@router.get("/runs", response_model=RagOperationsRunPage)
async def runs(
    session: SessionDep,
    context: SuperadminDep,
    filters: FiltersDep,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> RagOperationsRunPage:
    del context
    return await queries.list_runs(
        session,
        filters,
        cursor=_cursor(cursor),
        limit=limit,
    )


@router.get("/runs/{run_id}", response_model=RagOperationsRunOut)
async def run_detail(
    run_id: UUID,
    session: SessionDep,
    context: SuperadminDep,
    filters: FiltersDep,
) -> RagOperationsRunOut:
    del context
    return await queries.get_run(session, run_id, filters)


@router.get("/errors", response_model=RagOperationsErrorPage)
async def errors(
    session: SessionDep,
    context: SuperadminDep,
    filters: FiltersDep,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> RagOperationsErrorPage:
    del context
    return await queries.list_errors(
        session,
        filters,
        cursor=_cursor(cursor),
        limit=limit,
    )


@router.get("/errors/{issue_id}", response_model=RagOperationsErrorDetail)
async def error_detail(
    issue_id: UUID,
    session: SessionDep,
    context: SuperadminDep,
    filters: FiltersDep,
) -> RagOperationsErrorDetail:
    del context
    return await queries.get_error(session, issue_id, filters)
