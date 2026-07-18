import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from openrag.core.storage import build_storage
from openrag.modules.audit.models import AuditEvent
from openrag.modules.documents.service import (
    create_from_upload,
    get_document_checked,
    list_documents,
)
from tests.modules.retrieval.test_retrieve import seed_workspace


async def test_upload_stores_row_object_and_audit(
    session: AsyncSession,
    stack_env: None,
) -> None:
    context, workspace = await seed_workspace(session, "upload-1")

    document = await create_from_upload(
        session,
        context,
        workspace.id,
        filename="a.txt",
        mime="text/plain",
        data=b"hello world",
    )

    assert document.status == "queued"
    assert document.size_bytes == 11
    assert document.storage_key == (
        f"{context.org_id}/{workspace.id}/{document.id}/a.txt"
    )
    storage = build_storage(get_settings())
    assert await storage.get(document.storage_key) == b"hello world"
    actions = [
        event.action
        for event in (await session.execute(select(AuditEvent))).scalars()
    ]
    assert "document.uploaded" in actions


async def test_duplicate_content_conflicts(
    session: AsyncSession,
    stack_env: None,
) -> None:
    context, workspace = await seed_workspace(session, "upload-2")
    await create_from_upload(
        session,
        context,
        workspace.id,
        filename="a.txt",
        mime="text/plain",
        data=b"same bytes",
    )

    with pytest.raises(ConflictError):
        await create_from_upload(
            session,
            context,
            workspace.id,
            filename="b.txt",
            mime="text/plain",
            data=b"same bytes",
        )


async def test_non_member_cannot_upload_or_list(
    session: AsyncSession,
    stack_env: None,
) -> None:
    context, workspace = await seed_workspace(
        session,
        "upload-3",
        member=False,
    )

    with pytest.raises(WorkspaceAccessDenied):
        await create_from_upload(
            session,
            context,
            workspace.id,
            filename="a.txt",
            mime="text/plain",
            data=b"x",
        )
    with pytest.raises(WorkspaceAccessDenied):
        await list_documents(session, context, workspace.id)


async def test_list_and_get_checked(
    session: AsyncSession,
    stack_env: None,
) -> None:
    context, workspace = await seed_workspace(session, "upload-4")
    document = await create_from_upload(
        session,
        context,
        workspace.id,
        filename="a.txt",
        mime="text/plain",
        data=b"abc",
    )

    documents = await list_documents(session, context, workspace.id)

    assert [item.id for item in documents] == [document.id]
    assert (await get_document_checked(session, context, document.id)).id == document.id

    other_context, _ = await seed_workspace(session, "upload-5")
    with pytest.raises(NotFoundError):
        await get_document_checked(session, other_context, document.id)
