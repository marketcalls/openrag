import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.core.db import naive_utc
from openrag.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from openrag.core.storage import build_storage
from openrag.modules.audit.models import AuditEvent
from openrag.modules.documents import service
from openrag.modules.documents.models import (
    Document,
    DocumentVersion,
    DocumentVersionDecisionRecord,
    IngestJob,
)
from openrag.modules.documents.service import (
    create_from_upload,
    get_document_checked,
    list_documents,
)
from openrag.modules.documents.uploads import QuarantinedUpload
from openrag.modules.embeddings.models import EmbeddingDeployment, EmbeddingProfile
from openrag.modules.events.envelopes import (
    INGESTION_REQUESTED_EVENT_TYPE,
    DocumentVersionLifecycleV1,
    build_envelope,
)
from openrag.modules.events.models import OutboxEvent
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext
from tests.modules.retrieval.test_retrieve import seed_workspace


async def seed_review_version(
    session: AsyncSession,
    name: str,
    *,
    incumbent: bool = False,
    candidate_state: str = "review",
    candidate_provenance: str = "ready",
    candidate_page_count: int | None = 2,
    candidate_effective_at: datetime | None = None,
    candidate_expires_at: datetime | None = None,
    incumbent_id: UUID | None = None,
    candidate_id: UUID | None = None,
) -> tuple[TenantContext, Document, DocumentVersion, DocumentVersion | None]:
    context, workspace = await seed_workspace(session, name, role="admin")
    document = Document(
        org_id=context.org_id,
        workspace_id=workspace.id,
        name="Safety manual",
        created_by=context.user_id,
    )
    session.add(document)
    await session.flush()

    old: DocumentVersion | None = None
    if incumbent:
        old = DocumentVersion(
            id=incumbent_id or uuid4(),
            org_id=context.org_id,
            workspace_id=workspace.id,
            document_id=document.id,
            sequence=1,
            version_label="Rev 1",
            version_key="rev 1",
            content_hash=hashlib.sha256(b"old").hexdigest(),
            source_filename="manual.pdf",
            source_mime="application/pdf",
            source_size_bytes=3,
            source_storage_key=f"{context.org_id}/{workspace.id}/{document.id}/{uuid4()}/source",
            source_page_count=1,
            parser_profile_version="docling/v1",
            ocr_profile_version="none/v1",
            chunking_profile_version="semantic/v1",
            embedding_profile_version="bge-m3/v1",
            index_profile_version="hybrid/v1",
            state="approved",
            provenance_state="ready",
            approved_by=context.user_id,
            approved_at=naive_utc(),
            decision_at=naive_utc(),
            created_by=context.user_id,
        )
        session.add(old)
        await session.flush()

    candidate = DocumentVersion(
        id=candidate_id or uuid4(),
        org_id=context.org_id,
        workspace_id=workspace.id,
        document_id=document.id,
        sequence=2 if old else 1,
        version_label="Rev 2" if old else "Rev 1",
        version_key="rev 2" if old else "rev 1",
        content_hash=hashlib.sha256(b"candidate").hexdigest(),
        source_filename="manual.pdf",
        source_mime="application/pdf",
        source_size_bytes=9,
        source_storage_key=f"{context.org_id}/{workspace.id}/{document.id}/{uuid4()}/source",
        source_page_count=candidate_page_count,
        parser_profile_version="docling/v1",
        ocr_profile_version="none/v1",
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
        index_profile_version="hybrid/v1",
        state=candidate_state,
        provenance_state=candidate_provenance,
        effective_at=candidate_effective_at,
        expires_at=candidate_expires_at,
        created_by=context.user_id,
        approved_by=context.user_id if candidate_state == "approved" else None,
        approved_at=naive_utc() if candidate_state == "approved" else None,
        decision_at=naive_utc() if candidate_state == "approved" else None,
    )
    session.add(candidate)
    await session.commit()
    return context, document, candidate, old


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
        f"{context.org_id}/{workspace.id}/{document.id}/{document.id}/source"
    )
    storage = build_storage(get_settings())
    assert await storage.get(document.storage_key) == b"hello world"
    actions = [event.action for event in (await session.execute(select(AuditEvent))).scalars()]
    assert "document.uploaded" in actions
    command = (
        await session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == INGESTION_REQUESTED_EVENT_TYPE
            )
        )
    ).one()
    assert command.aggregate_id == document.id
    assert command.dedupe_key == f"document-version:{document.id}:ingestion:1"


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


async def test_legacy_upload_targets_active_embedding_generation(
    session: AsyncSession,
    stack_env: None,
    tmp_path: Path,
) -> None:
    context, workspace = await seed_workspace(session, "upload-active-generation")
    digest = "b" * 64
    generation_id = uuid4()
    profile = EmbeddingProfile(
        name="Active upload profile",
        name_key="active upload profile",
        provider_kind="hash",
        model_name="openrag-hash-v2",
        dimension=1024,
        max_input_tokens=8192,
        batch_size=32,
        config_digest=digest,
        created_by=context.user_id,
    )
    session.add(profile)
    await session.flush()
    session.add(
        EmbeddingDeployment(
            profile_id=profile.id,
            generation_id=generation_id,
            status="active",
            requested_by=context.user_id,
            activated_by=context.user_id,
            activated_at=naive_utc(),
            total_versions=0,
            completed_versions=0,
            failed_versions=0,
            scan_complete=True,
        )
    )
    await session.commit()

    source = tmp_path / "active.txt"
    source.write_bytes(b"active generation")
    document = await service.create_from_quarantined_upload(
        session,
        context,
        workspace.id,
        QuarantinedUpload(
            filename="active.txt",
            mime="text/plain",
            size_bytes=source.stat().st_size,
            content_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
            path=source,
        ),
    )

    version = await session.get(DocumentVersion, document.id)
    assert version is not None
    assert version.version_label == "Initial 1"
    assert version.embedding_profile_version == f"embedding/v1/{digest}"
    command = (
        await session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == document.id,
                OutboxEvent.event_type == INGESTION_REQUESTED_EVENT_TYPE,
            )
        )
    ).one()
    assert command.payload["payload"]["authority_generation_id"] == str(generation_id)


async def test_non_member_cannot_upload_or_list(
    session: AsyncSession,
    stack_env: None,
) -> None:
    context, workspace = await seed_workspace(
        session,
        "upload-3",
        member=False,
    )
    workspace_id = workspace.id

    with pytest.raises(WorkspaceAccessDenied):
        await create_from_upload(
            session,
            context,
            workspace_id,
            filename="a.txt",
            mime="text/plain",
            data=b"x",
        )
    with pytest.raises(WorkspaceAccessDenied):
        await list_documents(session, context, workspace_id)


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


async def test_approve_supersedes_incumbent_and_writes_atomic_content_free_records(
    session: AsyncSession,
) -> None:
    context, document, candidate, incumbent = await seed_review_version(
        session, "approve-atomic", incumbent=True
    )
    assert incumbent is not None

    approved = await service.approve_version(
        session,
        context,
        candidate.id,
        reason="Reviewed against controlled copy",
    )

    await session.refresh(incumbent)
    await session.refresh(document)
    assert approved.state == "approved"
    assert document.status == "indexed"
    assert document.page_count == candidate.source_page_count
    assert approved.lifecycle_revision == 2
    assert approved.approved_by == context.user_id
    assert incumbent.state == "superseded"
    assert incumbent.superseded_by_id == approved.id
    assert incumbent.lifecycle_revision == 2
    decisions = list(
        (
            await session.execute(
                select(DocumentVersionDecisionRecord).order_by(
                    DocumentVersionDecisionRecord.created_at
                )
            )
        ).scalars()
    )
    assert [(row.decision, row.document_version_id) for row in decisions] == [
        ("superseded", incumbent.id),
        ("approved", candidate.id),
    ]
    assert decisions[-1].reason == "Reviewed against controlled copy"
    events = list((await session.execute(select(OutboxEvent))).scalars())
    assert len(events) == 2
    serialized = repr([event.payload for event in events])
    for forbidden in (
        "Reviewed against controlled copy",
        "Safety manual",
        "manual.pdf",
        candidate.content_hash,
        candidate.source_storage_key,
    ):
        assert forbidden not in serialized
    assert {event.dedupe_key for event in events} == {
        f"document-version:{incumbent.id}:2",
        f"document-version:{candidate.id}:2",
    }


async def test_approve_oracle_distinguishes_foreign_object_from_missing_capability(
    session: AsyncSession,
) -> None:
    context, _document, candidate, _ = await seed_review_version(session, "approve-oracle")
    restricted = TenantContext(
        user_id=context.user_id,
        org_id=context.org_id,
        authorization=AuthorizationSnapshot(
            user_id=context.user_id,
            org_id=context.org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset({"document.read", "document.upload"}),
            workspace_permissions={},
            workspace_ids=context.workspace_ids,
        ),
    )
    candidate_id = candidate.id
    with pytest.raises(WorkspaceAccessDenied):
        await service.approve_version(session, restricted, candidate_id, reason=None)

    foreign_context, _ = await seed_workspace(session, "approve-foreign", role="admin")
    with pytest.raises(NotFoundError):
        await service.approve_version(session, foreign_context, candidate_id, reason=None)


@pytest.mark.parametrize(
    "command_name",
    ["approve_version", "reject_version", "obsolete_version"],
)
async def test_governance_reason_validation_does_not_bypass_object_oracle(
    session: AsyncSession,
    command_name: str,
) -> None:
    context, _document, candidate, _ = await seed_review_version(
        session,
        f"reason-oracle-{command_name}",
        candidate_state=("approved" if command_name == "obsolete_version" else "review"),
    )
    restricted = TenantContext(
        user_id=context.user_id,
        org_id=context.org_id,
        authorization=AuthorizationSnapshot(
            user_id=context.user_id,
            org_id=context.org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset({"document.read", "document.upload"}),
            workspace_permissions={},
            workspace_ids=context.workspace_ids,
        ),
    )
    command = getattr(service, command_name)
    candidate_id = candidate.id
    with pytest.raises(WorkspaceAccessDenied):
        await command(session, restricted, candidate_id, reason=" ")

    foreign_context, _ = await seed_workspace(
        session, f"reason-oracle-foreign-{command_name}", role="admin"
    )
    with pytest.raises(NotFoundError):
        await command(session, foreign_context, candidate_id, reason=" ")


async def test_retry_rejects_recent_processing_legacy_attempt(
    session: AsyncSession,
    stack_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, workspace = await seed_workspace(session, "recent-legacy-recovery", role="admin")
    document = await create_from_upload(
        session,
        context,
        workspace.id,
        filename="recent.txt",
        mime="text/plain",
        data=b"recent attempt",
    )
    version = await session.get(DocumentVersion, document.id)
    assert version is not None
    database_now = await session.scalar(select(func.timezone("UTC", func.now())))
    assert isinstance(database_now, datetime)
    version.updated_at = database_now - timedelta(seconds=901)
    session.add(
        IngestJob(
            org_id=context.org_id,
            document_id=document.id,
            document_version_id=None,
            stage="parse",
            started_at=database_now,
        )
    )
    await session.commit()
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            stale_ingest_recovery_seconds=900,
            authority_generation_id=get_settings().authority_generation_id,
        ),
    )

    with pytest.raises(ConflictError, match="ingest attempt is still active"):
        await service.retry_version(session, context, version.id)

    await session.refresh(version)
    assert version.lifecycle_revision == 1
    assert len(list((await session.execute(select(OutboxEvent))).scalars())) == 1


async def test_retry_recovers_stale_processing_legacy_attempt_with_fence(
    session: AsyncSession,
    stack_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, workspace = await seed_workspace(session, "stale-legacy-recovery", role="admin")
    document = await create_from_upload(
        session,
        context,
        workspace.id,
        filename="stale.txt",
        mime="text/plain",
        data=b"stale attempt",
    )
    version = await session.get(DocumentVersion, document.id)
    assert version is not None
    database_now = await session.scalar(select(func.timezone("UTC", func.now())))
    assert isinstance(database_now, datetime)
    stale_at = database_now - timedelta(seconds=901)
    version.updated_at = stale_at
    job = IngestJob(
        org_id=context.org_id,
        document_id=document.id,
        document_version_id=None,
        stage="upsert",
        started_at=stale_at,
    )
    session.add(job)
    await session.commit()
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            stale_ingest_recovery_seconds=900,
            authority_generation_id=get_settings().authority_generation_id,
        ),
    )

    recovered = await service.retry_version(session, context, version.id)

    assert (recovered.state, recovered.lifecycle_revision) == ("processing", 2)
    await session.refresh(job)
    assert job.finished_at is not None
    assert job.error == "superseded_by_v2_recovery"
    events = list((await session.execute(select(OutboxEvent))).scalars())
    assert len(events) == 3
    lifecycle = next(
        event for event in events if event.payload["event_type"] == "document.version.lifecycle.v1"
    )
    assert lifecycle.payload["payload"]["previous_state"] == "processing"
    assert lifecycle.payload["payload"]["new_state"] == "processing"
    audits = list(
        (
            await session.execute(
                select(AuditEvent).where(AuditEvent.action == "document.version.processing")
            )
        ).scalars()
    )
    assert [event.action for event in audits] == ["document.version.processing"]


@pytest.mark.parametrize(
    ("mutation", "expected_state"),
    [
        ("reject_version", "rejected"),
        ("obsolete_version", "obsolete"),
    ],
)
async def test_governance_commands_record_bounded_reason_only_in_decision(
    session: AsyncSession,
    mutation: str,
    expected_state: str,
) -> None:
    context, _document, candidate, _ = await seed_review_version(
        session,
        f"govern-{mutation}",
        candidate_state=("approved" if mutation == "obsolete_version" else "review"),
    )
    reason = "bounded governance detail"
    changed = await getattr(service, mutation)(session, context, candidate.id, reason=reason)
    assert changed.state == expected_state
    decision = (
        await session.execute(
            select(DocumentVersionDecisionRecord).where(
                DocumentVersionDecisionRecord.document_version_id == candidate.id
            )
        )
    ).scalar_one()
    assert decision.reason == reason
    outbox = list((await session.execute(select(OutboxEvent))).scalars())
    audits = list((await session.execute(select(AuditEvent))).scalars())
    assert reason not in repr([event.payload for event in outbox])
    assert reason not in repr(audits)


async def test_invalid_approval_has_no_partial_decision_audit_or_outbox(
    session: AsyncSession,
) -> None:
    context, _document, candidate, _ = await seed_review_version(
        session,
        "approve-invalid",
        candidate_expires_at=naive_utc() - timedelta(seconds=1),
    )

    with pytest.raises(ConflictError):
        await service.approve_version(session, context, candidate.id, reason=None)

    assert list((await session.execute(select(DocumentVersionDecisionRecord))).scalars()) == []
    assert list((await session.execute(select(AuditEvent))).scalars()) == []
    assert list((await session.execute(select(OutboxEvent))).scalars()) == []


@pytest.mark.parametrize("candidate_sorts_first", [True, False])
async def test_approval_is_independent_of_candidate_incumbent_uuid_order(
    session: AsyncSession,
    candidate_sorts_first: bool,
) -> None:
    lower = UUID("00000000-0000-0000-0000-000000000001")
    higher = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    context, _document, candidate, incumbent = await seed_review_version(
        session,
        f"approve-uuid-order-{candidate_sorts_first}",
        incumbent=True,
        candidate_id=lower if candidate_sorts_first else higher,
        incumbent_id=higher if candidate_sorts_first else lower,
    )
    assert incumbent is not None

    approved = await service.approve_version(
        session, context, candidate.id, reason="ordered lock regression"
    )

    await session.refresh(incumbent)
    assert approved.state == "approved"
    assert incumbent.state == "superseded"
    assert incumbent.superseded_by_id == approved.id


def test_lifecycle_event_contract_is_bounded_and_content_free() -> None:
    payload = DocumentVersionLifecycleV1(
        document_id=uuid4(),
        previous_state="review",
        new_state="approved",
    )
    event = build_envelope(
        payload=payload,
        event_id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        aggregate_id=uuid4(),
        lifecycle_revision=2,
        correlation_id=uuid4(),
        occurred_at=datetime.now(UTC),
    )

    assert set(event.model_dump()) == {
        "schema_version",
        "event_id",
        "event_type",
        "aggregate_type",
        "aggregate_id",
        "org_id",
        "workspace_id",
        "lifecycle_revision",
        "correlation_id",
        "occurred_at",
        "payload",
    }
    with pytest.raises(ValidationError):
        build_envelope(
            payload=payload,
            event_id=uuid4(),
            org_id=uuid4(),
            workspace_id=uuid4(),
            aggregate_id=uuid4(),
            lifecycle_revision=0,
            correlation_id=uuid4(),
            occurred_at=datetime.now(UTC),
        )
    with pytest.raises(ValidationError):
        DocumentVersionLifecycleV1(
            document_id=uuid4(),
            previous_state="review",
            new_state="approved",
            filename="secret.pdf",
        )


async def test_legacy_upload_object_calls_are_outside_sql_transaction(
    session: AsyncSession,
    stack_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, workspace = await seed_workspace(session, "upload-transaction-free")
    calls: list[str] = []

    class GuardedStorage:
        async def ensure_bucket(self) -> None:
            assert not session.in_transaction()
            calls.append("ensure")

        async def put(self, key: str, data: bytes, *, content_type: str) -> None:
            assert not session.in_transaction()
            assert data == b"payload"
            assert content_type == "text/plain"
            calls.append(f"put:{key}")

        async def delete(self, key: str) -> None:
            assert not session.in_transaction()
            calls.append(f"delete:{key}")

    monkeypatch.setattr(service, "build_storage", lambda _settings: GuardedStorage())

    document = await service.create_from_upload(
        session,
        context,
        workspace.id,
        filename="safe.txt",
        mime="text/plain",
        data=b"payload",
    )

    assert calls == [
        "ensure",
        f"put:{context.org_id}/{workspace.id}/{document.id}/{document.id}/source",
    ]


async def test_upload_record_failure_compensates_only_new_source_object(
    session: AsyncSession,
    stack_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, workspace = await seed_workspace(session, "upload-compensation")
    calls: list[tuple[str, str]] = []

    class GuardedStorage:
        async def ensure_bucket(self) -> None:
            assert not session.in_transaction()

        async def put(self, key: str, data: bytes, *, content_type: str) -> None:
            assert not session.in_transaction()
            calls.append(("put", key))

        async def delete(self, key: str) -> None:
            assert not session.in_transaction()
            calls.append(("delete", key))

    async def fail_record(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ConflictError("simulated post-storage conflict")

    monkeypatch.setattr(service, "build_storage", lambda _settings: GuardedStorage())
    monkeypatch.setattr(service, "create_document_record", fail_record)

    with pytest.raises(ConflictError, match="post-storage conflict"):
        await service.create_from_upload(
            session,
            context,
            workspace.id,
            filename="safe.txt",
            mime="text/plain",
            data=b"payload",
        )

    assert len(calls) == 2
    assert calls[0][0] == "put"
    assert calls[1] == ("delete", calls[0][1])
    assert list((await session.execute(select(Document))).scalars()) == []


async def test_upload_put_that_writes_then_raises_compensates_exact_new_key(
    session: AsyncSession,
    stack_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, workspace = await seed_workspace(session, "upload-partial-put")
    written: list[str] = []
    deleted: list[str] = []

    class PartialPutStorage:
        async def ensure_bucket(self) -> None:
            assert not session.in_transaction()

        async def put(self, key: str, data: bytes, *, content_type: str) -> None:
            assert not session.in_transaction()
            written.append(key)
            raise RuntimeError("primary object write failure")

        async def delete(self, key: str) -> None:
            assert not session.in_transaction()
            deleted.append(key)

    monkeypatch.setattr(service, "build_storage", lambda _settings: PartialPutStorage())

    with pytest.raises(RuntimeError, match="primary object write failure"):
        await service.create_from_upload(
            session,
            context,
            workspace.id,
            filename="partial.txt",
            mime="text/plain",
            data=b"partial payload",
        )

    assert len(written) == 1
    assert deleted == written
    assert list((await session.execute(select(Document))).scalars()) == []


async def test_upload_compensation_failure_never_masks_primary_put_error(
    session: AsyncSession,
    stack_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, workspace = await seed_workspace(session, "upload-compensation-fails")
    attempted_keys: list[str] = []
    log_messages: list[str] = []

    class CapturingLogger:
        def error(self, message: str) -> None:
            log_messages.append(message)

    class FailingStorage:
        async def ensure_bucket(self) -> None:
            assert not session.in_transaction()

        async def put(self, key: str, data: bytes, *, content_type: str) -> None:
            attempted_keys.append(key)
            raise RuntimeError("primary put error")

        async def delete(self, key: str) -> None:
            assert key == attempted_keys[0]
            raise RuntimeError("compensation secret details")

    monkeypatch.setattr(service, "build_storage", lambda _settings: FailingStorage())
    monkeypatch.setattr(service, "_logger", CapturingLogger())

    with pytest.raises(RuntimeError, match="primary put error"):
        await service.create_from_upload(
            session,
            context,
            workspace.id,
            filename="partial.txt",
            mime="text/plain",
            data=b"partial payload",
        )

    assert log_messages == ["upload object compensation failed"]
    assert "compensation secret details" not in repr(log_messages)
    assert attempted_keys[0] not in repr(log_messages)


def test_document_version_declares_restartable_source_deletion_markers() -> None:
    columns = DocumentVersion.__table__.c
    assert columns.source_delete_requested_at.nullable is True
    assert columns.source_deleted_at.nullable is True
    assert columns.source_delete_requested_by.nullable is True


@pytest.mark.parametrize(
    ("state", "provenance", "page_count", "effective_delta", "expiry_delta"),
    [
        ("processing", "building", 1, None, None),
        ("review", "building", 1, None, None),
        ("review", "ready", 1, 60, None),
        ("review", "ready", 1, None, -60),
    ],
)
async def test_approval_preconditions_fail_without_partial_writes(
    session: AsyncSession,
    state: str,
    provenance: str,
    page_count: int | None,
    effective_delta: int | None,
    expiry_delta: int | None,
) -> None:
    context, _document, candidate, _ = await seed_review_version(
        session,
        f"approve-precondition-{state}-{provenance}-{page_count}-{effective_delta}-{expiry_delta}",
        candidate_state=state,
        candidate_provenance=provenance,
        candidate_page_count=page_count,
        candidate_effective_at=(
            naive_utc() + timedelta(seconds=effective_delta)
            if effective_delta is not None
            else None
        ),
        candidate_expires_at=(
            naive_utc() + timedelta(seconds=expiry_delta) if expiry_delta is not None else None
        ),
    )

    with pytest.raises(ConflictError):
        await service.approve_version(session, context, candidate.id, reason=None)

    assert list((await session.execute(select(DocumentVersionDecisionRecord))).scalars()) == []
    assert list((await session.execute(select(OutboxEvent))).scalars()) == []


@pytest.mark.parametrize("page_count", [None, 0, -1])
def test_approval_validator_rejects_missing_or_nonpositive_page_count(
    page_count: int | None,
) -> None:
    candidate = SimpleNamespace(
        state="review",
        provenance_state="ready",
        source_page_count=page_count,
        effective_at=None,
        expires_at=None,
    )
    with pytest.raises(ConflictError):
        service._validate_approval_candidate(candidate, naive_utc())


async def test_retry_resets_only_processing_fields_and_emits_atomic_events(
    session: AsyncSession,
) -> None:
    context, _document, version, _ = await seed_review_version(
        session,
        "retry-semantics",
        candidate_state="failed",
        candidate_provenance="failed",
    )
    version_id = version.id
    version.processing_error_code = "parser_failed"
    profiles = (
        version.parser_profile_version,
        version.ocr_profile_version,
        version.chunking_profile_version,
        version.embedding_profile_version,
        version.index_profile_version,
    )
    await session.commit()

    retried = await service.retry_version(session, context, version_id)

    assert (retried.state, retried.provenance_state) == ("processing", "none")
    assert retried.processing_error_code is None
    assert retried.lifecycle_revision == 2
    assert (
        retried.parser_profile_version,
        retried.ocr_profile_version,
        retried.chunking_profile_version,
        retried.embedding_profile_version,
        retried.index_profile_version,
    ) == profiles
    events = list((await session.execute(select(OutboxEvent))).scalars())
    assert {event.event_type for event in events} == {
        "document.version.lifecycle.v1",
        INGESTION_REQUESTED_EVENT_TYPE,
    }
    assert list((await session.execute(select(DocumentVersionDecisionRecord))).scalars()) == []


async def test_retry_invalid_state_has_no_partial_rows(
    session: AsyncSession,
) -> None:
    context, _document, version, _ = await seed_review_version(session, "retry-invalid")
    with pytest.raises(ConflictError):
        await service.retry_version(session, context, version.id)
    assert list((await session.execute(select(OutboxEvent))).scalars()) == []
    assert list((await session.execute(select(AuditEvent))).scalars()) == []


def context_with_permissions(
    context: TenantContext,
    permissions: frozenset[str],
) -> TenantContext:
    return TenantContext(
        user_id=context.user_id,
        org_id=context.org_id,
        authorization=AuthorizationSnapshot(
            user_id=context.user_id,
            org_id=context.org_id,
            is_platform_superadmin=False,
            org_permissions=permissions,
            workspace_permissions={},
            workspace_ids=context.workspace_ids,
        ),
    )


@pytest.mark.parametrize(
    ("command", "required_permission"),
    [
        ("approve_version", "document.approve"),
        ("reject_version", "document.approve"),
        ("obsolete_version", "document.approve"),
        ("retry_version", "document.upload"),
        ("request_document_deletion", "document.upload"),
    ],
)
async def test_lifecycle_command_permission_and_foreign_object_oracles(
    session: AsyncSession,
    command: str,
    required_permission: str,
) -> None:
    initial_state = (
        "approved"
        if command == "obsolete_version"
        else "failed"
        if command in {"retry_version", "request_document_deletion"}
        else "review"
    )
    context, _document, version, _ = await seed_review_version(
        session,
        f"oracle-{command}",
        candidate_state=initial_state,
        candidate_provenance=("failed" if initial_state == "failed" else "ready"),
    )
    version_id = version.id

    allowed_except_required = frozenset(
        {"document.read", "document.upload", "document.approve"} - {required_permission}
    )
    restricted = context_with_permissions(context, allowed_except_required)
    kwargs = (
        {"reason": None}
        if command
        in {
            "approve_version",
            "reject_version",
            "obsolete_version",
        }
        else {}
    )
    with pytest.raises(WorkspaceAccessDenied):
        await getattr(service, command)(session, restricted, version_id, **kwargs)

    foreign_context, _ = await seed_workspace(session, f"foreign-{command}", role="admin")
    with pytest.raises(NotFoundError):
        await getattr(service, command)(session, foreign_context, version_id, **kwargs)


async def test_checked_version_read_and_list_preserve_oracles(
    session: AsyncSession,
) -> None:
    context, document, version, _ = await seed_review_version(session, "checked-version")
    assert (await service.get_version_checked(session, context, version.id)).id == version.id
    assert [item.id for item in await service.list_versions(session, context, document.id)] == [
        version.id
    ]

    restricted = context_with_permissions(context, frozenset({"document.upload"}))
    with pytest.raises(WorkspaceAccessDenied):
        await service.get_version_checked(session, restricted, version.id)
    with pytest.raises(WorkspaceAccessDenied):
        await service.list_versions(session, restricted, document.id)

    foreign_context, _ = await seed_workspace(session, "checked-version-foreign", role="admin")
    with pytest.raises(NotFoundError):
        await service.get_version_checked(session, foreign_context, version.id)
    with pytest.raises(NotFoundError):
        await service.list_versions(session, foreign_context, document.id)


async def test_same_org_nonmember_observes_not_found_for_object_reads_and_commands(
    session: AsyncSession,
) -> None:
    context, document, version, _ = await seed_review_version(
        session,
        "same-org-nonmember-object-oracle",
        candidate_state="failed",
        candidate_provenance="failed",
    )
    nonmember = TenantContext(
        user_id=context.user_id,
        org_id=context.org_id,
        authorization=AuthorizationSnapshot(
            user_id=context.user_id,
            org_id=context.org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset({"document.read", "document.upload", "document.approve"}),
            workspace_permissions={},
            workspace_ids=frozenset(),
        ),
    )
    document_id = document.id
    version_id = version.id

    object_reads = (
        lambda: service.get_document_checked(session, nonmember, document_id),
        lambda: service.get_version_checked(session, nonmember, version_id),
        lambda: service.list_versions(session, nonmember, document_id),
    )
    for read in object_reads:
        with pytest.raises(NotFoundError):
            await read()

    commands = (
        lambda: service.approve_version(session, nonmember, version_id, reason=None),
        lambda: service.reject_version(session, nonmember, version_id, reason=None),
        lambda: service.obsolete_version(session, nonmember, version_id, reason=None),
        lambda: service.retry_version(session, nonmember, version_id),
        lambda: service.request_document_deletion(session, nonmember, version_id),
    )
    for command in commands:
        with pytest.raises(NotFoundError):
            await command()


async def test_deletion_request_is_idempotent_and_requires_upload_not_read(
    session: AsyncSession,
) -> None:
    context, _document, version, _ = await seed_review_version(
        session,
        "delete-request-idempotent",
        candidate_state="failed",
        candidate_provenance="failed",
    )
    reader = context_with_permissions(context, frozenset({"document.read"}))
    version_id = version.id

    with pytest.raises(WorkspaceAccessDenied):
        await service.request_document_deletion(session, reader, version_id)

    first = await service.request_document_deletion(session, context, version_id)
    first_timestamp = first.source_delete_requested_at
    second = await service.request_document_deletion(session, context, version_id)
    assert second.source_delete_requested_at == first_timestamp
    assert second.source_delete_requested_by == context.user_id


async def test_deletion_request_defensively_rejects_governed_decision_history(
    session: AsyncSession,
) -> None:
    context, _document, version, _ = await seed_review_version(
        session,
        "delete-governed-history",
        candidate_state="rejected",
        candidate_provenance="ready",
    )
    version_id = version.id
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

    with pytest.raises(ConflictError, match="governed history"):
        await service.request_document_deletion(session, context, version_id)

    stored = await session.get(DocumentVersion, version_id)
    assert stored is not None
    assert stored.source_delete_requested_at is None


async def test_prepared_version_upload_normalizes_before_io_and_sequence_is_server_allocated(
    session: AsyncSession,
) -> None:
    context, document, _first, _ = await seed_review_version(session, "prepared-version")
    prepared = await service.authorize_upload_scope(
        session,
        context,
        document.workspace_id,
        document_id=document.id,
        version_label="  Ｒｅｖ\t2  ",
        filename="manual-v2.pdf",
        mime="application/pdf",
        data=b"revision two",
        parser_profile_version="docling/v1",
        ocr_profile_version="none/v1",
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
        index_profile_version="hybrid/v1",
    )
    assert not session.in_transaction()
    assert isinstance(prepared, service.PreparedUpload)
    assert (prepared.version_label, prepared.version_key) == ("Rev 2", "rev 2")
    assert prepared.storage_key == (
        f"{context.org_id}/{document.workspace_id}/{document.id}/{prepared.version_id}/source"
    )

    created = await service.create_version_record(session, context, prepared)
    await session.commit()
    assert created.sequence == 2


async def test_confusable_duplicate_label_rejected_during_authorization_before_storage(
    session: AsyncSession,
) -> None:
    context, document, _first, _ = await seed_review_version(session, "prepared-duplicate")
    with pytest.raises(ConflictError):
        await service.authorize_upload_scope(
            session,
            context,
            document.workspace_id,
            document_id=document.id,
            version_label="  Ｒｅｖ  1 ",
            filename="duplicate.pdf",
            mime="application/pdf",
            data=b"different bytes",
            parser_profile_version="docling/v1",
            ocr_profile_version="none/v1",
            chunking_profile_version="semantic/v1",
            embedding_profile_version="bge-m3/v1",
            index_profile_version="hybrid/v1",
        )
    assert not session.in_transaction()


async def test_create_version_reauthorizes_prepared_scope(
    session: AsyncSession,
) -> None:
    context, document, _first, _ = await seed_review_version(session, "prepared-reauthorize")
    prepared = await service.authorize_upload_scope(
        session,
        context,
        document.workspace_id,
        document_id=document.id,
        version_label="Rev 2",
        filename="v2.pdf",
        mime="application/pdf",
        data=b"v2",
        parser_profile_version="docling/v1",
        ocr_profile_version="none/v1",
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
        index_profile_version="hybrid/v1",
    )
    restricted = context_with_permissions(context, frozenset({"document.read"}))

    with pytest.raises(WorkspaceAccessDenied):
        await service.create_version_record(session, restricted, prepared)

    assert (
        list(
            (
                await session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.document_id == document.id,
                        DocumentVersion.version_key == "rev 2",
                    )
                )
            ).scalars()
        )
        == []
    )


async def test_governance_rolls_back_decision_audit_and_state_when_outbox_write_fails(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _document, candidate, _ = await seed_review_version(session, "approve-outbox-rollback")

    def fail_outbox(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("forced outbox failure")

    monkeypatch.setattr(service, "_persist_lifecycle_event", fail_outbox)
    with pytest.raises(RuntimeError, match="forced outbox failure"):
        await service.approve_version(session, context, candidate.id, reason=None)

    await session.refresh(candidate)
    assert (candidate.state, candidate.lifecycle_revision) == ("review", 1)
    assert list((await session.execute(select(DocumentVersionDecisionRecord))).scalars()) == []
    assert list((await session.execute(select(AuditEvent))).scalars()) == []
    assert list((await session.execute(select(OutboxEvent))).scalars()) == []
