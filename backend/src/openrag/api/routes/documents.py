import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.config import get_settings
from openrag.core.errors import ConflictError
from openrag.modules.documents import service
from openrag.modules.documents.lifecycle import LEGACY_VERSION_KEY, LEGACY_VERSION_LABEL
from openrag.modules.documents.schemas import (
    DocumentDetailOut,
    DocumentOut,
    DocumentPatch,
    DocumentVersionDecision,
    DocumentVersionOut,
)
from openrag.modules.documents.uploads import quarantine_upload
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
    sequence: Annotated[int | None, Form()] = None,
) -> DocumentOut:
    if sequence is not None:
        raise HTTPException(status_code=422, detail="sequence is server assigned")
    settings = get_settings()
    async with quarantine_upload(file, settings) as quarantined:
        document = await service.create_from_quarantined_upload(
            session,
            context,
            workspace_id,
            quarantined,
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
    return DocumentOut.from_document(document)


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
    return [DocumentOut.from_document(document) for document in documents]


@router.get("/documents/{document_id}", response_model=DocumentDetailOut)
async def get_document(
    document_id: UUID,
    session: SessionDep,
    context: ContextDep,
) -> DocumentDetailOut:
    document = await service.get_document_checked(session, context, document_id)
    return DocumentDetailOut.from_document(document)


@router.patch("/documents/{document_id}", response_model=DocumentDetailOut)
async def patch_document(
    document_id: UUID,
    patch: DocumentPatch,
    session: SessionDep,
    context: ContextDep,
) -> DocumentDetailOut:
    document = await service.patch_document(
        session,
        context,
        document_id,
        patch.model_dump(exclude_unset=True),
    )
    return DocumentDetailOut.from_document(document)


@router.get(
    "/documents/{document_id}/versions",
    response_model=list[DocumentVersionOut],
)
async def list_document_versions(
    document_id: UUID,
    session: SessionDep,
    context: ContextDep,
) -> list[DocumentVersionOut]:
    versions = await service.list_versions(session, context, document_id)
    return [DocumentVersionOut.from_version(version) for version in versions]


@router.get(
    "/document-versions/{version_id}",
    response_model=DocumentVersionOut,
)
async def get_document_version(
    version_id: UUID,
    session: SessionDep,
    context: ContextDep,
) -> DocumentVersionOut:
    version = await service.get_version_checked(session, context, version_id)
    return DocumentVersionOut.from_version(version)


async def _decide_version(
    action: str,
    version_id: UUID,
    decision: DocumentVersionDecision,
    session: AsyncSession,
    context: TenantContext,
) -> DocumentVersionOut:
    handler = {
        "approve": service.approve_version,
        "reject": service.reject_version,
        "obsolete": service.obsolete_version,
    }[action]
    version = await handler(session, context, version_id, reason=decision.reason)
    return DocumentVersionOut.from_version(version)


@router.post(
    "/document-versions/{version_id}/approve",
    response_model=DocumentVersionOut,
)
async def approve_document_version(
    version_id: UUID,
    decision: DocumentVersionDecision,
    session: SessionDep,
    context: ContextDep,
) -> DocumentVersionOut:
    return await _decide_version("approve", version_id, decision, session, context)


@router.post(
    "/document-versions/{version_id}/reject",
    response_model=DocumentVersionOut,
)
async def reject_document_version(
    version_id: UUID,
    decision: DocumentVersionDecision,
    session: SessionDep,
    context: ContextDep,
) -> DocumentVersionOut:
    return await _decide_version("reject", version_id, decision, session, context)


@router.post(
    "/document-versions/{version_id}/obsolete",
    response_model=DocumentVersionOut,
)
async def obsolete_document_version(
    version_id: UUID,
    decision: DocumentVersionDecision,
    session: SessionDep,
    context: ContextDep,
) -> DocumentVersionOut:
    return await _decide_version("obsolete", version_id, decision, session, context)


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
    requested = await service.request_document_deletion(session, context, version.id)
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
