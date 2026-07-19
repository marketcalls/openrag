"""Async ingestion runners with per-stage persistence and progress."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.core.db import build_engine, build_session_factory, naive_utc
from openrag.core.storage import ObjectStorage, build_storage
from openrag.modules.audit.service import record_audit
from openrag.modules.documents.events import DocumentVersionEventV1
from openrag.modules.documents.lifecycle import (
    LEGACY_VERSION_KEY,
    LEGACY_VERSION_LABEL,
    DocumentVersionState,
)
from openrag.modules.documents.models import (
    Document,
    DocumentBlock,
    DocumentChunk,
    DocumentChunkBlock,
    DocumentEvidenceSpan,
    DocumentVersion,
    DocumentVersionDecisionRecord,
    DocumentVersionProjection,
    IngestJob,
    IngestStageAttempt,
)
from openrag.modules.documents.pipeline import (
    Chunk,
    IngestFailure,
    PageBlock,
    chunk_blocks,
    embed_batch,
    parse_bytes,
    upsert_points,
)
from openrag.modules.events.models import OutboxEvent
from openrag.modules.retrieval.embeddings import get_dense_embedder
from openrag.modules.retrieval.service import (
    delete_document_points,
    delete_document_version_points,
    ensure_collection,
)

_BATCH_SIZE = 32
_LIFECYCLE_EVENT_TYPE = "document.version.lifecycle.v1"


@dataclass(frozen=True)
class _DeletionPlan:
    org_id: UUID
    document_id: UUID
    document_version_id: UUID
    source_storage_key: str
    exact_legacy: bool
    requested_by: UUID


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
    document: Document,
    stage: str,
    expected_revision: int,
) -> IngestJob:
    version = await _lock_active_attempt(session, document, expected_revision)
    job = IngestJob(
        document_id=document.id,
        org_id=document.org_id,
        document_version_id=version.id,
        stage=stage,
        started_at=naive_utc(),
    )
    document.status = "processing"
    document.error = None
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


async def _record_legacy_lifecycle_transition(
    session: AsyncSession,
    document: Document,
    version: DocumentVersion,
    previous_state: str,
    occurred_at: datetime,
) -> None:
    aware_occurred_at = (
        occurred_at.replace(tzinfo=UTC)
        if occurred_at.tzinfo is None
        else occurred_at.astimezone(UTC)
    )
    event = DocumentVersionEventV1(
        org_id=version.org_id,
        workspace_id=version.workspace_id,
        document_id=version.document_id,
        document_version_id=version.id,
        previous_state=DocumentVersionState(previous_state),
        new_state=DocumentVersionState(version.state),
        lifecycle_revision=version.lifecycle_revision,
        actor_id=document.created_by,
        occurred_at=aware_occurred_at,
    )
    session.add(
        OutboxEvent(
            event_id=uuid4(),
            aggregate_type="document_version",
            aggregate_id=version.id,
            event_type=_LIFECYCLE_EVENT_TYPE,
            payload=event.model_dump(mode="json"),
            dedupe_key=event.dedupe_key,
        )
    )
    await record_audit(
        session,
        org_id=version.org_id,
        actor_id=document.created_by,
        action=f"document.version.{version.state}",
        target_type="document_version",
        target_id=str(version.id),
    )


async def _fail_jobs(
    session: AsyncSession,
    document: Document,
    jobs: tuple[IngestJob, ...],
    reason: str,
    expected_revision: int,
) -> None:
    try:
        legacy_version = await _lock_active_attempt(
            session, document, expected_revision
        )
    except IngestFailure:
        await session.rollback()
        return
    document.status = "failed"
    document.error = reason[:1000]
    now = naive_utc()
    for job in jobs:
        job.finished_at = now
        job.error = reason[:1000]
    previous_state = legacy_version.state
    legacy_version.state = "failed"
    legacy_version.provenance_state = "none"
    legacy_version.processing_error_code = "ingest_failed"
    legacy_version.lifecycle_revision += 1
    await _record_legacy_lifecycle_transition(
        session, document, legacy_version, previous_state, now
    )
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


async def _legacy_version(
    session: AsyncSession,
    document: Document,
    *,
    lock: bool = False,
) -> DocumentVersion | None:
    statement = select(DocumentVersion).where(
        DocumentVersion.id == document.id,
        DocumentVersion.org_id == document.org_id,
        DocumentVersion.workspace_id == document.workspace_id,
        DocumentVersion.document_id == document.id,
        DocumentVersion.sequence == 1,
        DocumentVersion.version_label == LEGACY_VERSION_LABEL,
        DocumentVersion.version_key == LEGACY_VERSION_KEY,
    ).execution_options(populate_existing=True)
    if lock:
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


async def _lock_active_attempt(
    session: AsyncSession,
    document: Document,
    expected_revision: int,
) -> DocumentVersion:
    locked_document = (
        await session.execute(
            select(Document)
            .where(
                Document.id == document.id,
                Document.org_id == document.org_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked_document is None:
        raise IngestFailure("stale ingest attempt")
    version = await _legacy_version(session, locked_document, lock=True)
    if (
        version is None
        or version.state != "processing"
        or version.lifecycle_revision != expected_revision
        or version.source_delete_requested_at is not None
    ):
        raise IngestFailure("stale ingest attempt")
    return version


async def run_parse(document_id: UUID, expected_revision: int) -> None:
    async with _session() as session:
        document = await _get_document(session, document_id)
        job = await _start_stage(session, document, "parse", expected_revision)
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
            await _fail_jobs(
                session, document, (job,), str(exc), expected_revision
            )
            raise
        await storage.put(
            storage_key + ".blocks.json",
            json.dumps([asdict(block) for block in blocks]).encode(),
            content_type="application/json",
        )
        legacy_version = await _lock_active_attempt(
            session, document, expected_revision
        )
        document.page_count = max(block.page for block in blocks)
        legacy_version.source_page_count = document.page_count
        await _finish_stage(session, job)


async def run_chunk(document_id: UUID, expected_revision: int) -> None:
    async with _session() as session:
        document = await _get_document(session, document_id)
        job = await _start_stage(session, document, "chunk", expected_revision)
        storage = _storage()
        try:
            storage_key, _filename, _mime = _legacy_ingest_source(document)
        except IngestFailure as exc:
            await _fail_jobs(
                session, document, (job,), str(exc), expected_revision
            )
            raise
        raw = await storage.get(storage_key + ".blocks.json")
        blocks = [PageBlock(**block) for block in json.loads(raw)]
        chunks = chunk_blocks(blocks)
        if not chunks:
            reason = "chunking produced no chunks"
            await _fail_jobs(
                session, document, (job,), reason, expected_revision
            )
            raise IngestFailure(reason)
        await storage.put(
            storage_key + ".chunks.json",
            json.dumps([asdict(chunk) for chunk in chunks]).encode(),
            content_type="application/json",
        )
        await _lock_active_attempt(session, document, expected_revision)
        await _finish_stage(session, job)


async def run_embed_upsert(document_id: UUID, expected_revision: int) -> None:
    async with _session() as session:
        document = await _get_document(session, document_id)
        embed_job = await _start_stage(
            session, document, "embed", expected_revision
        )
        upsert_job = await _start_stage(
            session, document, "upsert", expected_revision
        )
        try:
            storage_key, _filename, mime = _legacy_ingest_source(document)
        except IngestFailure as exc:
            await _fail_jobs(
                session,
                document,
                (embed_job, upsert_job),
                str(exc),
                expected_revision,
            )
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
            await _lock_active_attempt(session, document, expected_revision)
            await session.commit()
        legacy_version = await _lock_active_attempt(
            session, document, expected_revision
        )
        now = naive_utc()
        document.status = "indexed"
        document.error = None
        previous_state = legacy_version.state
        legacy_version.state = "approved"
        legacy_version.provenance_state = "legacy_pending"
        legacy_version.source_page_count = document.page_count
        legacy_version.processing_error_code = None
        legacy_version.lifecycle_revision += 1
        legacy_version.approved_by = document.created_by
        legacy_version.approved_at = now
        legacy_version.decision_at = now
        session.add(
            DocumentVersionDecisionRecord(
                org_id=legacy_version.org_id,
                workspace_id=legacy_version.workspace_id,
                document_id=legacy_version.document_id,
                document_version_id=legacy_version.id,
                lifecycle_revision=legacy_version.lifecycle_revision,
                decision="approved",
                actor_id=document.created_by,
                reason=None,
            )
        )
        for job in (embed_job, upsert_job):
            job.finished_at = now
            job.progress = 1.0
        await _record_legacy_lifecycle_transition(
            session, document, legacy_version, previous_state, now
        )
        await session.commit()


async def _load_deletion_plan(
    session: AsyncSession,
    document_version_id: UUID,
) -> _DeletionPlan | None:
    identity = (
        await session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.id == document_version_id)
        )
    ).scalar_one_or_none()
    if identity is None:
        await session.rollback()
        return None
    document = (
        await session.execute(
            select(Document)
            .where(
                Document.id == identity.document_id,
                Document.org_id == identity.org_id,
                Document.workspace_id == identity.workspace_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if document is None:
        await session.rollback()
        return None
    version = (
        await session.execute(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == document_version_id,
                DocumentVersion.org_id == identity.org_id,
                DocumentVersion.document_id == document.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        version is None
        or version.source_delete_requested_at is None
        or version.source_delete_requested_by is None
        or version.source_deleted_at is not None
        or version.state not in {"draft", "rejected", "failed"}
        or version.source_storage_key is None
    ):
        await session.rollback()
        return None
    governed_decision = await session.scalar(
        select(DocumentVersionDecisionRecord.id)
        .where(
            DocumentVersionDecisionRecord.org_id == version.org_id,
            DocumentVersionDecisionRecord.document_version_id == version.id,
            DocumentVersionDecisionRecord.decision.in_(
                ("approved", "superseded", "obsolete")
            ),
        )
        .limit(1)
    )
    if (
        version.approved_by is not None
        or version.approved_at is not None
        or governed_decision is not None
    ):
        await session.rollback()
        return None
    exact_legacy = (
        version.id == version.document_id
        and version.sequence == 1
        and version.version_label == LEGACY_VERSION_LABEL
        and version.version_key == LEGACY_VERSION_KEY
    )
    plan = _DeletionPlan(
        org_id=version.org_id,
        document_id=version.document_id,
        document_version_id=version.id,
        source_storage_key=version.source_storage_key,
        exact_legacy=exact_legacy,
        requested_by=version.source_delete_requested_by,
    )
    await session.commit()
    return plan


async def _finalize_deletion(
    session: AsyncSession,
    plan: _DeletionPlan,
) -> None:
    document = (
        await session.execute(
            select(Document)
            .where(
                Document.id == plan.document_id,
                Document.org_id == plan.org_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if document is None:
        await session.rollback()
        return
    locked_versions = list(
        (
            await session.execute(
                select(DocumentVersion)
                .where(
                    DocumentVersion.org_id == plan.org_id,
                    DocumentVersion.document_id == plan.document_id,
                )
                .order_by(DocumentVersion.id)
                .with_for_update()
            )
        ).scalars()
    )
    version = next(
        (
            candidate
            for candidate in locked_versions
            if candidate.id == plan.document_version_id
        ),
        None,
    )
    if version is None or version.source_deleted_at is not None:
        await session.rollback()
        return
    if (
        version.source_delete_requested_at is None
        or version.source_delete_requested_by != plan.requested_by
        or version.state not in {"draft", "rejected", "failed"}
    ):
        await session.rollback()
        return

    for model in (
        DocumentEvidenceSpan,
        DocumentChunkBlock,
        DocumentChunk,
        DocumentBlock,
        DocumentVersionProjection,
        IngestStageAttempt,
    ):
        await session.execute(
            sa_delete(model).where(
                model.document_version_id == version.id
            )
        )
    await session.execute(
        sa_delete(IngestJob).where(IngestJob.document_version_id == version.id)
    )
    has_decision = bool(
        await session.scalar(
            select(func.count())
            .select_from(DocumentVersionDecisionRecord)
            .where(DocumentVersionDecisionRecord.document_version_id == version.id)
        )
    )
    version.source_deleted_at = naive_utc()
    await session.flush()
    if not has_decision:
        await session.delete(version)
        await session.flush()
        remaining = await session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.document_id == plan.document_id)
        )
        if not remaining:
            await session.delete(document)
    await record_audit(
        session,
        org_id=plan.org_id,
        actor_id=plan.requested_by,
        action="document.version.source_deleted",
        target_type="document_version",
        target_id=str(plan.document_version_id),
    )
    await session.commit()


async def run_delete(document_version_id: UUID, actor_id: UUID | None) -> None:
    _ = actor_id  # Operational provenance only; the committed marker authorizes cleanup.
    async with _session() as session:
        plan = await _load_deletion_plan(session, document_version_id)
        if plan is None:
            return
        if plan.exact_legacy:
            await delete_document_points(plan.org_id, plan.document_id)
        else:
            await delete_document_version_points(
                plan.org_id, plan.document_version_id
            )
        storage = _storage()
        for key in (
            plan.source_storage_key,
            plan.source_storage_key + ".blocks.json",
            plan.source_storage_key + ".chunks.json",
        ):
            await storage.delete(key)
        await _finalize_deletion(session, plan)


async def mark_failed(
    document_id: UUID,
    expected_revision: int,
    reason: str,
) -> None:
    async with _session() as session:
        document = (
            await session.execute(
                select(Document).where(Document.id == document_id)
            )
        ).scalar_one_or_none()
        if document is not None:
            try:
                legacy_version = await _lock_active_attempt(
                    session, document, expected_revision
                )
            except IngestFailure:
                await session.rollback()
                return
            document.status = "failed"
            document.error = reason[:1000]
            previous_state = legacy_version.state
            legacy_version.state = "failed"
            legacy_version.provenance_state = "none"
            legacy_version.processing_error_code = "ingest_failed"
            legacy_version.lifecycle_revision += 1
            unfinished = list(
                (
                    await session.execute(
                        select(IngestJob).where(
                            IngestJob.document_id == document.id,
                            IngestJob.finished_at.is_(None),
                        )
                    )
                ).scalars()
            )
            now = naive_utc()
            for job in unfinished:
                job.finished_at = now
                job.error = reason[:1000]
            await _record_legacy_lifecycle_transition(
                session, document, legacy_version, previous_state, now
            )
            await session.commit()
