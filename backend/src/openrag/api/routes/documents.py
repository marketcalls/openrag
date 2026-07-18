from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.config import get_settings
from openrag.core.errors import PayloadTooLarge
from openrag.modules.documents import service
from openrag.modules.documents.schemas import DocumentOut
from openrag.modules.tenancy.context import TenantContext, get_tenant_context
from openrag.worker.tasks import enqueue_delete, enqueue_ingest

router = APIRouter(tags=["documents"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContextDep = Annotated[TenantContext, Depends(get_tenant_context)]


@router.post(
    "/workspaces/{workspace_id}/documents",
    status_code=201,
    response_model=DocumentOut,
)
async def upload_document(
    workspace_id: UUID,
    session: SessionDep,
    context: ContextDep,
    file: Annotated[UploadFile, File()],
) -> DocumentOut:
    data = await file.read()
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise PayloadTooLarge(
            f"file exceeds {settings.max_upload_mb} MB limit"
        )
    document = await service.create_from_upload(
        session,
        context,
        workspace_id,
        filename=file.filename or "upload.bin",
        mime=file.content_type or "application/octet-stream",
        data=data,
    )
    enqueue_ingest(document.id, document.size_bytes)
    return DocumentOut.model_validate(document)


@router.get(
    "/workspaces/{workspace_id}/documents",
    response_model=list[DocumentOut],
)
async def list_workspace_documents(
    workspace_id: UUID,
    session: SessionDep,
    context: ContextDep,
) -> list[DocumentOut]:
    documents = await service.list_documents(
        session,
        context,
        workspace_id,
    )
    return [DocumentOut.model_validate(document) for document in documents]


@router.delete("/documents/{document_id}", status_code=202)
async def delete_document(
    document_id: UUID,
    session: SessionDep,
    context: ContextDep,
) -> dict[str, str]:
    document = await service.get_document_checked(
        session,
        context,
        document_id,
    )
    enqueue_delete(document.id, context.user_id)
    return {"status": "deletion scheduled"}
