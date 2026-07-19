import asyncio
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from openrag.core.db import build_session_factory, naive_utc
from openrag.modules.auth.models import User
from openrag.modules.documents.models import (
    Document,
    DocumentVersion,
    LegacyRebuildScanCheckpoint,
)
from openrag.modules.documents.rebuild import scan_legacy_pending
from openrag.modules.events.models import OutboxEvent
from openrag.modules.tenancy.models import Workspace


async def _seed_legacy_versions(
    session: AsyncSession,
    *,
    user: User,
    workspace: Workspace,
    identifiers: list[int],
    provenance_state: str = "legacy_pending",
) -> None:
    approved_at = naive_utc()
    documents: list[Document] = []
    for identifier in identifiers:
        version_id = UUID(int=identifier)
        content_hash = f"{identifier:064x}"
        documents.append(
            Document(
                id=version_id,
                org_id=user.org_id,
                workspace_id=workspace.id,
                name=f"legacy-{identifier}.pdf",
                filename=f"legacy-{identifier}.pdf",
                mime="application/pdf",
                size_bytes=identifier,
                content_hash=content_hash,
                status="indexed",
                storage_key=f"legacy/{identifier}.pdf",
                created_by=user.id,
            )
        )
    session.add_all(documents)
    await session.flush()
    versions: list[DocumentVersion] = []
    for identifier in identifiers:
        version_id = UUID(int=identifier)
        content_hash = f"{identifier:064x}"
        is_pending = provenance_state == "legacy_pending"
        versions.append(
            DocumentVersion(
                id=version_id,
                org_id=user.org_id,
                workspace_id=workspace.id,
                document_id=version_id,
                sequence=1,
                version_label="Legacy 1",
                version_key="legacy 1",
                content_hash=content_hash,
                source_filename=f"legacy-{identifier}.pdf",
                source_mime="application/pdf",
                source_size_bytes=identifier,
                source_storage_key=f"legacy/{identifier}.pdf",
                parser_profile_version="legacy/parser-v1",
                ocr_profile_version="legacy/ocr-unknown-v1",
                chunking_profile_version="legacy/chunking-v1",
                embedding_profile_version="legacy/embedding-v1",
                index_profile_version="legacy/index-v1",
                state="approved" if is_pending else "failed",
                provenance_state=provenance_state,
                created_by=user.id,
                approved_by=user.id if is_pending else None,
                approved_at=approved_at if is_pending else None,
                decision_at=approved_at if is_pending else None,
                legacy_approval_backfilled=is_pending,
            )
        )
    session.add_all(versions)
    await session.commit()


async def _seed_workspace(session: AsyncSession, user: User, name: str) -> Workspace:
    workspace = Workspace(org_id=user.org_id, name=name)
    session.add(workspace)
    await session.commit()
    return workspace


async def test_scanner_commits_bounded_pages_and_finds_insertions_below_cursor(
    engine: AsyncEngine,
    session: AsyncSession,
    seeded_user: User,
) -> None:
    workspace = await _seed_workspace(session, seeded_user, "Legacy scan")
    await _seed_legacy_versions(
        session,
        user=seeded_user,
        workspace=workspace,
        identifiers=[100, 300, 500],
    )
    factory = build_session_factory(engine)
    generation_id = uuid4()

    first = await scan_legacy_pending(
        factory,
        authority_generation_id=generation_id,
        page_size=2,
        max_pages=1,
    )
    assert (first.pages, first.scanned, first.emitted, first.skipped) == (1, 2, 2, 0)

    await _seed_legacy_versions(
        session,
        user=seeded_user,
        workspace=workspace,
        identifiers=[200, 400],
    )
    second = await scan_legacy_pending(
        factory,
        authority_generation_id=generation_id,
        page_size=2,
        max_pages=1,
    )
    assert (second.scanned, second.emitted) == (2, 2)

    await scan_legacy_pending(
        factory,
        authority_generation_id=generation_id,
        page_size=2,
        max_pages=4,
    )

    async with factory() as verify:
        events = list(
            (
                await verify.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.event_type
                        == "document.version.rebuild_requested.v1"
                    )
                    .order_by(OutboxEvent.aggregate_id)
                )
            ).all()
        )
        checkpoint = await verify.scalar(
            select(LegacyRebuildScanCheckpoint).where(
                LegacyRebuildScanCheckpoint.workspace_id == workspace.id
            )
        )

    assert [event.aggregate_id.int for event in events] == [100, 200, 300, 400, 500]
    assert all(
        event.payload["payload"]["authority_generation_id"] == str(generation_id)
        for event in events
    )
    assert checkpoint is not None
    assert checkpoint.pass_number >= 1
    assert checkpoint.scanned_count >= 5
    assert checkpoint.emitted_count == 5


async def test_scanner_skips_nonlegacy_versions_and_is_replay_safe(
    engine: AsyncEngine,
    session: AsyncSession,
    seeded_user: User,
) -> None:
    pending = await _seed_workspace(session, seeded_user, "Pending")
    ready = await _seed_workspace(session, seeded_user, "Ready")
    await _seed_legacy_versions(
        session,
        user=seeded_user,
        workspace=pending,
        identifiers=[1000],
    )
    await _seed_legacy_versions(
        session,
        user=seeded_user,
        workspace=ready,
        identifiers=[2000],
        provenance_state="none",
    )
    factory = build_session_factory(engine)
    generation_id = uuid4()

    await scan_legacy_pending(
        factory,
        authority_generation_id=generation_id,
        page_size=10,
        max_pages=2,
    )
    replay = await scan_legacy_pending(
        factory,
        authority_generation_id=generation_id,
        page_size=10,
        max_pages=2,
    )

    async with factory() as verify:
        count = await verify.scalar(select(func.count()).select_from(OutboxEvent))
    assert count == 1
    assert replay.emitted == 0
    assert replay.skipped >= 1


async def test_concurrent_scanners_emit_one_logical_rebuild_per_version(
    engine: AsyncEngine,
    session: AsyncSession,
    seeded_user: User,
) -> None:
    workspace = await _seed_workspace(session, seeded_user, "Concurrent")
    await _seed_legacy_versions(
        session,
        user=seeded_user,
        workspace=workspace,
        identifiers=[3000, 3001, 3002],
    )
    factory = build_session_factory(engine)
    generation_id = uuid4()

    await asyncio.gather(
        scan_legacy_pending(
            factory,
            authority_generation_id=generation_id,
            page_size=2,
            max_pages=3,
        ),
        scan_legacy_pending(
            factory,
            authority_generation_id=generation_id,
            page_size=2,
            max_pages=3,
        ),
    )

    async with factory() as verify:
        aggregate_ids = list(
            (
                await verify.scalars(
                    select(OutboxEvent.aggregate_id).order_by(OutboxEvent.aggregate_id)
                )
            ).all()
        )
    assert [value.int for value in aggregate_ids] == [3000, 3001, 3002]


async def test_scanner_pages_through_1001_versions_with_bounded_transactions(
    engine: AsyncEngine,
    session: AsyncSession,
    seeded_user: User,
) -> None:
    workspace = await _seed_workspace(session, seeded_user, "Large legacy scan")
    await _seed_legacy_versions(
        session,
        user=seeded_user,
        workspace=workspace,
        identifiers=list(range(10_000, 11_001)),
    )
    factory = build_session_factory(engine)

    result = await scan_legacy_pending(
        factory,
        authority_generation_id=uuid4(),
        page_size=128,
        max_pages=8,
    )

    async with factory() as verify:
        count = await verify.scalar(select(func.count()).select_from(OutboxEvent))
    assert result.pages == 8
    assert result.passes_completed == 1
    assert result.scanned == 1_001
    assert result.emitted == 1_001
    assert result.skipped == 0
    assert count == 1_001
