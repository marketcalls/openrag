import asyncio
import hashlib

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from openrag.core.db import build_session_factory, naive_utc
from openrag.core.errors import ConflictError
from openrag.modules.documents import service
from openrag.modules.documents.models import (
    Document,
    DocumentVersion,
    DocumentVersionDecisionRecord,
)
from openrag.modules.events.models import OutboxEvent
from tests.modules.retrieval.test_retrieve import seed_workspace


async def test_two_successor_approvals_from_same_snapshot_have_one_winner(
    engine: AsyncEngine,
) -> None:
    factory = build_session_factory(engine)
    async with factory() as seed_session:
        context, workspace = await seed_workspace(
            seed_session, "race-two-successors", role="admin"
        )
        document = Document(
            org_id=context.org_id,
            workspace_id=workspace.id,
            name="Controlled manual",
            created_by=context.user_id,
        )
        seed_session.add(document)
        await seed_session.flush()

        versions: list[DocumentVersion] = []
        for sequence, state in ((1, "approved"), (2, "review"), (3, "review")):
            version = DocumentVersion(
                org_id=context.org_id,
                workspace_id=workspace.id,
                document_id=document.id,
                sequence=sequence,
                version_label=f"Rev {sequence}",
                version_key=f"rev {sequence}",
                content_hash=hashlib.sha256(f"rev-{sequence}".encode()).hexdigest(),
                source_filename="manual.pdf",
                source_mime="application/pdf",
                source_size_bytes=100,
                source_storage_key=f"versions/{sequence}/source",
                source_page_count=2,
                parser_profile_version="docling/v1",
                ocr_profile_version="none/v1",
                chunking_profile_version="semantic/v1",
                embedding_profile_version="bge-m3/v1",
                index_profile_version="hybrid/v1",
                state=state,
                provenance_state="ready",
                created_by=context.user_id,
                approved_by=context.user_id if state == "approved" else None,
                approved_at=naive_utc() if state == "approved" else None,
                decision_at=naive_utc() if state == "approved" else None,
            )
            versions.append(version)
            seed_session.add(version)
        await seed_session.commit()
        document_id = document.id
        first_id, second_id = versions[1].id, versions[2].id

    # Hold the logical-document row so both commands take their optimistic
    # candidate/incumbent snapshots before either can acquire the global lock.
    async with factory() as blocker:
        await blocker.execute(
            select(Document)
            .where(Document.id == document_id)
            .with_for_update()
        )

        async def approve(version_id):  # type: ignore[no-untyped-def]
            async with factory() as contender:
                try:
                    result = await service.approve_version(
                        contender, context, version_id, reason=None
                    )
                    return result.id
                except ConflictError:
                    return "conflict"

        tasks = [
            asyncio.create_task(approve(first_id)),
            asyncio.create_task(approve(second_id)),
        ]
        await asyncio.sleep(0.1)
        await blocker.commit()
        outcomes = await asyncio.gather(*tasks)

    assert outcomes.count("conflict") == 1
    async with factory() as verify:
        stored = list(
            (
                await verify.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == document_id)
                    .order_by(DocumentVersion.sequence)
                )
            ).scalars()
        )
        assert sum(version.state == "approved" for version in stored) == 1
        assert stored[0].state == "superseded"
        assert stored[0].superseded_by_id in {first_id, second_id}
        assert [version.lifecycle_revision for version in stored] in (
            [2, 2, 1],
            [2, 1, 2],
        )


async def _seed_race_document(
    engine: AsyncEngine,
    name: str,
    states: tuple[str, ...],
):  # type: ignore[no-untyped-def]
    factory = build_session_factory(engine)
    async with factory() as session:
        context, workspace = await seed_workspace(session, name, role="admin")
        document = Document(
            org_id=context.org_id,
            workspace_id=workspace.id,
            name="Race manual",
            created_by=context.user_id,
        )
        session.add(document)
        await session.flush()
        versions = []
        for index, state in enumerate(states, start=1):
            version = DocumentVersion(
                org_id=context.org_id,
                workspace_id=workspace.id,
                document_id=document.id,
                sequence=index,
                version_label=f"Rev {index}",
                version_key=f"rev {index}",
                content_hash=hashlib.sha256(f"{name}-{index}".encode()).hexdigest(),
                source_filename="manual.pdf",
                source_mime="application/pdf",
                source_size_bytes=100,
                source_storage_key=f"versions/{name}/{index}/source",
                source_page_count=2,
                parser_profile_version="docling/v1",
                ocr_profile_version="none/v1",
                chunking_profile_version="semantic/v1",
                embedding_profile_version="bge-m3/v1",
                index_profile_version="hybrid/v1",
                state=state,
                provenance_state="ready" if state in {"review", "approved"} else "failed",
                created_by=context.user_id,
                approved_by=context.user_id if state == "approved" else None,
                approved_at=naive_utc() if state == "approved" else None,
                decision_at=naive_utc() if state == "approved" else None,
            )
            session.add(version)
            versions.append(version)
        await session.commit()
        return factory, context, document.id, tuple(version.id for version in versions)


async def _run_blocked_race(
    factory,  # type: ignore[no-untyped-def]
    document_id,
    contenders,  # type: ignore[no-untyped-def]
) -> list[object]:
    async with factory() as blocker:
        await blocker.execute(
            select(Document).where(Document.id == document_id).with_for_update()
        )

        async def run(contender):  # type: ignore[no-untyped-def]
            async with factory() as session:
                try:
                    return await contender(session)
                except ConflictError:
                    return "conflict"

        tasks = [asyncio.create_task(run(contender)) for contender in contenders]
        await asyncio.sleep(0.1)
        await blocker.commit()
        return list(await asyncio.gather(*tasks))


async def test_approve_vs_reject_same_snapshot_has_one_winner(
    engine: AsyncEngine,
) -> None:
    factory, context, document_id, version_ids = await _seed_race_document(
        engine, "race-approve-reject", ("review",)
    )
    (candidate_id,) = version_ids
    outcomes = await _run_blocked_race(
        factory,
        document_id,
        (
            lambda session: service.approve_version(
                session, context, candidate_id, reason=None
            ),
            lambda session: service.reject_version(
                session, context, candidate_id, reason=None
            ),
        ),
    )

    assert outcomes.count("conflict") == 1
    async with factory() as verify:
        version = await verify.get(DocumentVersion, candidate_id)
        assert version is not None and version.state in {"approved", "rejected"}
        assert version.lifecycle_revision == 2
        assert (
            await verify.scalar(
                select(func.count()).select_from(DocumentVersionDecisionRecord)
            )
        ) == 1
        assert await verify.scalar(select(func.count()).select_from(OutboxEvent)) == 1


async def test_approve_vs_obsolete_incumbent_snapshot_has_one_winner(
    engine: AsyncEngine,
) -> None:
    factory, context, document_id, version_ids = await _seed_race_document(
        engine, "race-approve-obsolete", ("approved", "review")
    )
    incumbent_id, candidate_id = version_ids
    outcomes = await _run_blocked_race(
        factory,
        document_id,
        (
            lambda session: service.approve_version(
                session, context, candidate_id, reason=None
            ),
            lambda session: service.obsolete_version(
                session, context, incumbent_id, reason=None
            ),
        ),
    )

    assert outcomes.count("conflict") == 1
    async with factory() as verify:
        incumbent = await verify.get(DocumentVersion, incumbent_id)
        candidate = await verify.get(DocumentVersion, candidate_id)
        assert incumbent is not None and candidate is not None
        assert (incumbent.state, candidate.state) in {
            ("superseded", "approved"),
            ("obsolete", "review"),
        }
        assert not (incumbent.state == "approved" and candidate.state == "approved")


async def test_retry_vs_delete_same_snapshot_has_one_winner(
    engine: AsyncEngine,
) -> None:
    factory, context, document_id, version_ids = await _seed_race_document(
        engine, "race-retry-delete", ("failed",)
    )
    (version_id,) = version_ids
    outcomes = await _run_blocked_race(
        factory,
        document_id,
        (
            lambda session: service.retry_version(session, context, version_id),
            lambda session: service.request_document_deletion(
                session, context, version_id
            ),
        ),
    )

    assert outcomes.count("conflict") == 1
    async with factory() as verify:
        version = await verify.get(DocumentVersion, version_id)
        assert version is not None
        assert (version.state, version.source_delete_requested_at is not None) in {
            ("processing", False),
            ("failed", True),
        }


async def test_blocked_document_does_not_serialize_unrelated_workspace_document(
    engine: AsyncEngine,
) -> None:
    factory = build_session_factory(engine)
    async with factory() as seed:
        context, workspace = await seed_workspace(
            seed, "race-workspace-parallelism", role="admin"
        )
        pairs: list[tuple[Document, DocumentVersion]] = []
        for index in (1, 2):
            document = Document(
                org_id=context.org_id,
                workspace_id=workspace.id,
                name=f"Independent manual {index}",
                created_by=context.user_id,
            )
            seed.add(document)
            await seed.flush()
            version = DocumentVersion(
                org_id=context.org_id,
                workspace_id=workspace.id,
                document_id=document.id,
                sequence=1,
                version_label="Rev 1",
                version_key="rev 1",
                content_hash=hashlib.sha256(f"parallel-{index}".encode()).hexdigest(),
                source_filename="manual.pdf",
                source_mime="application/pdf",
                source_size_bytes=100,
                source_storage_key=f"versions/parallel/{index}/source",
                parser_profile_version="docling/v1",
                ocr_profile_version="none/v1",
                chunking_profile_version="semantic/v1",
                embedding_profile_version="bge-m3/v1",
                index_profile_version="hybrid/v1",
                state="failed",
                provenance_state="failed",
                created_by=context.user_id,
            )
            seed.add(version)
            pairs.append((document, version))
        await seed.commit()
        first_document_id = pairs[0][0].id
        first_version_id = pairs[0][1].id
        second_version_id = pairs[1][1].id

    async def retry(version_id):  # type: ignore[no-untyped-def]
        async with factory() as contender:
            return await service.retry_version(contender, context, version_id)

    async with factory() as blocker:
        blocker_pid = await blocker.scalar(select(func.pg_backend_pid()))
        await blocker.execute(
            select(Document)
            .where(Document.id == first_document_id)
            .with_for_update()
        )
        first_task = asyncio.create_task(retry(first_version_id))
        deadline = asyncio.get_running_loop().time() + 2
        first_is_waiting = False
        while asyncio.get_running_loop().time() < deadline and not first_is_waiting:
            async with factory() as observer:
                first_is_waiting = bool(
                    await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE datname=current_database() AND pid<>:blocker_pid "
                            "AND wait_event_type='Lock' "
                            "AND query LIKE '%documents%FOR UPDATE%')"
                        ),
                        {"blocker_pid": blocker_pid},
                    )
                )
            if not first_is_waiting:
                await asyncio.sleep(0.01)
        assert first_is_waiting

        second_task = asyncio.create_task(retry(second_version_id))
        second_completed_while_first_blocked = True
        try:
            await asyncio.wait_for(asyncio.shield(second_task), timeout=0.5)
        except TimeoutError:
            second_completed_while_first_blocked = False
        finally:
            await blocker.commit()
            await asyncio.gather(first_task, second_task)

    assert second_completed_while_first_blocked


async def test_lifecycle_snapshot_refreshes_stale_identity_map(
    engine: AsyncEngine,
) -> None:
    factory = build_session_factory(engine)
    async with factory() as command_session:
        context, workspace = await seed_workspace(
            command_session, "stale-lifecycle-snapshot", role="admin"
        )
        document = Document(
            org_id=context.org_id,
            workspace_id=workspace.id,
            name="Cached manual",
            created_by=context.user_id,
        )
        command_session.add(document)
        await command_session.flush()
        version = DocumentVersion(
            org_id=context.org_id,
            workspace_id=workspace.id,
            document_id=document.id,
            sequence=1,
            version_label="Rev 1",
            version_key="rev 1",
            content_hash=hashlib.sha256(b"cached-version").hexdigest(),
            source_filename="manual.pdf",
            source_mime="application/pdf",
            source_size_bytes=100,
            source_storage_key="versions/cached/source",
            parser_profile_version="docling/v1",
            ocr_profile_version="none/v1",
            chunking_profile_version="semantic/v1",
            embedding_profile_version="bge-m3/v1",
            index_profile_version="hybrid/v1",
            state="failed",
            provenance_state="failed",
            created_by=context.user_id,
        )
        command_session.add(version)
        await command_session.commit()
        version_id = version.id
        assert version.lifecycle_revision == 1

        async with factory() as external_writer:
            await external_writer.execute(
                update(DocumentVersion)
                .where(DocumentVersion.id == version_id)
                .values(lifecycle_revision=2)
            )
            await external_writer.commit()

        retried = await service.retry_version(
            command_session, context, version_id
        )
        assert (retried.state, retried.lifecycle_revision) == ("processing", 3)
