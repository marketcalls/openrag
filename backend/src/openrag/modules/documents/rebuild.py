"""Restart-safe, content-free discovery of legacy authority rebuild work."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.db import naive_utc
from openrag.modules.documents.models import (
    DocumentVersion,
    IngestStageAttempt,
    LegacyRebuildScanCheckpoint,
)
from openrag.modules.events.envelopes import DocumentVersionRebuildRequestedV1
from openrag.modules.events.models import OutboxEvent
from openrag.modules.events.outbox import add_registered_event

_MAX_PAGE_SIZE = 1_000
_MAX_PAGES = 100


@dataclass(frozen=True, slots=True)
class LegacyScanResult:
    pages: int = 0
    passes_completed: int = 0
    scanned: int = 0
    emitted: int = 0
    skipped: int = 0


async def _ensure_workspace_checkpoints(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        scopes = list(
            (
                await session.execute(
                    select(DocumentVersion.org_id, DocumentVersion.workspace_id)
                    .where(DocumentVersion.provenance_state == "legacy_pending")
                    .distinct()
                )
            ).all()
        )
        if not scopes:
            return
        now = naive_utc()
        await session.execute(
            insert(LegacyRebuildScanCheckpoint)
            .values(
                [
                    {
                        "id": uuid4(),
                        "created_at": now,
                        "org_id": org_id,
                        "workspace_id": workspace_id,
                        "pass_number": 0,
                        "scanned_count": 0,
                        "emitted_count": 0,
                        "skipped_count": 0,
                        "pass_started_at": now,
                        "updated_at": now,
                    }
                    for org_id, workspace_id in scopes
                ]
            )
            .on_conflict_do_nothing(
                constraint="uq_legacy_rebuild_scan_checkpoints_workspace"
            )
        )


async def scan_legacy_pending(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    authority_generation_id: UUID,
    page_size: int = 500,
    max_pages: int = 1,
) -> LegacyScanResult:
    """Commit bounded keyset pages and enqueue idempotent rebuild commands.

    The scanner performs PostgreSQL work only. The transactional outbox relay is
    solely responsible for later Redis publication.
    """

    if not 1 <= page_size <= _MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {_MAX_PAGE_SIZE}")
    if not 1 <= max_pages <= _MAX_PAGES:
        raise ValueError(f"max_pages must be between 1 and {_MAX_PAGES}")

    await _ensure_workspace_checkpoints(session_factory)
    pages = passes_completed = scanned = emitted = skipped = 0
    while pages < max_pages:
        async with session_factory() as session, session.begin():
            checkpoint_query = (
                select(LegacyRebuildScanCheckpoint)
                .order_by(
                    LegacyRebuildScanCheckpoint.updated_at,
                    LegacyRebuildScanCheckpoint.workspace_id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            checkpoint = await session.scalar(checkpoint_query)
            if checkpoint is None:
                break

            now = naive_utc()
            if checkpoint.cursor_document_version_id is None:
                checkpoint.pass_started_at = now
                checkpoint.completed_at = None

            version_query = (
                select(DocumentVersion)
                .where(
                    DocumentVersion.org_id == checkpoint.org_id,
                    DocumentVersion.workspace_id == checkpoint.workspace_id,
                    DocumentVersion.provenance_state == "legacy_pending",
                )
                .order_by(DocumentVersion.id)
                .limit(page_size)
            )
            if checkpoint.cursor_document_version_id is not None:
                version_query = version_query.where(
                    DocumentVersion.id > checkpoint.cursor_document_version_id
                )
            versions = list((await session.scalars(version_query)).all())
            version_ids = [version.id for version in versions]

            existing_event_ids: set[UUID] = set()
            active_attempt_ids: set[UUID] = set()
            if version_ids:
                existing_event_ids = set(
                    (
                        await session.scalars(
                            select(OutboxEvent.aggregate_id).where(
                                OutboxEvent.aggregate_id.in_(version_ids),
                                OutboxEvent.event_type
                                == "document.version.rebuild_requested.v1",
                            )
                        )
                    ).all()
                )
                active_attempt_ids = set(
                    (
                        await session.scalars(
                            select(IngestStageAttempt.document_version_id)
                            .where(
                                IngestStageAttempt.document_version_id.in_(version_ids),
                                IngestStageAttempt.pipeline_kind == "rebuild",
                            )
                            .distinct()
                        )
                    ).all()
                )

            page_emitted = 0
            page_skipped = 0
            for version in versions:
                if version.id in existing_event_ids or version.id in active_attempt_ids:
                    page_skipped += 1
                    continue
                add_registered_event(
                    session,
                    payload=DocumentVersionRebuildRequestedV1(
                        document_id=version.document_id,
                        authority_generation_id=authority_generation_id,
                    ),
                    org_id=version.org_id,
                    workspace_id=version.workspace_id,
                    aggregate_id=version.id,
                    lifecycle_revision=version.lifecycle_revision,
                    correlation_id=uuid4(),
                    occurred_at=datetime.now(UTC),
                )
                page_emitted += 1

            page_scanned = len(versions)
            checkpoint.scanned_count += page_scanned
            checkpoint.emitted_count += page_emitted
            checkpoint.skipped_count += page_skipped
            checkpoint.updated_at = now
            if versions:
                checkpoint.cursor_document_version_id = versions[-1].id

            pass_complete = len(versions) < page_size
            if pass_complete:
                checkpoint.cursor_document_version_id = None
                checkpoint.pass_number += 1
                checkpoint.completed_at = now
                passes_completed += 1

            pages += 1
            scanned += page_scanned
            emitted += page_emitted
            skipped += page_skipped

    return LegacyScanResult(
        pages=pages,
        passes_completed=passes_completed,
        scanned=scanned,
        emitted=emitted,
        skipped=skipped,
    )
