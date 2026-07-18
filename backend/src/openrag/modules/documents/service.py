import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.core.errors import ConflictError, NotFoundError
from openrag.core.storage import build_storage
from openrag.modules.audit.service import record_audit
from openrag.modules.documents.models import Document
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.service import get_workspace_checked


async def create_from_upload(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    *,
    filename: str,
    mime: str,
    data: bytes,
) -> Document:
    workspace = await get_workspace_checked(
        session,
        context,
        workspace_id,
        "document.upload",
    )
    content_hash = hashlib.sha256(data).hexdigest()
    duplicate = (
        await session.execute(
            select(Document).where(
                Document.workspace_id == workspace.id,
                Document.content_hash == content_hash,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise ConflictError(
            f"identical content already uploaded as document {duplicate.id}"
        )

    document = Document(
        org_id=context.org_id,
        workspace_id=workspace.id,
        filename=filename,
        mime=mime,
        size_bytes=len(data),
        content_hash=content_hash,
        storage_key="",
        created_by=context.user_id,
    )
    session.add(document)
    await session.flush()
    document.storage_key = (
        f"{context.org_id}/{workspace.id}/{document.id}/{filename}"
    )
    storage = build_storage(get_settings())
    await storage.ensure_bucket()
    await storage.put(document.storage_key, data, content_type=mime)
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="document.uploaded",
        target_type="document",
        target_id=str(document.id),
    )
    await session.commit()
    return document


async def list_documents(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
) -> list[Document]:
    workspace = await get_workspace_checked(
        session,
        context,
        workspace_id,
        "document.read",
    )
    statement = (
        select(Document)
        .where(Document.workspace_id == workspace.id)
        .order_by(Document.created_at.desc())
    )
    return list((await session.execute(statement)).scalars())


async def get_document_checked(
    session: AsyncSession,
    context: TenantContext,
    document_id: UUID,
) -> Document:
    document = (
        await session.execute(
            select(Document).where(
                Document.id == document_id,
                Document.org_id == context.org_id,
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("document not found")
    await get_workspace_checked(
        session,
        context,
        document.workspace_id,
        "document.read",
    )
    return document
