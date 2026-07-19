"""Async ingestion runners with per-stage persistence and progress."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.core.db import build_engine, build_session_factory, naive_utc
from openrag.core.storage import ObjectStorage, build_storage
from openrag.modules.audit.service import record_audit
from openrag.modules.documents.models import Document, IngestJob
from openrag.modules.documents.pipeline import (
    Chunk,
    IngestFailure,
    PageBlock,
    chunk_blocks,
    embed_batch,
    parse_bytes,
    upsert_points,
)
from openrag.modules.retrieval.embeddings import get_dense_embedder
from openrag.modules.retrieval.service import (
    delete_document_points,
    ensure_collection,
)

_BATCH_SIZE = 32


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    engine = build_engine(get_settings().database_url)
    try:
        async with build_session_factory(engine)() as session:
            yield session
    finally:
        await engine.dispose()


async def _get_document(
    session: AsyncSession,
    document_id: UUID,
) -> Document:
    document = (
        await session.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if document is None:
        raise IngestFailure(f"document {document_id} no longer exists")
    return document


async def _start_stage(
    session: AsyncSession,
    document_id: UUID,
    stage: str,
) -> IngestJob:
    job = IngestJob(
        document_id=document_id,
        stage=stage,
        started_at=naive_utc(),
    )
    session.add(job)
    await session.commit()
    return job


async def _finish_stage(
    session: AsyncSession,
    job: IngestJob,
    error: str | None = None,
) -> None:
    job.finished_at = naive_utc()
    job.error = error
    if error is None:
        job.progress = 1.0
    await session.commit()


async def _fail(
    session: AsyncSession,
    document: Document,
    job: IngestJob,
    reason: str,
) -> None:
    await _fail_jobs(session, document, (job,), reason)


async def _fail_jobs(
    session: AsyncSession,
    document: Document,
    jobs: tuple[IngestJob, ...],
    reason: str,
) -> None:
    document.status = "failed"
    document.error = reason[:1000]
    for job in jobs:
        job.finished_at = naive_utc()
        job.error = reason[:1000]
    await session.commit()


def _storage() -> ObjectStorage:
    return build_storage(get_settings())


def _legacy_ingest_source(document: Document) -> tuple[str, str, str]:
    """Return compatibility source fields required by the legacy ingest path."""

    if document.storage_key is None:
        raise IngestFailure("document has no legacy storage key")
    if document.filename is None:
        raise IngestFailure("document has no legacy source filename")
    if document.mime is None:
        raise IngestFailure("document has no legacy source MIME type")
    return document.storage_key, document.filename, document.mime


async def run_parse(document_id: UUID) -> None:
    async with _session() as session:
        document = await _get_document(session, document_id)
        job = await _start_stage(session, document_id, "parse")
        storage = _storage()
        try:
            storage_key, filename, _mime = _legacy_ingest_source(document)
            data = await storage.get(storage_key)
            blocks = await asyncio.to_thread(
                parse_bytes,
                data,
                filename,
            )
        except IngestFailure as exc:
            await _fail(session, document, job, str(exc))
            raise
        await storage.put(
            storage_key + ".blocks.json",
            json.dumps([asdict(block) for block in blocks]).encode(),
            content_type="application/json",
        )
        document.status = "processing"
        document.page_count = max(block.page for block in blocks)
        await _finish_stage(session, job)


async def run_chunk(document_id: UUID) -> None:
    async with _session() as session:
        document = await _get_document(session, document_id)
        job = await _start_stage(session, document_id, "chunk")
        storage = _storage()
        try:
            storage_key, _filename, _mime = _legacy_ingest_source(document)
        except IngestFailure as exc:
            await _fail(session, document, job, str(exc))
            raise
        raw = await storage.get(storage_key + ".blocks.json")
        blocks = [PageBlock(**block) for block in json.loads(raw)]
        chunks = chunk_blocks(blocks)
        if not chunks:
            reason = "chunking produced no chunks"
            await _fail(session, document, job, reason)
            raise IngestFailure(reason)
        await storage.put(
            storage_key + ".chunks.json",
            json.dumps([asdict(chunk) for chunk in chunks]).encode(),
            content_type="application/json",
        )
        await _finish_stage(session, job)


async def run_embed_upsert(document_id: UUID) -> None:
    async with _session() as session:
        document = await _get_document(session, document_id)
        embed_job = await _start_stage(session, document_id, "embed")
        upsert_job = await _start_stage(session, document_id, "upsert")
        try:
            storage_key, _filename, mime = _legacy_ingest_source(document)
        except IngestFailure as exc:
            await _fail_jobs(session, document, (embed_job, upsert_job), str(exc))
            raise
        raw = await _storage().get(storage_key + ".chunks.json")
        chunks = [Chunk(**chunk) for chunk in json.loads(raw)]
        await ensure_collection()
        dense_embedder = get_dense_embedder()
        completed = 0
        for start in range(0, len(chunks), _BATCH_SIZE):
            batch = chunks[start : start + _BATCH_SIZE]
            dense, sparse = await embed_batch(
                [chunk.text for chunk in batch],
                dense_embedder,
            )
            await upsert_points(
                org_id=document.org_id,
                workspace_id=document.workspace_id,
                document_id=document.id,
                mime=mime,
                created_at=document.created_at,
                chunks=batch,
                dense=dense,
                sparse=sparse,
            )
            completed += len(batch)
            progress = completed / len(chunks)
            embed_job.progress = progress
            upsert_job.progress = progress
            await session.commit()
        document.status = "indexed"
        await _finish_stage(session, embed_job)
        await _finish_stage(session, upsert_job)


async def run_delete(document_id: UUID, actor_id: UUID | None) -> None:
    async with _session() as session:
        document = (
            await session.execute(
                select(Document).where(Document.id == document_id)
            )
        ).scalar_one_or_none()
        if document is None:
            return

        await delete_document_points(document.org_id, document_id)
        storage = _storage()
        if document.storage_key is not None:
            for key in (
                document.storage_key,
                document.storage_key + ".blocks.json",
                document.storage_key + ".chunks.json",
            ):
                await storage.delete(key)
        await session.execute(
            sa_delete(IngestJob).where(IngestJob.document_id == document_id)
        )
        await record_audit(
            session,
            org_id=document.org_id,
            actor_id=actor_id,
            action="document.deleted",
            target_type="document",
            target_id=str(document_id),
        )
        await session.delete(document)
        await session.commit()


async def mark_failed(document_id: UUID, reason: str) -> None:
    async with _session() as session:
        document = (
            await session.execute(
                select(Document).where(Document.id == document_id)
            )
        ).scalar_one_or_none()
        if document is not None:
            document.status = "failed"
            document.error = reason[:1000]
            await session.commit()
