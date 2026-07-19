import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from openrag.core.db import naive_utc
from openrag.modules.documents.models import DocumentVersion, IngestStageAttempt
from openrag.modules.documents.stages import (
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

    stale = await complete_stage(factory, old_claim, output_digest="a" * 64)
    completed = await complete_stage(factory, new_claim, output_digest="b" * 64)

    assert stale == "lease_lost"
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
