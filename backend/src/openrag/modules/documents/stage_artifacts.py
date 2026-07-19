"""Canonical, identity-bound artifacts for durable document stages."""

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal
from uuid import UUID

from openrag.modules.documents.pipeline import (
    Chunk,
    EvidenceSpan,
    PageBlock,
    ParsedDocument,
)
from openrag.modules.documents.stages import StageCheckpoint, parse_stage_checkpoint

ArtifactKind = Literal["parsed", "chunks"]
_MAX_ARTIFACT_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    org_id: UUID
    workspace_id: UUID
    document_version_id: UUID
    checkpoint: StageCheckpoint


@dataclass(frozen=True, slots=True)
class StageArtifact:
    key: str
    digest: str
    data: bytes


def artifact_key(identity: ArtifactIdentity, kind: ArtifactKind) -> str:
    checkpoint = identity.checkpoint
    return (
        f"authority-artifacts/{identity.org_id.hex}/{identity.workspace_id.hex}/"
        f"{identity.document_version_id.hex}/{checkpoint.pipeline_kind}/"
        f"{checkpoint.pipeline_attempt}/{checkpoint.authority_generation_id.hex}/"
        f"{kind}.v1.json"
    )


def _identity_payload(identity: ArtifactIdentity) -> dict[str, object]:
    checkpoint = identity.checkpoint
    return {
        "org_id": str(identity.org_id),
        "workspace_id": str(identity.workspace_id),
        "document_version_id": str(identity.document_version_id),
        "checkpoint": checkpoint.for_stage(checkpoint.stage),
    }


def _decode_identity(value: object) -> ArtifactIdentity:
    if not isinstance(value, dict) or set(value) != {
        "org_id",
        "workspace_id",
        "document_version_id",
        "checkpoint",
    }:
        raise ValueError("artifact identity is invalid")
    try:
        checkpoint_value = value["checkpoint"]
        if not isinstance(checkpoint_value, str):
            raise ValueError
        return ArtifactIdentity(
            org_id=UUID(str(value["org_id"])),
            workspace_id=UUID(str(value["workspace_id"])),
            document_version_id=UUID(str(value["document_version_id"])),
            checkpoint=parse_stage_checkpoint(checkpoint_value),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("artifact identity is invalid") from exc


def _canonical(payload: dict[str, object]) -> bytes:
    try:
        data = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("artifact contains non-JSON data") from exc
    if len(data) > _MAX_ARTIFACT_BYTES:
        raise ValueError("artifact exceeds byte limit")
    return data


def _artifact(
    identity: ArtifactIdentity,
    kind: ArtifactKind,
    payload: dict[str, object],
) -> StageArtifact:
    data = _canonical(payload)
    return StageArtifact(
        key=artifact_key(identity, kind),
        digest=hashlib.sha256(data).hexdigest(),
        data=data,
    )


def _load(raw: bytes, *, schema: str, expected: ArtifactIdentity) -> dict[str, object]:
    if not raw or len(raw) > _MAX_ARTIFACT_BYTES:
        raise ValueError("artifact byte length is invalid")
    try:
        decoded: object = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("artifact JSON is invalid") from exc
    if not isinstance(decoded, dict) or decoded.get("schema") != schema:
        raise ValueError("artifact schema is unsupported")
    if _decode_identity(decoded.get("identity")) != expected:
        raise ValueError("artifact identity mismatch")
    return decoded


def _int(value: object, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"artifact {field} is invalid")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"artifact {field} is invalid")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError(f"artifact {field} is invalid")
    return tuple(value)


def _int_tuple(value: object, field: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise ValueError(f"artifact {field} is invalid")
    return tuple(_int(item, field) for item in value)


def _records(value: object, field: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, dict) for item in value
    ):
        raise ValueError(f"artifact {field} is invalid")
    return value


def _validate_pages(pages: tuple[int, ...], *, page_count: int, field: str) -> None:
    if pages != tuple(sorted(set(pages))) or any(
        page < 1 or page > page_count for page in pages
    ):
        raise ValueError(f"artifact {field} is invalid")


def _validate_parsed(
    blocks: list[PageBlock],
    page_count: int,
    ocr_pages: tuple[int, ...],
    low_confidence_ocr_pages: tuple[int, ...],
) -> None:
    if not blocks or page_count < 1 or max(block.page for block in blocks) > page_count:
        raise ValueError("artifact parsed pages are invalid")
    _validate_pages(ocr_pages, page_count=page_count, field="OCR pages")
    _validate_pages(
        low_confidence_ocr_pages,
        page_count=page_count,
        field="low-confidence OCR pages",
    )
    if not set(low_confidence_ocr_pages).issubset(ocr_pages):
        raise ValueError("artifact OCR page sets conflict")


def encode_parsed_artifact(
    identity: ArtifactIdentity,
    *,
    blocks: list[PageBlock],
    page_count: int,
    ocr_pages: tuple[int, ...] = (),
    low_confidence_ocr_pages: tuple[int, ...] = (),
) -> StageArtifact:
    if identity.checkpoint.stage != "parse":
        raise ValueError("parsed artifact requires parse checkpoint")
    _validate_parsed(blocks, page_count, ocr_pages, low_confidence_ocr_pages)
    return _artifact(
        identity,
        "parsed",
        {
            "schema": "openrag.parsed.v1",
            "identity": _identity_payload(identity),
            "page_count": page_count,
            "ocr_pages": ocr_pages,
            "low_confidence_ocr_pages": low_confidence_ocr_pages,
            "blocks": [asdict(block) for block in blocks],
        },
    )


def _decode_block(item: dict[str, object]) -> PageBlock:
    coordinates = item.get("source_coordinates")
    if coordinates is not None and not isinstance(coordinates, dict):
        raise ValueError("artifact block coordinates are invalid")
    confidence = item.get("ocr_confidence")
    if confidence is not None and (
        isinstance(confidence, bool) or not isinstance(confidence, int | float)
    ):
        raise ValueError("artifact block OCR confidence is invalid")
    return PageBlock(
        page=_int(item.get("page"), "block page", minimum=1),
        text=_string(item.get("text"), "block text"),
        kind=_string(item.get("kind"), "block kind"),
        section_path=_string_tuple(item.get("section_path"), "block section"),
        locator_kind=_string(item.get("locator_kind"), "block locator kind"),
        locator_label=_string(item.get("locator_label"), "block locator label"),
        source_coordinates=coordinates,
        extraction_method=_string(
            item.get("extraction_method"), "block extraction method"
        ),
        ocr_confidence=float(confidence) if confidence is not None else None,
    )


def decode_parsed_artifact(raw: bytes, *, expected: ArtifactIdentity) -> ParsedDocument:
    decoded = _load(raw, schema="openrag.parsed.v1", expected=expected)
    blocks = [_decode_block(item) for item in _records(decoded.get("blocks"), "blocks")]
    page_count = _int(decoded.get("page_count"), "page count", minimum=1)
    ocr_pages = _int_tuple(decoded.get("ocr_pages"), "OCR pages")
    low_confidence = _int_tuple(
        decoded.get("low_confidence_ocr_pages"),
        "low-confidence OCR pages",
    )
    _validate_parsed(blocks, page_count, ocr_pages, low_confidence)
    return ParsedDocument(
        blocks=blocks,
        page_count=page_count,
        ocr_pages=ocr_pages,
        low_confidence_ocr_pages=low_confidence,
    )


def _validate_chunk_output(
    chunks: list[Chunk],
    evidence_spans: list[EvidenceSpan],
) -> None:
    if not chunks or not evidence_spans:
        raise ValueError("artifact chunk output is empty")
    if [chunk.chunk_index for chunk in chunks] != list(range(len(chunks))):
        raise ValueError("artifact chunk indices are not contiguous")
    if [span.span_index for span in evidence_spans] != list(range(len(evidence_spans))):
        raise ValueError("artifact evidence indices are not contiguous")
    covered: set[int] = set()
    for chunk in chunks:
        if (
            not chunk.text
            or chunk.page_start < 1
            or chunk.page_end < chunk.page_start
            or not chunk.block_ordinals
            or len(set(chunk.block_ordinals)) != len(chunk.block_ordinals)
        ):
            raise ValueError("artifact chunk contract is invalid")
    for span in evidence_spans:
        if span.chunk_index < 0 or span.chunk_index >= len(chunks):
            raise ValueError("artifact evidence chunk is invalid")
        chunk = chunks[span.chunk_index]
        encoded = chunk.text.encode("utf-8")
        if (
            span.artifact_byte_start < 0
            or span.artifact_byte_end <= span.artifact_byte_start
            or span.artifact_byte_end > len(encoded)
            or encoded[span.artifact_byte_start : span.artifact_byte_end]
            != span.text.encode("utf-8")
            or not span.block_ordinals
            or not set(span.block_ordinals).issubset(chunk.block_ordinals)
        ):
            raise ValueError("artifact evidence range is invalid")
        covered.add(span.chunk_index)
    if covered != set(range(len(chunks))):
        raise ValueError("artifact evidence coverage is incomplete")


def encode_chunk_artifact(
    identity: ArtifactIdentity,
    *,
    chunks: list[Chunk],
    evidence_spans: list[EvidenceSpan],
) -> StageArtifact:
    if identity.checkpoint.stage != "chunk":
        raise ValueError("chunk artifact requires chunk checkpoint")
    _validate_chunk_output(chunks, evidence_spans)
    return _artifact(
        identity,
        "chunks",
        {
            "schema": "openrag.chunks.v1",
            "identity": _identity_payload(identity),
            "chunks": [asdict(chunk) for chunk in chunks],
            "evidence_spans": [asdict(span) for span in evidence_spans],
        },
    )


def _decode_chunk(item: dict[str, object]) -> Chunk:
    return Chunk(
        text=_string(item.get("text"), "chunk text"),
        page_start=_int(item.get("page_start"), "chunk page start", minimum=1),
        page_end=_int(item.get("page_end"), "chunk page end", minimum=1),
        chunk_index=_int(item.get("chunk_index"), "chunk index"),
        section_path=_string_tuple(item.get("section_path"), "chunk section"),
        block_ordinals=_int_tuple(item.get("block_ordinals"), "chunk blocks"),
    )


def _decode_span(item: dict[str, object]) -> EvidenceSpan:
    return EvidenceSpan(
        text=_string(item.get("text"), "evidence text"),
        page_number=_int(item.get("page_number"), "evidence page", minimum=1),
        locator_kind=_string(item.get("locator_kind"), "evidence locator kind"),
        locator_label=_string(item.get("locator_label"), "evidence locator label"),
        section_path=_string_tuple(item.get("section_path"), "evidence section"),
        chunk_index=_int(item.get("chunk_index"), "evidence chunk index"),
        span_index=_int(item.get("span_index"), "evidence span index"),
        artifact_byte_start=_int(
            item.get("artifact_byte_start"), "evidence byte start"
        ),
        artifact_byte_end=_int(
            item.get("artifact_byte_end"), "evidence byte end", minimum=1
        ),
        block_ordinals=_int_tuple(item.get("block_ordinals"), "evidence blocks"),
    )


def decode_chunk_artifact(
    raw: bytes,
    *,
    expected: ArtifactIdentity,
) -> tuple[list[Chunk], list[EvidenceSpan]]:
    decoded = _load(raw, schema="openrag.chunks.v1", expected=expected)
    chunks = [_decode_chunk(item) for item in _records(decoded.get("chunks"), "chunks")]
    spans = [
        _decode_span(item)
        for item in _records(decoded.get("evidence_spans"), "evidence spans")
    ]
    _validate_chunk_output(chunks, spans)
    return chunks, spans
