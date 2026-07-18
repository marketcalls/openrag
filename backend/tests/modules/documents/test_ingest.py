import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.core.storage import build_storage
from openrag.modules.audit.models import AuditEvent
from openrag.modules.documents.ingest import (
    mark_failed,
    run_chunk,
    run_delete,
    run_embed_upsert,
    run_parse,
)
from openrag.modules.documents.models import Document, IngestJob
from openrag.modules.documents.pipeline import IngestFailure
from openrag.modules.documents.service import create_from_upload
from openrag.modules.retrieval.service import retrieve
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace
from tests.modules.retrieval.test_retrieve import seed_workspace

TEXT = (
    b"The flux capacitor requires 1.21 gigawatts.\n\n"
    b"Invoice 0231 covers plutonium."
)


async def upload(
    session: AsyncSession,
    name: str,
) -> tuple[TenantContext, Workspace, Document]:
    context, workspace = await seed_workspace(session, name)
    document = await create_from_upload(
        session,
        context,
        workspace.id,
        filename="notes.txt",
        mime="text/plain",
        data=TEXT,
    )
    return context, workspace, document


async def test_full_runner_sequence_indexes_document(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace, document = await upload(session, "ingest-1")

    await run_parse(document.id)
    await run_chunk(document.id)
    await run_embed_upsert(document.id)

    await session.refresh(document)
    assert document.status == "indexed"
    assert document.page_count == 1
    jobs = {
        job.stage: job
        for job in (
            await session.execute(
                select(IngestJob).where(IngestJob.document_id == document.id)
            )
        ).scalars()
    }
    assert set(jobs) == {"parse", "chunk", "embed", "upsert"}
    assert all(
        job.finished_at is not None and job.progress == 1.0
        for job in jobs.values()
    )

    result = await retrieve(session, context, workspace.id, "invoice 0231")
    assert result.chunks
    assert result.chunks[0].document_id == document.id

    artifact = await build_storage(get_settings()).get(
        document.storage_key + ".chunks.json"
    )
    assert json.loads(artifact)


async def test_parse_failure_marks_document_failed(
    session: AsyncSession,
    stack_env: None,
) -> None:
    context, workspace = await seed_workspace(session, "ingest-2")
    document = await create_from_upload(
        session,
        context,
        workspace.id,
        filename="bad.xyz",
        mime="application/octet-stream",
        data=b"\x00junk",
    )

    with pytest.raises(IngestFailure):
        await run_parse(document.id)

    await session.refresh(document)
    assert document.status == "failed"
    assert document.error


async def test_mark_failed_records_reason(
    session: AsyncSession,
    stack_env: None,
) -> None:
    _context, _workspace, document = await upload(session, "ingest-3")

    await mark_failed(document.id, "boom after retries")

    await session.refresh(document)
    assert document.status == "failed"
    assert document.error == "boom after retries"


async def test_delete_propagates_everywhere(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace, document = await upload(session, "ingest-4")
    await run_parse(document.id)
    await run_chunk(document.id)
    await run_embed_upsert(document.id)

    await run_delete(document.id, context.user_id)

    stored = (
        await session.execute(select(Document).where(Document.id == document.id))
    ).scalar_one_or_none()
    assert stored is None
    result = await retrieve(session, context, workspace.id, "invoice 0231")
    assert result.chunks == []
    actions = [
        event.action
        for event in (await session.execute(select(AuditEvent))).scalars()
    ]
    assert "document.deleted" in actions
    await run_delete(document.id, context.user_id)
