"""Pure ingestion pipeline stages shared by workers and integration tests."""

import asyncio
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

from qdrant_client import models

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


@dataclass(frozen=True)
class Chunk:
    text: str
    page: int
    chunk_index: int


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
) -> list[Chunk]:
    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_page: int | None = None
    overlap_chars = int(target_chars * overlap_ratio)

    def flush(carry_overlap: bool) -> None:
        nonlocal buffer, buffer_page
        if not buffer:
            return
        text = "\n\n".join(buffer)
        chunks.append(
            Chunk(
                text=text,
                page=buffer_page or 1,
                chunk_index=len(chunks),
            )
        )
        if carry_overlap and overlap_chars > 0:
            tail = text[-overlap_chars:]
            cut = tail.find(" ")
            buffer = [
                tail[cut + 1 :] if 0 <= cut < len(tail) - 1 else tail
            ]
        else:
            buffer = []
            buffer_page = None

    for block in blocks:
        if block.kind == "table":
            flush(carry_overlap=False)
            chunks.append(
                Chunk(
                    text=block.text,
                    page=block.page,
                    chunk_index=len(chunks),
                )
            )
            continue
        if (
            block.kind == "heading"
            and buffer
            and len("\n\n".join(buffer)) >= target_chars // 2
        ):
            flush(carry_overlap=False)
        for piece in _split_text(block.text, target_chars):
            if (
                buffer
                and len("\n\n".join(buffer)) + len(piece) > target_chars
            ):
                flush(carry_overlap=True)
            if buffer_page is None:
                buffer_page = block.page
            buffer.append(piece)
    flush(carry_overlap=False)
    return chunks


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
