import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from openrag.core.config import get_settings
from openrag.core.db import build_session_factory, naive_utc
from openrag.core.errors import ConflictError
from openrag.core.storage import build_storage
from openrag.modules.audit.models import AuditEvent
from openrag.modules.auth.models import User
from openrag.modules.documents import ingest, service
from openrag.modules.documents.ingest import (
    mark_failed,
    run_chunk,
    run_delete,
    run_embed_upsert,
    run_parse,
)
from openrag.modules.documents.models import (
    Document,
    DocumentBlock,
    DocumentChunk,
    DocumentChunkBlock,
    DocumentEvidenceSpan,
    DocumentVersion,
    DocumentVersionDecisionRecord,
    IngestJob,
)
from openrag.modules.documents.pipeline import IngestFailure
from openrag.modules.documents.service import create_from_upload
from openrag.modules.retrieval.service import retrieve
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace, WorkspaceMember
from tests.modules.documents.test_service import seed_review_version
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

    await run_parse(document.id, 1)
    await run_chunk(document.id, 1)
    await run_embed_upsert(document.id, 1)

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


async def test_late_worker_failure_cannot_regress_approved_legacy_content(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, _workspace, document = await upload(session, "late-legacy-failure")
    version = await session.get(DocumentVersion, document.id)
    assert version is not None
    document_id = document.id
    attempt_revision = version.lifecycle_revision

    await run_parse(document.id, attempt_revision)
    await run_chunk(document.id, attempt_revision)
    await run_embed_upsert(document.id, attempt_revision)
    stale_job = IngestJob(
        document_id=document.id,
        org_id=context.org_id,
        document_version_id=version.id,
        stage="late-duplicate",
        started_at=naive_utc(),
    )
    session.add(stale_job)
    await session.commit()
    await ingest._fail_jobs(
        session,
        document,
        (stale_job,),
        "late stage failure",
        attempt_revision,
    )
    await mark_failed(
        document_id, attempt_revision, "late duplicate task failure"
    )

    await session.refresh(document)
    await session.refresh(version)
    assert (document.status, document.error) == ("indexed", None)
    assert (version.state, version.provenance_state) == (
        "approved",
        "legacy_pending",
    )
    assert version.lifecycle_revision > attempt_revision
    assert version.approved_by == context.user_id
    assert version.approved_at is not None
    assert (
        await session.scalar(
            select(DocumentVersionDecisionRecord.id).where(
                DocumentVersionDecisionRecord.document_version_id == version.id,
                DocumentVersionDecisionRecord.decision == "approved",
            )
        )
        is not None
    )


async def test_dispatch_compensation_fences_a_stale_accepted_worker(
    session: AsyncSession,
    stack_env: None,
) -> None:
    context, _workspace, document = await upload(
        session, "stale-accepted-dispatch"
    )
    version = await session.get(DocumentVersion, document.id)
    assert version is not None
    dispatched_revision = version.lifecycle_revision
    await service.mark_retry_dispatch_failed(
        session,
        context,
        version.id,
        expected_revision=dispatched_revision,
    )

    with pytest.raises(IngestFailure, match="stale ingest attempt"):
        await run_parse(document.id, dispatched_revision)

    await session.refresh(document)
    await session.refresh(version)
    assert (document.status, document.error) == ("failed", "dispatch_failed")
    assert (version.state, version.processing_error_code) == (
        "failed",
        "dispatch_failed",
    )
    assert version.lifecycle_revision == dispatched_revision + 1
    assert (
        await session.scalar(
            select(func.count())
            .select_from(IngestJob)
            .where(IngestJob.document_id == document.id)
        )
        == 0
    )


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
        await run_parse(document.id, 1)

    await session.refresh(document)
    assert document.status == "failed"
    assert document.error


async def test_mark_failed_records_reason(
    session: AsyncSession,
    stack_env: None,
) -> None:
    _context, _workspace, document = await upload(session, "ingest-3")

    await mark_failed(document.id, 1, "boom after retries")

    await session.refresh(document)
    assert document.status == "failed"
    assert document.error == "boom after retries"


@pytest.mark.parametrize("stage", ["chunk", "embed"])
async def test_missing_legacy_source_terminalizes_started_jobs(
    session: AsyncSession,
    stack_env: None,
    stage: str,
) -> None:
    _context, _workspace, document = await upload(session, f"missing-source-{stage}")
    document.storage_key = None
    await session.commit()

    runner = run_chunk if stage == "chunk" else run_embed_upsert
    with pytest.raises(IngestFailure, match="legacy storage key"):
        await runner(document.id, 1)

    await session.refresh(document)
    jobs = list(
        (
            await session.execute(
                select(IngestJob).where(IngestJob.document_id == document.id)
            )
        ).scalars()
    )
    expected_stages = {"chunk"} if stage == "chunk" else {"embed", "upsert"}
    assert {job.stage for job in jobs} == expected_stages
    assert all(job.finished_at is not None and job.error for job in jobs)
    assert document.status == "failed"


async def test_governed_indexed_document_cannot_be_deleted_by_worker_escape_hatch(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace, document = await upload(session, "ingest-4")
    await run_parse(document.id, 1)
    await run_chunk(document.id, 1)
    await run_embed_upsert(document.id, 1)

    await run_delete(document.id, context.user_id)

    stored = (
        await session.execute(select(Document).where(Document.id == document.id))
    ).scalar_one_or_none()
    assert stored is not None
    result = await retrieve(session, context, workspace.id, "invoice 0231")
    assert result.chunks
    actions = [
        event.action
        for event in (await session.execute(select(AuditEvent))).scalars()
    ]
    assert "document.version.source_deleted" not in actions
    await run_delete(document.id, context.user_id)


async def test_delete_is_restartable_and_external_cleanup_has_no_sql_transaction(
    session: AsyncSession,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, document, version, _ = await seed_review_version(
        session,
        "delete-restartable",
        candidate_state="failed",
        candidate_provenance="failed",
    )
    version_id = version.id
    document_id = document.id
    source_storage_key = version.source_storage_key
    await service.request_document_deletion(session, context, version_id)

    active_sessions: list[AsyncSession] = []
    factory = build_session_factory(engine)

    @asynccontextmanager
    async def controlled_session() -> AsyncIterator[AsyncSession]:
        async with factory() as worker_session:
            active_sessions.append(worker_session)
            try:
                yield worker_session
            finally:
                active_sessions.remove(worker_session)

    async def delete_points(_org_id, _document_id):  # type: ignore[no-untyped-def]
        assert active_sessions and not active_sessions[-1].in_transaction()

    class RestartableStorage:
        def __init__(self) -> None:
            self.fail_once = True
            self.deleted: list[str] = []

        async def delete(self, key: str) -> None:
            assert active_sessions and not active_sessions[-1].in_transaction()
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("object store unavailable")
            self.deleted.append(key)

    storage = RestartableStorage()
    monkeypatch.setattr(ingest, "_session", controlled_session)
    monkeypatch.setattr(ingest, "delete_document_points", delete_points)
    monkeypatch.setattr(ingest, "delete_document_version_points", delete_points)
    monkeypatch.setattr(ingest, "_storage", lambda: storage)

    with pytest.raises(RuntimeError, match="object store unavailable"):
        await run_delete(version_id, context.user_id)

    await session.refresh(version)
    assert version.source_delete_requested_at is not None
    assert version.source_deleted_at is None

    await run_delete(version_id, context.user_id)

    assert source_storage_key in storage.deleted
    session.expire_all()
    assert await session.get(DocumentVersion, version_id) is None
    assert await session.get(Document, document_id) is None


async def test_deletion_finalization_locks_document_before_version(
    session: AsyncSession,
    engine: AsyncEngine,
) -> None:
    context, document, version, _ = await seed_review_version(
        session,
        "delete-lock-order",
        candidate_state="failed",
        candidate_provenance="failed",
    )
    await service.request_document_deletion(session, context, version.id)
    plan = ingest._DeletionPlan(
        org_id=context.org_id,
        document_id=document.id,
        document_version_id=version.id,
        source_storage_key=version.source_storage_key,
        exact_legacy=False,
        requested_by=context.user_id,
    )
    factory = build_session_factory(engine)

    async with factory() as blocker:
        await blocker.execute(
            select(Document).where(Document.id == document.id).with_for_update()
        )

        async def finalize() -> None:
            async with factory() as worker:
                await ingest._finalize_deletion(worker, plan)

        finalizer = asyncio.create_task(finalize())
        try:
            deadline = asyncio.get_running_loop().time() + 2
            finalizer_is_waiting = False
            while (
                asyncio.get_running_loop().time() < deadline
                and not finalizer_is_waiting
            ):
                async with factory() as observer:
                    finalizer_is_waiting = bool(
                        await observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE datname=current_database() "
                                "AND wait_event_type='Lock' "
                                "AND query LIKE '%documents%')"
                            )
                        )
                    )
                if not finalizer_is_waiting:
                    await asyncio.sleep(0.01)
            assert finalizer_is_waiting

            # If finalization obeys the global document -> version order, it is
            # still waiting before it can own this version row.
            async with factory() as observer:
                locked = (
                    await observer.execute(
                        select(DocumentVersion)
                        .where(DocumentVersion.id == version.id)
                        .with_for_update(nowait=True)
                    )
                ).scalar_one()
                assert locked.id == version.id
                await observer.rollback()
        finally:
            await blocker.commit()
            await finalizer


async def test_rejected_decision_history_retains_metadata_tombstone_after_purge(
    session: AsyncSession,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, document, version, _ = await seed_review_version(
        session,
        "delete-rejected-tombstone",
        candidate_state="processing",
        candidate_provenance="building",
    )
    block = DocumentBlock(
        org_id=context.org_id,
        document_version_id=version.id,
        ordinal=0,
        text="governed evidence",
        page_number=1,
        locator_kind="page",
        locator_label="1",
        block_type="paragraph",
        section_path=["Scope"],
        extraction_method="parser",
        ocr_profile_version="none/v1",
        content_hash="b" * 64,
    )
    chunk = DocumentChunk(
        org_id=context.org_id,
        document_version_id=version.id,
        ordinal=0,
        text="governed evidence",
        token_count=2,
        page_start=1,
        page_end=1,
        section_path=["Scope"],
        content_hash="c" * 64,
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
    )
    session.add_all([block, chunk])
    await session.flush()
    session.add_all(
        [
            DocumentChunkBlock(
                org_id=context.org_id,
                document_version_id=version.id,
                chunk_id=chunk.id,
                block_id=block.id,
                position=0,
            ),
            DocumentEvidenceSpan(
                org_id=context.org_id,
                document_version_id=version.id,
                chunk_id=chunk.id,
                page_number=1,
                locator_kind="page",
                locator_label="1",
                section_path=["Scope"],
                content_hash="d" * 64,
                ordinal=0,
                token_count=2,
                artifact_byte_start=0,
                artifact_byte_end=18,
            ),
        ]
    )
    await session.commit()
    version.state = "review"
    version.provenance_state = "ready"
    await session.commit()
    await service.reject_version(
        session, context, version.id, reason="Superseded draft content"
    )
    await service.request_document_deletion(session, context, version.id)
    version_id = version.id

    active_sessions: list[AsyncSession] = []
    factory = build_session_factory(engine)

    @asynccontextmanager
    async def controlled_session() -> AsyncIterator[AsyncSession]:
        async with factory() as worker_session:
            active_sessions.append(worker_session)
            try:
                yield worker_session
            finally:
                active_sessions.remove(worker_session)

    async def delete_points(_org_id, _document_id):  # type: ignore[no-untyped-def]
        assert active_sessions and not active_sessions[-1].in_transaction()

    deleted_keys: list[str] = []

    class Storage:
        async def delete(self, key: str) -> None:
            assert active_sessions and not active_sessions[-1].in_transaction()
            deleted_keys.append(key)

    monkeypatch.setattr(ingest, "_session", controlled_session)
    monkeypatch.setattr(ingest, "delete_document_points", delete_points)
    monkeypatch.setattr(ingest, "delete_document_version_points", delete_points)
    monkeypatch.setattr(ingest, "_storage", lambda: Storage())

    await run_delete(version_id, context.user_id)

    session.expire_all()
    tombstone = await session.get(DocumentVersion, version_id)
    assert tombstone is not None
    assert tombstone.state == "rejected"
    assert tombstone.source_delete_requested_at is not None
    assert tombstone.source_deleted_at is not None
    assert tombstone.source_storage_key in deleted_keys
    decision = (
        await session.execute(
            select(DocumentVersionDecisionRecord).where(
                DocumentVersionDecisionRecord.document_version_id == version_id
            )
        )
    ).scalar_one()
    assert decision.reason == "Superseded draft content"
    assert (
        await session.scalar(
            select(DocumentBlock).where(DocumentBlock.document_version_id == version_id)
        )
    ) is None
    assert (
        await session.scalar(
            select(DocumentChunk).where(DocumentChunk.document_version_id == version_id)
        )
    ) is None
    assert (
        await session.scalar(
            select(DocumentEvidenceSpan).where(
                DocumentEvidenceSpan.document_version_id == version_id
            )
        )
    ) is None


async def test_delete_worker_requires_exact_marked_version_identity_before_external_calls(
    session: AsyncSession,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, document, version, _ = await seed_review_version(
        session,
        "delete-exact-version",
        candidate_state="failed",
        candidate_provenance="failed",
    )
    await service.request_document_deletion(session, context, version.id)
    assert version.id != document.id

    factory = build_session_factory(engine)

    @asynccontextmanager
    async def controlled_session() -> AsyncIterator[AsyncSession]:
        async with factory() as worker_session:
            yield worker_session

    external_calls: list[str] = []

    async def delete_points(*args, **kwargs):  # type: ignore[no-untyped-def]
        external_calls.append("qdrant")

    class Storage:
        async def delete(self, key: str) -> None:
            external_calls.append(key)

    monkeypatch.setattr(ingest, "_session", controlled_session)
    monkeypatch.setattr(ingest, "delete_document_points", delete_points)
    monkeypatch.setattr(ingest, "delete_document_version_points", delete_points)
    monkeypatch.setattr(ingest, "_storage", lambda: Storage())

    await run_delete(document.id, context.user_id)

    assert external_calls == []
    await session.refresh(version)
    assert version.source_deleted_at is None


async def test_delete_restarts_after_qdrant_phase_failure(
    session: AsyncSession,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _document, version, _ = await seed_review_version(
        session,
        "delete-qdrant-restart",
        candidate_state="failed",
        candidate_provenance="failed",
    )
    await service.request_document_deletion(session, context, version.id)
    factory = build_session_factory(engine)

    @asynccontextmanager
    async def controlled_session() -> AsyncIterator[AsyncSession]:
        async with factory() as worker_session:
            yield worker_session

    calls: list[str] = []

    async def delete_points(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append("qdrant")
        if calls.count("qdrant") == 1:
            raise RuntimeError("qdrant unavailable")

    class Storage:
        async def delete(self, key: str) -> None:
            calls.append(f"storage:{key}")

    monkeypatch.setattr(ingest, "_session", controlled_session)
    monkeypatch.setattr(ingest, "delete_document_points", delete_points)
    monkeypatch.setattr(ingest, "delete_document_version_points", delete_points)
    monkeypatch.setattr(ingest, "_storage", lambda: Storage())

    with pytest.raises(RuntimeError, match="qdrant unavailable"):
        await run_delete(version.id, context.user_id)
    await session.refresh(version)
    assert version.source_delete_requested_at is not None
    assert version.source_deleted_at is None
    assert calls == ["qdrant"]

    await run_delete(version.id, context.user_id)
    assert calls.count("qdrant") == 2
    assert any(call.startswith("storage:") for call in calls)


async def test_delete_restarts_after_external_cleanup_before_sql_finalization(
    session: AsyncSession,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _document, version, _ = await seed_review_version(
        session,
        "delete-finalize-restart",
        candidate_state="failed",
        candidate_provenance="failed",
    )
    await service.request_document_deletion(session, context, version.id)
    factory = build_session_factory(engine)

    @asynccontextmanager
    async def controlled_session() -> AsyncIterator[AsyncSession]:
        async with factory() as worker_session:
            yield worker_session

    calls: list[str] = []

    async def delete_points(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append("qdrant")

    class Storage:
        async def delete(self, key: str) -> None:
            calls.append(f"storage:{key}")

    original_finalize = ingest._finalize_deletion
    finalize_attempts = 0

    async def fail_first_finalize(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal finalize_attempts
        finalize_attempts += 1
        if finalize_attempts == 1:
            raise RuntimeError("database unavailable after cleanup")
        return await original_finalize(*args, **kwargs)

    monkeypatch.setattr(ingest, "_session", controlled_session)
    monkeypatch.setattr(ingest, "delete_document_points", delete_points)
    monkeypatch.setattr(ingest, "delete_document_version_points", delete_points)
    monkeypatch.setattr(ingest, "_storage", lambda: Storage())
    monkeypatch.setattr(ingest, "_finalize_deletion", fail_first_finalize)

    with pytest.raises(RuntimeError, match="database unavailable after cleanup"):
        await run_delete(version.id, context.user_id)
    await session.refresh(version)
    assert version.source_delete_requested_at is not None
    assert version.source_deleted_at is None
    first_calls = list(calls)
    assert "qdrant" in first_calls and any(
        call.startswith("storage:") for call in first_calls
    )

    await run_delete(version.id, context.user_id)
    assert finalize_attempts == 2
    assert calls.count("qdrant") == 2


async def test_delete_request_replay_by_another_admin_preserves_original_authority(
    session: AsyncSession,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, document, version, _ = await seed_review_version(
        session,
        "delete-cross-admin-replay",
        candidate_state="failed",
        candidate_provenance="failed",
    )
    second_admin = User(
        org_id=context.org_id,
        email="second-admin@delete-replay.example.com",
        password_hash="inert",  # noqa: S106 - persisted test fixture value
    )
    session.add(second_admin)
    await session.flush()
    session.add(
        WorkspaceMember(
            org_id=context.org_id,
            workspace_id=document.workspace_id,
            user_id=second_admin.id,
        )
    )
    await session.commit()
    second_context = TenantContext(
        user_id=second_admin.id,
        org_id=context.org_id,
        authorization=AuthorizationSnapshot(
            user_id=second_admin.id,
            org_id=context.org_id,
            is_platform_superadmin=False,
            org_permissions=context.authorization.org_permissions,
            workspace_permissions=context.authorization.workspace_permissions,
            workspace_ids=context.workspace_ids,
        ),
    )
    version_id = version.id
    original = await service.request_document_deletion(
        session, context, version_id
    )
    original_requested_at = original.source_delete_requested_at
    replay = await service.request_document_deletion(
        session, second_context, version_id
    )
    assert replay.source_delete_requested_at == original_requested_at
    assert replay.source_delete_requested_by == context.user_id

    factory = build_session_factory(engine)

    @asynccontextmanager
    async def controlled_session() -> AsyncIterator[AsyncSession]:
        async with factory() as worker_session:
            yield worker_session

    async def delete_points(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    class Storage:
        async def delete(self, key: str) -> None:
            return None

    monkeypatch.setattr(ingest, "_session", controlled_session)
    monkeypatch.setattr(ingest, "delete_document_points", delete_points)
    monkeypatch.setattr(ingest, "delete_document_version_points", delete_points)
    monkeypatch.setattr(ingest, "_storage", lambda: Storage())

    await run_delete(version_id, second_context.user_id)

    session.expire_all()
    deletion_audit = (
        await session.execute(
            select(AuditEvent).where(
                AuditEvent.action == "document.version.source_deleted",
                AuditEvent.target_id == str(version_id),
            )
        )
    ).scalar_one()
    assert deletion_audit.actor_id == context.user_id


@pytest.mark.parametrize(
    "state", ["processing", "review", "approved", "superseded", "obsolete"]
)
async def test_non_deletable_state_never_reaches_external_cleanup(
    session: AsyncSession,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    context, _document, version, _ = await seed_review_version(
        session,
        f"delete-denied-{state}",
        candidate_state=state,
        candidate_provenance=("ready" if state != "processing" else "building"),
    )
    version_id = version.id

    with pytest.raises(ConflictError):
        await service.request_document_deletion(session, context, version.id)

    factory = build_session_factory(engine)

    @asynccontextmanager
    async def controlled_session() -> AsyncIterator[AsyncSession]:
        async with factory() as worker_session:
            yield worker_session

    external_calls: list[str] = []

    async def delete_points(*args, **kwargs):  # type: ignore[no-untyped-def]
        external_calls.append("qdrant")

    monkeypatch.setattr(ingest, "_session", controlled_session)
    monkeypatch.setattr(ingest, "delete_document_points", delete_points)
    monkeypatch.setattr(ingest, "delete_document_version_points", delete_points)
    await run_delete(version_id, context.user_id)
    assert external_calls == []


async def test_delete_worker_defensively_refuses_governed_decision_history(
    session: AsyncSession,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _document, version, _ = await seed_review_version(
        session,
        "delete-worker-governed-history",
        candidate_state="rejected",
        candidate_provenance="ready",
    )
    version_id = version.id
    version.source_delete_requested_at = naive_utc()
    version.source_delete_requested_by = context.user_id
    session.add(
        DocumentVersionDecisionRecord(
            org_id=context.org_id,
            workspace_id=version.workspace_id,
            document_id=version.document_id,
            document_version_id=version.id,
            lifecycle_revision=version.lifecycle_revision,
            decision="approved",
            actor_id=context.user_id,
            reason=None,
        )
    )
    await session.commit()
    factory = build_session_factory(engine)

    @asynccontextmanager
    async def controlled_session() -> AsyncIterator[AsyncSession]:
        async with factory() as worker_session:
            yield worker_session

    external_calls: list[str] = []

    async def delete_points(*args, **kwargs):  # type: ignore[no-untyped-def]
        external_calls.append("qdrant")

    class Storage:
        async def delete(self, key: str) -> None:
            external_calls.append(key)

    monkeypatch.setattr(ingest, "_session", controlled_session)
    monkeypatch.setattr(ingest, "delete_document_points", delete_points)
    monkeypatch.setattr(ingest, "delete_document_version_points", delete_points)
    monkeypatch.setattr(ingest, "_storage", lambda: Storage())

    await run_delete(version_id, context.user_id)

    assert external_calls == []
    session.expire_all()
    stored = await session.get(DocumentVersion, version_id)
    assert stored is not None
    assert stored.source_deleted_at is None
