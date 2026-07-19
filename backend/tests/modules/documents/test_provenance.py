import hashlib
from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.documents.models import (
    DocumentBlock,
    DocumentChunk,
    DocumentChunkBlock,
    DocumentEvidenceSpan,
    DocumentVersion,
)
from openrag.modules.documents.pipeline import (
    Chunk,
    EvidenceSpan,
    IngestFailure,
    PageBlock,
    chunk_blocks,
)
from openrag.modules.documents.provenance import persist_page_provenance
from tests.modules.documents.test_service import seed_review_version


def fixture_provenance() -> tuple[list[PageBlock], list[Chunk], list[EvidenceSpan]]:
    blocks = [
        PageBlock(1, "Emergency isolation", "heading", ("Emergency",)),
        PageBlock(1, "Close valve A.", "text", ("Emergency",)),
        PageBlock(2, "Notify the HSE manager.", "text", ("Emergency",)),
    ]
    chunks, spans = chunk_blocks(blocks, target_chars=2000)
    return blocks, chunks, spans


async def _counts(
    session: AsyncSession,
    version_id: UUID,
) -> tuple[int, int, int, int]:
    counts: list[int] = []
    for model in (
        DocumentBlock,
        DocumentChunk,
        DocumentChunkBlock,
        DocumentEvidenceSpan,
    ):
        counts.append(
            int(
                await session.scalar(
                    select(func.count()).select_from(model).where(
                        model.document_version_id == version_id
                    )
                )
                or 0
            )
        )
    return counts[0], counts[1], counts[2], counts[3]


async def test_page_provenance_persists_exact_version_scoped_rows_idempotently(
    session: AsyncSession,
) -> None:
    _context, _document, version, _ = await seed_review_version(
        session,
        "persist-provenance",
        candidate_state="processing",
        candidate_provenance="building",
    )
    blocks, chunks, spans = fixture_provenance()

    await persist_page_provenance(session, version, blocks, chunks, spans)
    await session.commit()

    persisted_blocks = list(
        (
            await session.execute(
                select(DocumentBlock)
                .where(DocumentBlock.document_version_id == version.id)
                .order_by(DocumentBlock.ordinal)
            )
        ).scalars()
    )
    persisted_chunks = list(
        (
            await session.execute(
                select(DocumentChunk).where(
                    DocumentChunk.document_version_id == version.id
                )
            )
        ).scalars()
    )
    persisted_spans = list(
        (
            await session.execute(
                select(DocumentEvidenceSpan)
                .where(DocumentEvidenceSpan.document_version_id == version.id)
                .order_by(DocumentEvidenceSpan.ordinal)
            )
        ).scalars()
    )
    first_ids = {
        *(row.id for row in persisted_blocks),
        *(row.id for row in persisted_chunks),
        *(row.id for row in persisted_spans),
    }

    assert await _counts(session, version.id) == (3, 1, 3, 2)
    assert [row.page_number for row in persisted_spans] == [1, 2]
    assert [row.text for row in persisted_blocks] == [block.text for block in blocks]
    assert all(len(row.content_hash) == 64 for row in persisted_blocks + persisted_chunks)
    encoded = persisted_chunks[0].text.encode("utf-8")
    for row in persisted_spans:
        exact = encoded[row.artifact_byte_start : row.artifact_byte_end]
        assert hashlib.sha256(exact).hexdigest() == row.content_hash

    await persist_page_provenance(session, version, blocks, chunks, spans)
    await session.commit()

    assert await _counts(session, version.id) == (3, 1, 3, 2)
    second_ids = set(
        await session.scalars(
            select(DocumentBlock.id).where(DocumentBlock.document_version_id == version.id)
        )
    ) | set(
        await session.scalars(
            select(DocumentChunk.id).where(DocumentChunk.document_version_id == version.id)
        )
    ) | set(
        await session.scalars(
            select(DocumentEvidenceSpan.id).where(
                DocumentEvidenceSpan.document_version_id == version.id
            )
        )
    )
    assert second_ids == first_ids


async def test_persistence_rejects_conflicting_retry(
    session: AsyncSession,
) -> None:
    _context, _document, version, _ = await seed_review_version(
        session,
        "persist-conflict",
        candidate_state="processing",
        candidate_provenance="building",
    )
    blocks, chunks, spans = fixture_provenance()
    await persist_page_provenance(session, version, blocks, chunks, spans)
    await session.commit()

    changed = [replace(blocks[0], text="Changed evidence"), *blocks[1:]]
    changed_chunks, changed_spans = chunk_blocks(changed, target_chars=2000)

    with pytest.raises(IngestFailure, match="conflicts"):
        await persist_page_provenance(
            session,
            version,
            changed,
            changed_chunks,
            changed_spans,
        )


async def test_persistence_rejects_inexact_span_byte_range(
    session: AsyncSession,
) -> None:
    _context, _document, version, _ = await seed_review_version(
        session,
        "persist-bad-range",
        candidate_state="processing",
        candidate_provenance="building",
    )
    blocks, chunks, spans = fixture_provenance()
    invalid = [replace(spans[0], artifact_byte_end=spans[0].artifact_byte_end + 1), *spans[1:]]

    with pytest.raises(IngestFailure, match="byte range"):
        await persist_page_provenance(session, version, blocks, chunks, invalid)


async def test_approved_legacy_rebuild_persists_then_seals_page_provenance(
    session: AsyncSession,
    chat_env: dict[str, object],
) -> None:
    document = chat_env["document"]
    version = await session.get(DocumentVersion, document.id)  # type: ignore[attr-defined]
    assert version is not None
    version.source_page_count = 2
    version.provenance_state = "building"
    await session.commit()
    blocks, chunks, spans = fixture_provenance()

    await persist_page_provenance(session, version, blocks, chunks, spans)
    await session.commit()
    version.provenance_state = "ready"
    await session.commit()

    assert await _counts(session, version.id) == (3, 1, 3, 2)
    with pytest.raises(IngestFailure, match="not accepting"):
        await persist_page_provenance(session, version, blocks, chunks, spans)
