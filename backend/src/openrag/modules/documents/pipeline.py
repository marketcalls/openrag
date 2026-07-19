"""Pure ingestion pipeline stages shared by workers and integration tests."""

import asyncio
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

from qdrant_client import models

from openrag.modules.documents.lifecycle import validate_section_path
from openrag.modules.retrieval.client import COLLECTION, get_qdrant
from openrag.modules.retrieval.embeddings import DenseEmbedder, embed_sparse

_CHUNK_NAMESPACE = UUID("6c7d9a52-3e1f-4b8a-9c0d-2f5e8b1a7d43")


class IngestFailure(Exception):
    """Terminal ingestion failure caused by invalid or unsupported input."""


@dataclass(frozen=True)
class PageBlock:
    page: int
    text: str
    kind: str
    section_path: tuple[str, ...] = ("Document",)
    locator_kind: str = "page"
    locator_label: str | None = None

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError("page must be positive")
        object.__setattr__(
            self,
            "section_path",
            validate_section_path(list(self.section_path)),
        )
        if self.locator_label is None:
            object.__setattr__(self, "locator_label", str(self.page))


@dataclass(frozen=True)
class Chunk:
    text: str
    page_start: int
    page_end: int
    chunk_index: int
    section_path: tuple[str, ...]
    block_ordinals: tuple[int, ...]

    @property
    def page(self) -> int:
        """Compatibility locator for the legacy retrieval collection."""

        return self.page_start


@dataclass(frozen=True)
class EvidenceSpan:
    text: str
    page_number: int
    locator_kind: str
    locator_label: str
    section_path: tuple[str, ...]
    chunk_index: int
    span_index: int
    artifact_byte_start: int
    artifact_byte_end: int
    block_ordinals: tuple[int, ...]


@dataclass(frozen=True)
class _Piece:
    text: str
    page: int
    locator_kind: str
    locator_label: str
    section_path: tuple[str, ...]
    block_ordinal: int


def parse_bytes(data: bytes, filename: str) -> list[PageBlock]:
    if not data:
        raise IngestFailure("file is empty")
    suffix = Path(filename).suffix.lower()
    if suffix == ".txt":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IngestFailure("text file is not valid UTF-8") from exc
        text_blocks = [
            PageBlock(page=1, text=paragraph.strip(), kind="text")
            for paragraph in text.split("\n\n")
            if paragraph.strip()
        ]
        if not text_blocks:
            raise IngestFailure("document contains no extractable text")
        return text_blocks

    from docling.document_converter import DocumentConverter
    from docling_core.types.doc import (  # type: ignore[attr-defined]
        DocItemLabel,
        TableItem,
        TextItem,
    )

    heading_labels = {DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER}
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
        temporary.write(data)
        temporary_path = Path(temporary.name)
    try:
        try:
            result = DocumentConverter().convert(
                temporary_path,
                raises_on_error=True,
            )
        except Exception as exc:
            raise IngestFailure(
                f"unsupported or unparsable document: {exc}"
            ) from exc
    finally:
        temporary_path.unlink(missing_ok=True)

    blocks: list[PageBlock] = []
    for item, _level in result.document.iterate_items():
        provenance = getattr(item, "prov", None)
        page = provenance[0].page_no if provenance else 1
        if isinstance(item, TableItem):
            text = item.export_to_markdown(result.document)
            kind = "table"
        elif isinstance(item, TextItem):
            text = item.text
            kind = "heading" if item.label in heading_labels else "text"
        else:
            continue
        if text.strip():
            blocks.append(
                PageBlock(page=page, text=text.strip(), kind=kind)
            )
    if not blocks:
        raise IngestFailure("document contains no extractable text")
    return blocks


def _split_text(text: str, limit: int) -> list[str]:
    words = text.split()
    pieces: list[str] = []
    buffer: list[str] = []
    size = 0
    for word in words:
        if size + len(word) + 1 > limit and buffer:
            pieces.append(" ".join(buffer))
            buffer = []
            size = 0
        buffer.append(word)
        size += len(word) + 1
    if buffer:
        pieces.append(" ".join(buffer))
    return pieces


def chunk_blocks(
    blocks: list[PageBlock],
    *,
    target_chars: int = 2000,
    overlap_ratio: float = 0.15,
) -> tuple[list[Chunk], list[EvidenceSpan]]:
    chunks: list[Chunk] = []
    spans: list[EvidenceSpan] = []
    buffer: list[_Piece] = []
    overlap_chars = int(target_chars * overlap_ratio)

    def joined_size(pieces: list[_Piece]) -> int:
        return sum(len(piece.text) for piece in pieces) + max(0, len(pieces) - 1) * 2

    def common_section(pieces: list[_Piece]) -> tuple[str, ...]:
        prefix = list(pieces[0].section_path)
        for piece in pieces[1:]:
            matched = 0
            for value, candidate in zip(prefix, piece.section_path, strict=False):
                if value != candidate:
                    break
                matched += 1
            prefix = prefix[:matched]
            if not prefix:
                break
        return tuple(prefix) if prefix else ("Document",)

    def overlap_tail(pieces: list[_Piece]) -> list[_Piece]:
        if overlap_chars <= 0:
            return []
        selected: list[_Piece] = []
        size = 0
        for piece in reversed(pieces):
            separator = 2 if selected else 0
            if size + separator + len(piece.text) <= overlap_chars:
                selected.append(piece)
                size += separator + len(piece.text)
                continue
            if not selected:
                tail = piece.text[-overlap_chars:]
                boundary = tail.find(" ")
                if 0 <= boundary < len(tail) - 1:
                    tail = tail[boundary + 1 :]
                selected.append(
                    _Piece(
                        text=tail,
                        page=piece.page,
                        locator_kind=piece.locator_kind,
                        locator_label=piece.locator_label,
                        section_path=piece.section_path,
                        block_ordinal=piece.block_ordinal,
                    )
                )
            break
        selected.reverse()
        return selected

    def append_chunk(pieces: list[_Piece]) -> None:
        text = "\n\n".join(piece.text for piece in pieces)
        chunk_index = len(chunks)
        block_ordinals = tuple(dict.fromkeys(piece.block_ordinal for piece in pieces))
        chunks.append(
            Chunk(
                text=text,
                page_start=min(piece.page for piece in pieces),
                page_end=max(piece.page for piece in pieces),
                chunk_index=chunk_index,
                section_path=common_section(pieces),
                block_ordinals=block_ordinals,
            )
        )

        encoded = text.encode("utf-8")
        positioned: list[tuple[_Piece, int, int]] = []
        cursor = 0
        for index, piece in enumerate(pieces):
            if index:
                cursor += len(b"\n\n")
            start = cursor
            cursor += len(piece.text.encode("utf-8"))
            positioned.append((piece, start, cursor))

        group: list[tuple[_Piece, int, int]] = []

        def append_span() -> None:
            if not group:
                return
            first, start, _ = group[0]
            _, _, end = group[-1]
            span_text = encoded[start:end].decode("utf-8")
            spans.append(
                EvidenceSpan(
                    text=span_text,
                    page_number=first.page,
                    locator_kind=first.locator_kind,
                    locator_label=first.locator_label,
                    section_path=first.section_path,
                    chunk_index=chunk_index,
                    span_index=len(spans),
                    artifact_byte_start=start,
                    artifact_byte_end=end,
                    block_ordinals=tuple(
                        dict.fromkeys(item.block_ordinal for item, _, _ in group)
                    ),
                )
            )

        for positioned_piece in positioned:
            piece = positioned_piece[0]
            if group:
                previous = group[-1][0]
                if (
                    piece.page,
                    piece.locator_kind,
                    piece.locator_label,
                    piece.section_path,
                ) != (
                    previous.page,
                    previous.locator_kind,
                    previous.locator_label,
                    previous.section_path,
                ):
                    append_span()
                    group = []
            group.append(positioned_piece)
        append_span()

    def flush(carry_overlap: bool) -> None:
        nonlocal buffer
        if not buffer:
            return
        append_chunk(buffer)
        buffer = overlap_tail(buffer) if carry_overlap else []

    for block_ordinal, block in enumerate(blocks):
        locator_label = block.locator_label or str(block.page)
        if block.kind == "table":
            flush(carry_overlap=False)
            append_chunk(
                [
                    _Piece(
                        text=block.text,
                        page=block.page,
                        locator_kind=block.locator_kind,
                        locator_label=locator_label,
                        section_path=block.section_path,
                        block_ordinal=block_ordinal,
                    )
                ]
            )
            continue
        if (
            block.kind == "heading"
            and buffer
            and joined_size(buffer) >= target_chars // 2
        ):
            flush(carry_overlap=False)
        for text in _split_text(block.text, target_chars):
            piece = _Piece(
                text=text,
                page=block.page,
                locator_kind=block.locator_kind,
                locator_label=locator_label,
                section_path=block.section_path,
                block_ordinal=block_ordinal,
            )
            if buffer and joined_size(buffer) + len(text) + 2 > target_chars:
                flush(carry_overlap=True)
            buffer.append(piece)
    flush(carry_overlap=False)
    return chunks, spans


async def embed_batch(
    texts: list[str],
    dense_embedder: DenseEmbedder,
) -> tuple[list[list[float]], list[models.SparseVector]]:
    dense_vectors = await dense_embedder.embed(texts)
    sparse_vectors = await asyncio.to_thread(embed_sparse, texts)
    return dense_vectors, sparse_vectors


async def upsert_points(
    *,
    org_id: UUID,
    workspace_id: UUID,
    document_id: UUID,
    mime: str,
    created_at: datetime,
    chunks: list[Chunk],
    dense: list[list[float]],
    sparse: list[models.SparseVector],
) -> None:
    points = [
        models.PointStruct(
            id=str(uuid5(_CHUNK_NAMESPACE, f"{document_id}:{chunk.chunk_index}")),
            vector={"dense": dense_vector, "sparse": sparse_vector},
            payload={
                "tenant_id": str(org_id),
                "workspace_id": str(workspace_id),
                "document_id": str(document_id),
                "page": chunk.page,
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "doc_type": mime,
                "date": created_at.isoformat(),
                "acl_groups": [],
            },
        )
        for chunk, dense_vector, sparse_vector in zip(
            chunks,
            dense,
            sparse,
            strict=True,
        )
    ]
    await get_qdrant().upsert(COLLECTION, points=points, wait=True)
