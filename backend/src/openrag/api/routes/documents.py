import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.config import get_settings
from openrag.core.errors import ConflictError, PayloadTooLarge
from openrag.modules.documents import service
from openrag.modules.documents.lifecycle import LEGACY_VERSION_KEY, LEGACY_VERSION_LABEL
from openrag.modules.documents.schemas import DocumentOut
from openrag.modules.tenancy.context import TenantContext, get_tenant_context
from openrag.worker.tasks import enqueue_delete, enqueue_ingest

router = APIRouter(tags=["documents"])
_logger = logging.getLogger(__name__)
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContextDep = Annotated[TenantContext, Depends(get_tenant_context)]
_INITIAL_LIFECYCLE_REVISION = 1


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
    if document.size_bytes is None:
        raise RuntimeError("new upload is missing its compatibility size mirror")
    try:
        enqueue_ingest(
            document.id,
            document.size_bytes,
            _INITIAL_LIFECYCLE_REVISION,
        )
    except Exception:
        try:
            await service.mark_retry_dispatch_failed(
                session,
                context,
                document.id,
                expected_revision=_INITIAL_LIFECYCLE_REVISION,
            )
        except Exception:
            _logger.error("legacy upload dispatch compensation failed")
        raise
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
        permission="document.upload",
    )
    versions = await service.list_versions(
        session, context, document.id, permission="document.upload"
    )
    if len(versions) != 1:
        raise ConflictError("legacy delete route cannot target versioned content")
    version = versions[0]
    if not (
        version.id == document.id
        and version.document_id == document.id
        and version.sequence == 1
        and version.version_label == LEGACY_VERSION_LABEL
        and version.version_key == LEGACY_VERSION_KEY
    ):
        raise ConflictError("legacy delete route cannot target versioned content")
    requested = await service.request_document_deletion(
        session, context, version.id
    )
    enqueue_delete(requested.id, context.user_id)
    return {"status": "deletion scheduled"}


@router.post("/document-versions/{version_id}/retry", status_code=202)
async def retry_document_version(
    version_id: UUID,
    session: SessionDep,
    context: ContextDep,
) -> dict[str, str]:
    version = await service.get_version_checked(
        session, context, version_id, permission="document.upload"
    )
    if not (
        version.id == version.document_id
        and version.sequence == 1
        and version.version_label == LEGACY_VERSION_LABEL
        and version.version_key == LEGACY_VERSION_KEY
    ):
        raise ConflictError("nonlegacy retry is not available until event cutover")
    retried = await service.retry_version(session, context, version.id)
    if retried.source_size_bytes is None:
        raise ConflictError("document version has no source size")
    try:
        enqueue_ingest(
            retried.document_id,
            retried.source_size_bytes,
            retried.lifecycle_revision,
        )
    except Exception:
        try:
            await service.mark_retry_dispatch_failed(
                session,
                context,
                retried.id,
                expected_revision=retried.lifecycle_revision,
            )
        except Exception:
            _logger.error("legacy retry dispatch compensation failed")
        raise
    return {"status": "retry scheduled"}
