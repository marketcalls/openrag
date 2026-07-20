"""Authenticated user controls for governed memory."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.errors import ConflictError
from openrag.modules.memory import service
from openrag.modules.memory.models import MemoryProvenance, MemoryRecord
from openrag.modules.memory.schemas import (
    MemoryCreate,
    MemoryExportOut,
    MemoryForget,
    MemoryOut,
    MemoryPageOut,
    MemoryPatch,
    MemoryPreferenceOut,
    MemoryPreferencePatch,
    MemoryProvenanceOut,
)
from openrag.modules.tenancy.context import TenantContext, get_tenant_context, rate_limit_user

router = APIRouter(prefix="/workspaces/{workspace_id}/memories", tags=["memory"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContextDep = Annotated[TenantContext, Depends(get_tenant_context)]
WriteContextDep = Annotated[
    TenantContext,
    Depends(rate_limit_user("memory_write", 60, 60)),
]


def _out(
    memory: MemoryRecord,
    provenance: tuple[MemoryProvenance, ...] = (),
) -> MemoryOut:
    result = MemoryOut.model_validate(memory)
    return result.model_copy(
        update={
            "provenance": [
                MemoryProvenanceOut.model_validate(item) for item in provenance
            ]
        }
    )


@router.post("", status_code=201, response_model=MemoryOut)
async def create_memory(
    workspace_id: UUID,
    body: MemoryCreate,
    session: SessionDep,
    context: WriteContextDep,
) -> MemoryOut:
    memory = await service.create_memory(session, context, workspace_id, body)
    _, provenance = await service.get_memory(
        session,
        context,
        workspace_id,
        memory.id,
    )
    return _out(memory, provenance)


@router.get("", response_model=MemoryPageOut)
async def list_memories(
    workspace_id: UUID,
    session: SessionDep,
    context: ContextDep,
    include_history: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> MemoryPageOut:
    try:
        page = await service.list_memories(
            session,
            context,
            workspace_id,
            include_history=include_history,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise ConflictError("memory cursor is invalid") from exc
    return MemoryPageOut(
        items=[_out(item, page.provenance.get(item.id, ())) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/export", response_model=MemoryExportOut)
async def export_memories(
    workspace_id: UUID,
    session: SessionDep,
    context: ContextDep,
) -> MemoryExportOut:
    items, provenance, truncated = await service.export_memories(
        session,
        context,
        workspace_id,
    )
    return MemoryExportOut(
        exported_at=datetime.now(UTC),
        items=[_out(item, provenance.get(item.id, ())) for item in items],
        truncated=truncated,
    )


@router.get("/preferences", response_model=MemoryPreferenceOut)
async def get_preferences(
    workspace_id: UUID,
    session: SessionDep,
    context: ContextDep,
) -> MemoryPreferenceOut:
    return MemoryPreferenceOut.model_validate(
        await service.get_preferences(session, context, workspace_id)
    )


@router.patch("/preferences", response_model=MemoryPreferenceOut)
async def update_preferences(
    workspace_id: UUID,
    body: MemoryPreferencePatch,
    session: SessionDep,
    context: WriteContextDep,
) -> MemoryPreferenceOut:
    return MemoryPreferenceOut.model_validate(
        await service.update_preferences(session, context, workspace_id, body)
    )


@router.get("/{memory_id}", response_model=MemoryOut)
async def get_memory(
    workspace_id: UUID,
    memory_id: UUID,
    session: SessionDep,
    context: ContextDep,
) -> MemoryOut:
    memory, provenance = await service.get_memory(
        session,
        context,
        workspace_id,
        memory_id,
    )
    return _out(memory, provenance)


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    workspace_id: UUID,
    memory_id: UUID,
    body: MemoryPatch,
    session: SessionDep,
    context: WriteContextDep,
) -> MemoryOut:
    memory = await service.update_memory(
        session,
        context,
        workspace_id,
        memory_id,
        body,
    )
    _, provenance = await service.get_memory(
        session,
        context,
        workspace_id,
        memory.id,
    )
    return _out(memory, provenance)


@router.post("/{memory_id}/forget", status_code=204)
async def forget_memory(
    workspace_id: UUID,
    memory_id: UUID,
    body: MemoryForget,
    session: SessionDep,
    context: WriteContextDep,
) -> None:
    await service.forget_memory(
        session,
        context,
        workspace_id,
        memory_id,
        body.client_request_id,
    )
