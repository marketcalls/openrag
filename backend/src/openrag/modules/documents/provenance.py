"""Deterministic, version-scoped persistence for page-local provenance."""

import hashlib
import json
from collections.abc import Callable
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.documents.models import (
    DocumentBlock,
    DocumentChunk,
    DocumentChunkBlock,
    DocumentEvidenceSpan,
    DocumentVersion,
)
from openrag.modules.documents.pipeline import Chunk, EvidenceSpan, IngestFailure, PageBlock

_PROVENANCE_NAMESPACE = UUID("bc6e7280-e5bf-49a5-91d1-8c85e771ab43")
_MAX_ROW_TEXT_BYTES = 1_000_000
_MAX_COORDINATES_BYTES = 8192


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_id(version_id: UUID, kind: str, ordinal: int, digest: str) -> UUID:
    return uuid5(_PROVENANCE_NAMESPACE, f"{version_id}:{kind}:{ordinal}:{digest}")


def _bounded_text(text: str) -> None:
    if not text.strip():
        raise IngestFailure("provenance text is empty")
    if len(text.encode("utf-8")) > _MAX_ROW_TEXT_BYTES:
        raise IngestFailure("provenance row exceeds text limit")


def _validate_contract(
    blocks: list[PageBlock],
    chunks: list[Chunk],
    spans: list[EvidenceSpan],
) -> None:
    if not blocks or not chunks or not spans:
        raise IngestFailure("complete page provenance is required")
    if [chunk.chunk_index for chunk in chunks] != list(range(len(chunks))):
        raise IngestFailure("chunk ordinals are not contiguous")
    if [span.span_index for span in spans] != list(range(len(spans))):
        raise IngestFailure("evidence span ordinals are not contiguous")
    for block in blocks:
        _bounded_text(block.text)
        if block.source_coordinates is not None:
            encoded = json.dumps(
                block.source_coordinates,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > _MAX_COORDINATES_BYTES:
                raise IngestFailure("source coordinates exceed limit")
    for chunk in chunks:
        _bounded_text(chunk.text)
        if not chunk.block_ordinals or any(
            ordinal < 0 or ordinal >= len(blocks) for ordinal in chunk.block_ordinals
        ):
            raise IngestFailure("chunk block membership is invalid")
        if len(set(chunk.block_ordinals)) != len(chunk.block_ordinals):
            raise IngestFailure("chunk block membership contains duplicates")
        pages = [blocks[ordinal].page for ordinal in chunk.block_ordinals]
        if (chunk.page_start, chunk.page_end) != (min(pages), max(pages)):
            raise IngestFailure("chunk page range conflicts with block provenance")
    for span in spans:
        if span.chunk_index < 0 or span.chunk_index >= len(chunks):
            raise IngestFailure("evidence span chunk is invalid")
        chunk = chunks[span.chunk_index]
        encoded = chunk.text.encode("utf-8")
        if (
            span.artifact_byte_start < 0
            or span.artifact_byte_end <= span.artifact_byte_start
            or span.artifact_byte_end > len(encoded)
            or encoded[span.artifact_byte_start : span.artifact_byte_end]
            != span.text.encode("utf-8")
        ):
            raise IngestFailure("evidence span byte range is inexact")
        if not span.block_ordinals or not set(span.block_ordinals).issubset(
            chunk.block_ordinals
        ):
            raise IngestFailure("evidence span membership is invalid")
        if any(blocks[ordinal].page != span.page_number for ordinal in span.block_ordinals):
            raise IngestFailure("evidence span crosses a page boundary")
        if any(
            blocks[ordinal].section_path != span.section_path
            or blocks[ordinal].locator_kind != span.locator_kind
            or (blocks[ordinal].locator_label or str(blocks[ordinal].page))
            != span.locator_label
            for ordinal in span.block_ordinals
        ):
            raise IngestFailure("evidence span locator conflicts with block provenance")
        _bounded_text(span.text)
    if {span.chunk_index for span in spans} != set(range(len(chunks))):
        raise IngestFailure("every chunk requires page-local evidence")


def _expected_rows(
    version: DocumentVersion,
    blocks: list[PageBlock],
    chunks: list[Chunk],
    spans: list[EvidenceSpan],
) -> tuple[
    list[DocumentBlock],
    list[DocumentChunk],
    list[DocumentChunkBlock],
    list[DocumentEvidenceSpan],
]:
    block_rows = [
        DocumentBlock(
            id=_stable_id(version.id, "block", ordinal, _digest(block.text)),
            org_id=version.org_id,
            document_version_id=version.id,
            ordinal=ordinal,
            text=block.text,
            page_number=block.page,
            locator_kind=block.locator_kind,
            locator_label=block.locator_label or str(block.page),
            block_type=block.kind,
            section_path=list(block.section_path),
            source_coordinates=block.source_coordinates,
            extraction_method=block.extraction_method,
            ocr_profile_version=version.ocr_profile_version,
            ocr_confidence=block.ocr_confidence,
            content_hash=_digest(block.text),
        )
        for ordinal, block in enumerate(blocks)
    ]
    chunk_rows = [
        DocumentChunk(
            id=_stable_id(version.id, "chunk", chunk.chunk_index, _digest(chunk.text)),
            org_id=version.org_id,
            document_version_id=version.id,
            ordinal=chunk.chunk_index,
            text=chunk.text,
            token_count=len(chunk.text.split()),
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            section_path=list(chunk.section_path),
            content_hash=_digest(chunk.text),
            chunking_profile_version=version.chunking_profile_version,
            embedding_profile_version=version.embedding_profile_version,
        )
        for chunk in chunks
    ]
    membership_rows: list[DocumentChunkBlock] = []
    membership_ordinal = 0
    for chunk in chunks:
        for position, block_ordinal in enumerate(chunk.block_ordinals):
            identity = (
                f"{chunk_rows[chunk.chunk_index].id}:"
                f"{block_rows[block_ordinal].id}:{position}"
            )
            membership_rows.append(
                DocumentChunkBlock(
                    id=_stable_id(
                        version.id,
                        "membership",
                        membership_ordinal,
                        hashlib.sha256(identity.encode()).hexdigest(),
                    ),
                    org_id=version.org_id,
                    document_version_id=version.id,
                    chunk_id=chunk_rows[chunk.chunk_index].id,
                    block_id=block_rows[block_ordinal].id,
                    position=position,
                )
            )
            membership_ordinal += 1
    span_rows = [
        DocumentEvidenceSpan(
            id=_stable_id(version.id, "span", span.span_index, _digest(span.text)),
            org_id=version.org_id,
            document_version_id=version.id,
            chunk_id=chunk_rows[span.chunk_index].id,
            page_number=span.page_number,
            locator_kind=span.locator_kind,
            locator_label=span.locator_label,
            section_path=list(span.section_path),
            content_hash=_digest(span.text),
            ordinal=span.span_index,
            token_count=len(span.text.split()),
            artifact_byte_start=span.artifact_byte_start,
            artifact_byte_end=span.artifact_byte_end,
        )
        for span in spans
    ]
    return block_rows, chunk_rows, membership_rows, span_rows


async def _load_rows[RowT](
    session: AsyncSession,
    model: type[RowT],
    version_id: UUID,
) -> list[RowT]:
    return list(
        (
            await session.execute(
                select(model).where(model.document_version_id == version_id)  # type: ignore[attr-defined]
            )
        ).scalars()
    )


def _fingerprints[RowT](
    rows: list[RowT],
    factory: Callable[[RowT], tuple[object, ...]],
) -> set[tuple[object, ...]]:
    return {factory(row) for row in rows}


def _same_rows(
    existing: tuple[
        list[DocumentBlock],
        list[DocumentChunk],
        list[DocumentChunkBlock],
        list[DocumentEvidenceSpan],
    ],
    expected: tuple[
        list[DocumentBlock],
        list[DocumentChunk],
        list[DocumentChunkBlock],
        list[DocumentEvidenceSpan],
    ],
) -> bool:
    block = lambda row: (  # noqa: E731
        row.id,
        row.ordinal,
        row.text,
        row.page_number,
        row.locator_kind,
        row.locator_label,
        row.block_type,
        tuple(row.section_path),
        (
            json.dumps(row.source_coordinates, sort_keys=True, separators=(",", ":"))
            if row.source_coordinates is not None
            else None
        ),
        row.extraction_method,
        row.ocr_profile_version,
        row.ocr_confidence,
        row.content_hash,
    )
    chunk = lambda row: (  # noqa: E731
        row.id,
        row.ordinal,
        row.text,
        row.token_count,
        row.page_start,
        row.page_end,
        tuple(row.section_path),
        row.content_hash,
        row.chunking_profile_version,
        row.embedding_profile_version,
    )
    membership = lambda row: (row.id, row.chunk_id, row.block_id, row.position)  # noqa: E731
    span = lambda row: (  # noqa: E731
        row.id,
        row.chunk_id,
        row.page_number,
        row.locator_kind,
        row.locator_label,
        tuple(row.section_path),
        row.content_hash,
        row.ordinal,
        row.token_count,
        row.artifact_byte_start,
        row.artifact_byte_end,
    )
    return (
        _fingerprints(existing[0], block) == _fingerprints(expected[0], block)
        and _fingerprints(existing[1], chunk) == _fingerprints(expected[1], chunk)
        and _fingerprints(existing[2], membership)
        == _fingerprints(expected[2], membership)
        and _fingerprints(existing[3], span) == _fingerprints(expected[3], span)
    )


async def persist_page_provenance(
    session: AsyncSession,
    version: DocumentVersion,
    blocks: list[PageBlock],
    chunks: list[Chunk],
    spans: list[EvidenceSpan],
) -> None:
    """Persist one complete deterministic provenance result or reject conflicts."""

    if version.state != "processing" or version.provenance_state != "building":
        raise IngestFailure("version is not accepting provenance")
    _validate_contract(blocks, chunks, spans)
    expected = _expected_rows(version, blocks, chunks, spans)
    existing = (
        await _load_rows(session, DocumentBlock, version.id),
        await _load_rows(session, DocumentChunk, version.id),
        await _load_rows(session, DocumentChunkBlock, version.id),
        await _load_rows(session, DocumentEvidenceSpan, version.id),
    )
    if any(existing):
        if _same_rows(existing, expected):
            return
        raise IngestFailure("persisted provenance conflicts with completed output")
    for rows in expected:
        session.add_all(rows)
    await session.flush()
