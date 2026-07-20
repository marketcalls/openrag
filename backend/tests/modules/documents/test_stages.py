import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from openrag.core.db import naive_utc
from openrag.modules.documents.models import (
    Document,
    DocumentVersion,
    IngestStageAttempt,
)
from openrag.modules.documents.stages import (
    StageCheckpoint,
    claim_stage,
    complete_stage,
    heartbeat_stage,
    retry_stage,
)
from tests.modules.documents.test_models import seed_document_version, seed_scope


async def _seed_attempt(
    session: AsyncSession,
    *,
    stage: str = "parse",
    pipeline_kind: str = "ingestion",
    attempts: int = 0,
) -> tuple[IngestStageAttempt, UUID]:
    organization, workspace, user = await seed_scope(session)
    _, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash=f"stage-{uuid4()}",
        version_hash=f"stage-version-{uuid4()}",
        state="processing",
    )
    generation_id = uuid4()
    attempt = IngestStageAttempt(
        org_id=organization.id,
        workspace_id=workspace.id,
        document_version_id=version.id,
        pipeline_kind=pipeline_kind,
        stage=stage,
        checkpoint=f"{stage}:{pipeline_kind}:1:{generation_id.hex}",
        attempts=attempts,
    )
    session.add(attempt)
    await session.commit()
    return attempt, generation_id


async def test_concurrent_claimers_get_one_fenced_lease(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    attempt, _ = await _seed_attempt(session)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    first, second = await asyncio.gather(
        claim_stage(factory, owner="worker-a", lease_seconds=30),
        claim_stage(factory, owner="worker-b", lease_seconds=30),
    )

    claims = [claim for claim in (first, second) if claim is not None]
    assert len(claims) == 1
    assert claims[0].attempt_id == attempt.id
    assert claims[0].attempt_number == 1
    async with factory() as verify:
        stored = await verify.get(IngestStageAttempt, attempt.id)
    assert stored is not None
    assert stored.state == "running"
    assert stored.lease_token == claims[0].lease_token
    assert stored.lease_owner == claims[0].owner


async def test_legacy_rebuild_claim_opens_approved_provenance_window(
    engine: AsyncEngine,
    session: AsyncSession,
    chat_env: dict[str, object],
) -> None:
    document = chat_env["document"]
    version = await session.get(DocumentVersion, document.id)  # type: ignore[attr-defined]
    assert version is not None
    version.source_page_count = 1
    generation_id = uuid4()
    attempt = IngestStageAttempt(
        org_id=version.org_id,
        workspace_id=version.workspace_id,
        document_version_id=version.id,
        pipeline_kind="rebuild",
        stage="parse",
        checkpoint=f"parse:rebuild:1:{generation_id.hex}",
    )
    session.add(attempt)
    await session.commit()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    claim = await claim_stage(factory, owner="rebuild-worker", lease_seconds=30)

    assert claim is not None
    assert claim.document_version_id == version.id
    async with factory() as verify:
        stored = await verify.get(DocumentVersion, version.id)
    assert stored is not None
    assert stored.state == "approved"
    assert stored.provenance_state == "building"

    terminal = await retry_stage(
        factory,
        claim,
        error_code="LEGACY_REBUILD_FAILED",
        terminal=True,
    )

    assert terminal == "failed"
    async with factory() as verify:
        failed = await verify.get(DocumentVersion, version.id)
    assert failed is not None
    assert failed.state == "approved"
    assert failed.provenance_state == "failed"
    assert failed.processing_error_code == "LEGACY_REBUILD_FAILED"


async def test_expired_reclaim_fences_old_completion_and_advances_once(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    attempt, generation_id = await _seed_attempt(session)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    old_claim = await claim_stage(factory, owner="worker-old", lease_seconds=30)
    assert old_claim is not None
    renewed = await heartbeat_stage(factory, old_claim, lease_seconds=60)
    assert renewed is True
    async with factory.begin() as expire:
        stored = await expire.get(IngestStageAttempt, attempt.id)
        assert stored is not None
        stored.lease_expires_at = naive_utc() - timedelta(seconds=1)

    new_claim = await claim_stage(factory, owner="worker-new", lease_seconds=30)
    assert new_claim is not None
    assert new_claim.lease_token != old_claim.lease_token
    assert new_claim.attempt_number == 2

    stale_callback_called = False

    async def stale_callback(*_args: object) -> None:
        nonlocal stale_callback_called
        stale_callback_called = True

    stale = await complete_stage(
        factory,
        old_claim,
        output_digest="a" * 64,
        apply_result=stale_callback,
    )
    completed = await complete_stage(factory, new_claim, output_digest="b" * 64)

    assert stale == "lease_lost"
    assert stale_callback_called is False
    assert completed == "advanced"
    async with factory() as verify:
        attempts = list(
            (
                await verify.scalars(
                    select(IngestStageAttempt).order_by(IngestStageAttempt.created_at)
                )
            ).all()
        )
    assert len(attempts) == 2
    assert attempts[0].state == "succeeded"
    assert attempts[0].output_digest == "b" * 64
    assert attempts[1].stage == "chunk"
    assert attempts[1].state == "queued"
    assert attempts[1].checkpoint == f"chunk:ingestion:1:{generation_id.hex}"

    assert await heartbeat_stage(factory, old_claim, lease_seconds=60) is False


async def test_completion_applies_result_and_next_stage_in_one_transaction(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    attempt, generation_id = await _seed_attempt(session)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    claim = await claim_stage(factory, owner="worker-a", lease_seconds=30)
    assert claim is not None

    async def apply_result(
        transaction: AsyncSession,
        row: IngestStageAttempt,
        checkpoint: StageCheckpoint,
    ) -> None:
        version = await transaction.get(DocumentVersion, row.document_version_id)
        assert version is not None
        version.source_page_count = 2
        assert checkpoint.authority_generation_id == generation_id

    result = await complete_stage(
        factory,
        claim,
        output_digest="d" * 64,
        apply_result=apply_result,
    )

    assert result == "advanced"
    async with factory() as verify:
        stored = await verify.get(IngestStageAttempt, attempt.id)
        version = await verify.get(DocumentVersion, attempt.document_version_id)
        attempts = list((await verify.scalars(select(IngestStageAttempt))).all())
    assert stored is not None and stored.state == "succeeded"
    assert version is not None and version.source_page_count == 2
    assert len(attempts) == 2
    assert {row.stage for row in attempts} == {"parse", "chunk"}


async def test_completion_rolls_back_result_and_stage_advance_together(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    attempt, _ = await _seed_attempt(session)
    baseline = await session.get(DocumentVersion, attempt.document_version_id)
    assert baseline is not None
    baseline_page_count = baseline.source_page_count
    factory = async_sessionmaker(engine, expire_on_commit=False)
    claim = await claim_stage(factory, owner="worker-a", lease_seconds=30)
    assert claim is not None

    async def reject_result(
        transaction: AsyncSession,
        row: IngestStageAttempt,
        _checkpoint: StageCheckpoint,
    ) -> None:
        version = await transaction.get(DocumentVersion, row.document_version_id)
        assert version is not None
        version.source_page_count = 3
        raise RuntimeError("result rejected")

    with pytest.raises(RuntimeError, match="result rejected"):
        await complete_stage(
            factory,
            claim,
            output_digest="e" * 64,
            apply_result=reject_result,
        )

    async with factory() as verify:
        stored = await verify.get(IngestStageAttempt, attempt.id)
        version = await verify.get(DocumentVersion, attempt.document_version_id)
        count = await verify.scalar(select(func.count()).select_from(IngestStageAttempt))
    assert stored is not None and stored.state == "running"
    assert stored.output_digest is None
    assert version is not None and version.source_page_count == baseline_page_count
    assert count == 1


async def test_authority_stage_is_deferred_until_exact_storage_is_ready(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    attempt, generation_id = await _seed_attempt(session, stage="authority_upsert")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    probes: list[UUID] = []

    async def unavailable(candidate: UUID) -> bool:
        probes.append(candidate)
        return False

    claim = await claim_stage(
        factory,
        owner="worker-a",
        lease_seconds=30,
        authority_ready=unavailable,
    )

    assert claim is None
    assert probes == [generation_id]
    async with factory() as verify:
        stored = await verify.get(IngestStageAttempt, attempt.id)
    assert stored is not None
    assert stored.state == "queued"
    assert stored.error_code == "AUTHORITY_STORAGE_NOT_READY"
    assert stored.available_at > naive_utc()
    assert stored.lease_token is None


async def test_retry_releases_lease_with_backoff_then_terminalizes_at_limit(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    attempt, _ = await _seed_attempt(session, attempts=6)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    claim = await claim_stage(factory, owner="worker-a", lease_seconds=30)
    assert claim is not None and claim.attempt_number == 7

    retry = await retry_stage(factory, claim, error_code="STAGE_RETRYABLE")
    assert retry == "queued"
    async with factory.begin() as make_available:
        stored = await make_available.get(IngestStageAttempt, attempt.id)
        assert stored is not None
        assert stored.available_at > naive_utc()
        stored.available_at = naive_utc() - timedelta(seconds=1)

    final_claim = await claim_stage(factory, owner="worker-b", lease_seconds=30)
    assert final_claim is not None and final_claim.attempt_number == 8
    terminal = await retry_stage(
        factory,
        final_claim,
        error_code="STAGE_RETRYABLE",
    )

    assert terminal == "failed"
    async with factory() as verify:
        stored = await verify.get(IngestStageAttempt, attempt.id)
        version = await verify.get(DocumentVersion, attempt.document_version_id)
        count = await verify.scalar(select(func.count()).select_from(IngestStageAttempt))
    assert stored is not None
    assert stored.state == "failed"
    assert stored.error_code == "STAGE_RETRYABLE"
    assert stored.lease_token is None
    assert version is not None
    assert version.state == "failed"
    assert version.provenance_state == "failed"
    assert version.processing_error_code == "STAGE_RETRYABLE"
    assert count == 1


async def test_terminal_ingestion_failure_advances_exact_legacy_lifecycle(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    organization, workspace, user = await seed_scope(session)
    document_id = uuid4()
    source_key = f"legacy/{document_id}.pdf"
    document = Document(
        id=document_id,
        org_id=organization.id,
        workspace_id=workspace.id,
        name="Legacy report.pdf",
        filename="legacy.pdf",
        mime="application/pdf",
        size_bytes=10,
        content_hash="7" * 64,
        storage_key=source_key,
        status="processing",
        created_by=user.id,
    )
    version = DocumentVersion(
        id=document_id,
        org_id=organization.id,
        workspace_id=workspace.id,
        document_id=document_id,
        sequence=1,
        version_label="Legacy 1",
        version_key="legacy 1",
        content_hash="7" * 64,
        source_filename="legacy.pdf",
        source_mime="application/pdf",
        source_size_bytes=10,
        source_storage_key=source_key,
        source_page_count=1,
        parser_profile_version="legacy/parser-v1",
        ocr_profile_version="legacy/ocr-unknown-v1",
        chunking_profile_version="legacy/chunking-v1",
        embedding_profile_version="legacy/embedding-v1",
        index_profile_version="legacy/index-v1",
        state="processing",
        provenance_state="building",
        lifecycle_revision=1,
        created_by=user.id,
    )
    generation_id = uuid4()
    attempt = IngestStageAttempt(
        org_id=organization.id,
        workspace_id=workspace.id,
        document_version_id=document_id,
        pipeline_kind="ingestion",
        stage="embed",
        checkpoint=f"embed:ingestion:1:{generation_id.hex}",
        available_at=naive_utc() - timedelta(seconds=1),
    )
    session.add_all([document, version, attempt])
    await session.commit()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    claim = await claim_stage(factory, owner="worker-a", lease_seconds=30)
    assert claim is not None

    result = await retry_stage(
        factory,
        claim,
        error_code="EMBEDDING_OUTPUT_INVALID",
        terminal=True,
    )

    assert result == "failed"
    async with factory() as verify:
        stored_version = await verify.get(DocumentVersion, document_id)
        stored_document = await verify.get(Document, document_id)
        stored_attempt = await verify.get(IngestStageAttempt, attempt.id)
    assert stored_version is not None
    assert stored_version.state == "failed"
    assert stored_version.provenance_state == "failed"
    assert stored_version.lifecycle_revision == 2
    assert stored_document is not None
    assert stored_document.status == "failed"
    assert stored_document.error == "EMBEDDING_OUTPUT_INVALID"
    assert stored_attempt is not None
    assert stored_attempt.state == "failed"
    assert stored_attempt.lease_token is None


async def test_final_authority_stage_completes_without_creating_another_stage(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    attempt, _ = await _seed_attempt(session, stage="authority_upsert")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def ready(_generation_id: UUID) -> bool:
        return True

    claim = await claim_stage(
        factory,
        owner="worker-a",
        lease_seconds=30,
        authority_ready=ready,
    )
    assert claim is not None

    result = await complete_stage(factory, claim, output_digest="c" * 64)

    assert result == "completed"
    async with factory() as verify:
        attempts = list((await verify.scalars(select(IngestStageAttempt))).all())
    assert len(attempts) == 1
    assert attempts[0].state == "succeeded"
