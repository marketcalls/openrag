"""Pure ingestion pipeline stages shared by workers and integration tests."""

from __future__ import annotations

import asyncio
import math
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID, uuid5

import pypdfium2 as pdfium
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
from qdrant_client import models

from openrag.modules.documents.lifecycle import validate_section_path
from openrag.modules.retrieval.client import COLLECTION, get_qdrant
from openrag.modules.retrieval.embeddings import DenseEmbedder, embed_sparse

_CHUNK_NAMESPACE = UUID("6c7d9a52-3e1f-4b8a-9c0d-2f5e8b1a7d43")
_NATIVE_PDF_MIN_PAGES = 8
_NATIVE_PDF_MIN_PAGE_CHARS = 40
_NATIVE_PDF_MIN_AVERAGE_CHARS = 200


class IngestFailure(Exception):
    """Terminal ingestion failure caused by invalid or unsupported input."""


class _PdfImageObject(Protocol):
    def get_bounds(self) -> tuple[float, float, float, float]: ...


class _PdfPage(Protocol):
    def get_size(self) -> tuple[float, float]: ...

    def get_objects(self, *, filter: list[int]) -> Iterable[_PdfImageObject]: ...


@dataclass(frozen=True)
class ParseProfile:
    max_file_bytes: int = 100 * 1024 * 1024
    max_pages: int = 1000
    max_page_pixels: int = 40_000_000
    render_dpi: int = 200
    timeout_seconds: int = 300
    max_blocks: int = 100_000
    max_output_chars: int = 10_000_000
    ocr_mode: Literal["auto", "force", "disabled"] = "auto"
    ocr_languages: tuple[str, ...] = ("english",)
    ocr_min_confidence: float = 0.5
    ocr_text_score: float = 0.3
    ocr_bitmap_area_threshold: float = 0.05
    ocr_batch_size: int = 2

    def __post_init__(self) -> None:
        positive = (
            self.max_file_bytes,
            self.max_pages,
            self.max_page_pixels,
            self.render_dpi,
            self.timeout_seconds,
            self.max_blocks,
            self.max_output_chars,
            self.ocr_batch_size,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("parse profile limits must be positive")
        if not self.ocr_languages or any(
            not language
            or len(language) > 20
            or not language.replace("-", "").replace("_", "").isalnum()
            for language in self.ocr_languages
        ):
            raise ValueError("OCR languages are invalid")
        if not 0 <= self.ocr_min_confidence <= 1:
            raise ValueError("OCR confidence must be between zero and one")
        if not 0 <= self.ocr_text_score <= self.ocr_min_confidence:
            raise ValueError("OCR text score must not exceed the review threshold")
        if not 0 <= self.ocr_bitmap_area_threshold <= 1:
            raise ValueError("OCR bitmap threshold must be between zero and one")


@dataclass(frozen=True)
class ParsedDocument:
    blocks: list[PageBlock]
    page_count: int
    ocr_pages: tuple[int, ...] = ()
    low_confidence_ocr_pages: tuple[int, ...] = ()


@dataclass(frozen=True)
class PageBlock:
    page: int
    text: str
    kind: str
    section_path: tuple[str, ...] = ("Document",)
    locator_kind: str = "page"
    locator_label: str | None = None
    source_coordinates: dict[str, object] | None = None
    extraction_method: str = "parser"
    ocr_confidence: float | None = None

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
        if self.extraction_method not in {"parser", "ocr", "mixed"}:
            raise ValueError("extraction method is unsupported")
        if self.ocr_confidence is not None and not 0 <= self.ocr_confidence <= 1:
            raise ValueError("OCR confidence must be between zero and one")


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


def build_pdf_pipeline_options(profile: ParseProfile) -> PdfPipelineOptions:
    """Build one explicit, local-only Docling OCR profile."""

    return PdfPipelineOptions(
        document_timeout=float(profile.timeout_seconds),
        enable_remote_services=False,
        allow_external_plugins=False,
        do_ocr=profile.ocr_mode != "disabled",
        ocr_options=RapidOcrOptions(
            lang=list(profile.ocr_languages),
            force_full_page_ocr=profile.ocr_mode == "force",
            bitmap_area_threshold=profile.ocr_bitmap_area_threshold,
            text_score=profile.ocr_text_score,
            backend="onnxruntime",
            print_verbose=False,
        ),
        generate_page_images=False,
        generate_picture_images=False,
        generate_parsed_pages=True,
        ocr_batch_size=profile.ocr_batch_size,
    )


def _preflight_pdf(data: bytes, profile: ParseProfile) -> None:
    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:
        raise IngestFailure("PDF preflight failed") from exc
    try:
        if len(document) > profile.max_pages:
            raise IngestFailure("document exceeds page limit")
        for index in range(len(document)):
            page = document[index]
            try:
                width, height = page.get_size()
            finally:
                page.close()
            pixel_width = math.ceil(width * profile.render_dpi / 72)
            pixel_height = math.ceil(height * profile.render_dpi / 72)
            if pixel_width <= 0 or pixel_height <= 0:
                raise IngestFailure("PDF page dimensions are invalid")
            if pixel_width * pixel_height > profile.max_page_pixels:
                raise IngestFailure("PDF page exceeds rendered pixel limit")
    finally:
        document.close()


def _parse_large_text_pdf(
    data: bytes,
    profile: ParseProfile,
) -> ParsedDocument | None:
    """Fast-path long, fully text-native PDFs while preserving OCR for mixed scans."""

    if profile.ocr_mode != "auto":
        return None
    document = pdfium.PdfDocument(data)
    try:
        if len(document) < _NATIVE_PDF_MIN_PAGES:
            return None
        page_text: list[str] = []
        pages_requiring_ocr: list[bool] = []
        for page_number, page in enumerate(document, start=1):
            text_page = page.get_textpage()
            try:
                text = "\n".join(
                    line.strip()
                    for line in text_page.get_text_range().splitlines()
                    if line.strip()
                )
            finally:
                text_page.close()
            is_cover_page = page_number in {1, len(document)}
            pages_requiring_ocr.append(
                len(text) < _NATIVE_PDF_MIN_PAGE_CHARS
                and _page_has_material_bitmap(page, profile)
                and (not text or not is_cover_page)
            )
            page.close()
            page_text.append(text)
        if (
            not page_text
            or any(pages_requiring_ocr)
            or sum(map(len, page_text)) / len(page_text)
            < _NATIVE_PDF_MIN_AVERAGE_CHARS
        ):
            return None
        if sum(map(len, page_text)) > profile.max_output_chars:
            raise IngestFailure("document exceeds extracted text limit")
        blocks = [
            PageBlock(
                page=index,
                text=text,
                kind="text",
                section_path=("Document",),
                extraction_method="parser",
            )
            for index, text in enumerate(page_text, start=1)
            if text
        ]
        if len(blocks) > profile.max_blocks:
            raise IngestFailure("document exceeds block limit")
        return ParsedDocument(blocks=blocks, page_count=len(page_text))
    finally:
        document.close()


def _page_has_material_bitmap(page: _PdfPage, profile: ParseProfile) -> bool:
    """Conservatively detect low-text pages that still require OCR."""

    try:
        width, height = page.get_size()
        page_area = float(width) * float(height)
        if page_area <= 0:
            return True
        images = page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE])
        for image in images:
            left, bottom, right, top = image.get_bounds()
            visible_width = max(0.0, min(float(right), width) - max(float(left), 0.0))
            visible_height = max(0.0, min(float(top), height) - max(float(bottom), 0.0))
            if visible_width * visible_height / page_area >= profile.ocr_bitmap_area_threshold:
                return True
    except Exception:
        return True
    return False


def _source_coordinates(provenance: object | None) -> dict[str, object] | None:
    bbox = getattr(provenance, "bbox", None)
    if bbox is None:
        return None
    dump = getattr(bbox, "model_dump", None)
    if not callable(dump):
        return None
    raw = dump(mode="json", exclude_none=True)
    return dict(raw) if isinstance(raw, dict) else None


def _page_ocr_metadata(result: object) -> dict[int, tuple[str, float]]:
    metadata: dict[int, tuple[str, float]] = {}
    for page in getattr(result, "pages", []):
        parsed_page = getattr(page, "parsed_page", None)
        cells = getattr(parsed_page, "textline_cells", []) if parsed_page else []
        ocr_cells = [cell for cell in cells if bool(getattr(cell, "from_ocr", False))]
        if not ocr_cells:
            continue
        native_cells = [cell for cell in cells if not bool(getattr(cell, "from_ocr", False))]
        scores = [
            float(cell.confidence)
            for cell in ocr_cells
            if math.isfinite(float(getattr(cell, "confidence", math.nan)))
        ]
        confidence = sum(scores) / len(scores) if scores else 0.0
        metadata[int(page.page_no)] = (
            "mixed" if native_cells else "ocr",
            max(0.0, min(1.0, confidence)),
        )
    return metadata


def _section_heading(text: str) -> str:
    normalized = " ".join(text.split()).strip()
    return (normalized[:200].strip() or "Document")


def parse_document(
    data: bytes,
    filename: str,
    profile: ParseProfile | None = None,
) -> ParsedDocument:
    profile = profile or ParseProfile()
    if not data:
        raise IngestFailure("file is empty")
    if len(data) > profile.max_file_bytes:
        raise IngestFailure("file exceeds parser byte limit")
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".csv"}:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IngestFailure("text file is not valid UTF-8") from exc
        if len(text) > profile.max_output_chars:
            raise IngestFailure("document exceeds extracted text limit")
        text_blocks = [
            PageBlock(
                page=1,
                text=paragraph.strip(),
                kind="table" if suffix == ".csv" else "text",
            )
            for paragraph in text.split("\n\n")
            if paragraph.strip()
        ]
        if not text_blocks:
            raise IngestFailure("document contains no extractable text")
        if len(text_blocks) > profile.max_blocks:
            raise IngestFailure("document exceeds block limit")
        return ParsedDocument(blocks=text_blocks, page_count=1)

    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import (  # type: ignore[attr-defined]
        DocItemLabel,
        TableItem,
        TextItem,
    )

    heading_labels = {DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER}
    if suffix == ".pdf":
        _preflight_pdf(data, profile)
        native_pdf = _parse_large_text_pdf(data, profile)
        if native_pdf is not None:
            return native_pdf
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
        temporary.write(data)
        temporary_path = Path(temporary.name)
    try:
        try:
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=build_pdf_pipeline_options(profile)
                    )
                }
            )
            result = converter.convert(
                temporary_path,
                raises_on_error=True,
                max_num_pages=profile.max_pages,
                max_file_size=profile.max_file_bytes,
            )
        except Exception as exc:
            raise IngestFailure("unsupported or unparsable document") from exc
    finally:
        temporary_path.unlink(missing_ok=True)

    ocr_metadata = _page_ocr_metadata(result)
    locator_kind = "slide" if suffix == ".pptx" else "sheet" if suffix == ".xlsx" else "page"
    blocks: list[PageBlock] = []
    section_path: list[str] = ["Document"]
    total_chars = 0
    for item, level in result.document.iterate_items():
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
            normalized_text = text.strip()
            if kind == "heading":
                depth = max(1, min(8, int(level or 1)))
                section_path = section_path[: depth - 1] + [
                    _section_heading(normalized_text)
                ]
            extraction_method, confidence = ocr_metadata.get(
                page,
                ("parser", None),
            )
            blocks.append(
                PageBlock(
                    page=page,
                    text=normalized_text,
                    kind=kind,
                    section_path=tuple(section_path),
                    locator_kind=locator_kind,
                    locator_label=str(page),
                    source_coordinates=_source_coordinates(
                        provenance[0] if provenance else None
                    ),
                    extraction_method=extraction_method,
                    ocr_confidence=confidence,
                )
            )
            total_chars += len(normalized_text)
            if len(blocks) > profile.max_blocks:
                raise IngestFailure("document exceeds block limit")
            if total_chars > profile.max_output_chars:
                raise IngestFailure("document exceeds extracted text limit")
    if not blocks:
        raise IngestFailure("document contains no extractable text")
    pages = [int(page.page_no) for page in result.pages]
    page_count = max(pages) if pages else max(block.page for block in blocks)
    ocr_pages = tuple(sorted(ocr_metadata))
    low_confidence = tuple(
        page
        for page in ocr_pages
        if ocr_metadata[page][1] < profile.ocr_min_confidence
    )
    return ParsedDocument(
        blocks=blocks,
        page_count=page_count,
        ocr_pages=ocr_pages,
        low_confidence_ocr_pages=low_confidence,
    )


def parse_bytes(data: bytes, filename: str) -> list[PageBlock]:
    """Compatibility wrapper for callers that only consume normalized blocks."""

    return parse_document(data, filename).blocks


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
