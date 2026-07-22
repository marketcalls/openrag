"""External-I/O adapters for replay-safe document parse and chunk stages."""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from qdrant_client import models

from openrag.modules.documents.authority_storage import AuthorityCollectionSpec
from openrag.modules.documents.pipeline import (
    Chunk,
    EvidenceSpan,
    IngestFailure,
    ParsedDocument,
    ParseProfile,
    chunk_blocks,
    parse_document,
)
from openrag.modules.documents.stage_artifacts import (
    ArtifactIdentity,
    EvidenceVector,
    StageArtifact,
    artifact_key,
    decode_chunk_artifact,
    decode_parsed_artifact,
    decode_vector_artifact,
    encode_chunk_artifact,
    encode_parsed_artifact,
    encode_vector_artifact,
)
from openrag.modules.documents.stages import (
    StageCheckpoint,
    StageClaim,
    parse_stage_checkpoint,
)
from openrag.modules.retrieval.embeddings import DenseEmbedder, embed_sparse

SparseEmbedder = Callable[[list[str]], Awaitable[list[models.SparseVector]]]
AuthorityReady = Callable[[UUID], Awaitable[bool]]


class StageObjectStorage(Protocol):
    async def get(self, key: str) -> bytes: ...

    async def put(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None: ...


class AuthorityPointWriter(Protocol):
    async def upsert(
        self,
        collection_name: str,
        *,
        points: list[models.PointStruct],
        wait: bool,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class StageSourcePlan:
    org_id: UUID
    workspace_id: UUID
    document_version_id: UUID
    source_storage_key: str
    source_filename: str
    source_mime: str
    embedding_profile_version: str
    dense_dimension: int

    def __post_init__(self) -> None:
        bounded = (
            (self.source_storage_key, 1024),
            (self.source_filename, 500),
            (self.source_mime, 255),
            (self.embedding_profile_version, 100),
        )
        if any(
            not value
            or len(value) > limit
            or value != value.strip()
            or any(ord(character) < 32 for character in value)
            for value, limit in bounded
        ):
            raise ValueError("stage source plan is invalid")
        if self.dense_dimension < 1:
            raise ValueError("stage dense dimension is invalid")


@dataclass(frozen=True, slots=True)
class ParsedStageResult:
    identity: ArtifactIdentity
    artifact: StageArtifact
    parsed: ParsedDocument


@dataclass(frozen=True, slots=True)
class ChunkStageResult:
    identity: ArtifactIdentity
    artifact: StageArtifact
    parsed: ParsedDocument
    chunks: list[Chunk]
    evidence_spans: list[EvidenceSpan]


@dataclass(frozen=True, slots=True)
class EmbeddedStageResult:
    identity: ArtifactIdentity
    artifact: StageArtifact
    vectors: list[EvidenceVector]


@dataclass(frozen=True, slots=True)
class PersistedEvidence:
    id: UUID
    chunk_id: UUID
    ordinal: int
    page_number: int
    locator_kind: str
    locator_label: str
    section_path: tuple[str, ...]
    content_hash: str
    ocr_status: str
    content_type: str


@dataclass(frozen=True, slots=True)
class AuthorityPlan:
    source: StageSourcePlan
    document_id: UUID
    document_name: str
    version_label: str
    revision_date: datetime | None
    projection_revision: int
    evidence: list[PersistedEvidence]
    document_checksum: str

    def __post_init__(self) -> None:
        if (
            not 1 <= len(self.document_name.strip()) <= 500
            or not 1 <= len(self.version_label.strip()) <= 200
            or self.projection_revision < 0
            or not self.evidence
            or [row.ordinal for row in self.evidence] != list(range(len(self.evidence)))
            or len(self.document_checksum) != 64
            or any(
                row.ocr_status not in {"not_required", "ocr", "mixed"}
                or row.content_type not in {"text", "table", "mixed"}
                for row in self.evidence
            )
        ):
            raise ValueError("authority plan is invalid")


@dataclass(frozen=True, slots=True)
class AuthorityStageResult:
    physical_collection: str
    output_digest: str
    point_count: int


class AuthorityStorageUnavailable(RuntimeError):
    """The exact authority generation failed its immediate readiness probe."""


def _identity(
    claim: StageClaim,
    plan: StageSourcePlan,
    *,
    expected_stage: str,
    artifact_stage: str | None = None,
) -> ArtifactIdentity:
    if (
        claim.org_id != plan.org_id
        or claim.workspace_id != plan.workspace_id
        or claim.document_version_id != plan.document_version_id
        or claim.stage != expected_stage
    ):
        raise IngestFailure("stage source identity mismatch")
    try:
        checkpoint = parse_stage_checkpoint(claim.checkpoint)
    except ValueError as exc:
        raise IngestFailure("stage checkpoint is invalid") from exc
    if (
        checkpoint.stage != expected_stage
        or checkpoint.pipeline_kind != claim.pipeline_kind
        or checkpoint.authority_generation_id != claim.authority_generation_id
    ):
        raise IngestFailure("stage checkpoint identity mismatch")
    selected_stage = artifact_stage or expected_stage
    return ArtifactIdentity(
        org_id=plan.org_id,
        workspace_id=plan.workspace_id,
        document_version_id=plan.document_version_id,
        checkpoint=StageCheckpoint(
            stage=selected_stage,
            pipeline_kind=checkpoint.pipeline_kind,
            pipeline_attempt=checkpoint.pipeline_attempt,
            authority_generation_id=checkpoint.authority_generation_id,
        ),
    )


async def parse_stage_external(
    claim: StageClaim,
    plan: StageSourcePlan,
    storage: StageObjectStorage,
    profile: ParseProfile,
) -> ParsedStageResult:
    """Parse one immutable source and write only a content-addressed artifact."""

    identity = _identity(claim, plan, expected_stage="parse")
    source = await storage.get(plan.source_storage_key)
    parsed = await asyncio.to_thread(
        parse_document,
        source,
        plan.source_filename,
        profile,
    )
    try:
        artifact = encode_parsed_artifact(
            identity,
            blocks=parsed.blocks,
            page_count=parsed.page_count,
            ocr_pages=parsed.ocr_pages,
            low_confidence_ocr_pages=parsed.low_confidence_ocr_pages,
        )
    except ValueError as exc:
        raise IngestFailure("parsed artifact is invalid") from exc
    await storage.put(artifact.key, artifact.data, content_type="application/json")
    return ParsedStageResult(identity=identity, artifact=artifact, parsed=parsed)


async def chunk_stage_external(
    claim: StageClaim,
    plan: StageSourcePlan,
    storage: StageObjectStorage,
    *,
    parsed_digest: str,
) -> ChunkStageResult:
    """Consume the fenced parse digest and write page-local chunk evidence."""

    identity = _identity(claim, plan, expected_stage="chunk")
    parsed_identity = _identity(
        claim,
        plan,
        expected_stage="chunk",
        artifact_stage="parse",
    )
    try:
        parsed_key = artifact_key(parsed_identity, "parsed", parsed_digest)
        parsed_raw = await storage.get(parsed_key)
        parsed = decode_parsed_artifact(
            parsed_raw,
            expected=parsed_identity,
            expected_digest=parsed_digest,
        )
    except (KeyError, ValueError) as exc:
        raise IngestFailure("parsed artifact is invalid") from exc
    chunks, evidence_spans = chunk_blocks(parsed.blocks)
    if not chunks or not evidence_spans:
        raise IngestFailure("chunking produced no page-local evidence")
    try:
        artifact = encode_chunk_artifact(
            identity,
            chunks=chunks,
            evidence_spans=evidence_spans,
        )
    except ValueError as exc:
        raise IngestFailure("chunk artifact is invalid") from exc
    await storage.put(artifact.key, artifact.data, content_type="application/json")
    return ChunkStageResult(
        identity=identity,
        artifact=artifact,
        parsed=parsed,
        chunks=chunks,
        evidence_spans=evidence_spans,
    )


async def _default_sparse_embedder(texts: list[str]) -> list[models.SparseVector]:
    return await asyncio.to_thread(embed_sparse, texts)


async def embed_stage_external(
    claim: StageClaim,
    plan: StageSourcePlan,
    storage: StageObjectStorage,
    *,
    chunks_digest: str,
    dense_embedder: DenseEmbedder,
    sparse_embedder: SparseEmbedder = _default_sparse_embedder,
    batch_size: int = 32,
) -> EmbeddedStageResult:
    """Embed exact evidence spans and persist profile-bound vector output."""

    if not 1 <= batch_size <= 256:
        raise ValueError("embedding batch size is invalid")
    identity = _identity(claim, plan, expected_stage="embed")
    chunk_identity = _identity(
        claim,
        plan,
        expected_stage="embed",
        artifact_stage="chunk",
    )
    try:
        chunks_key = artifact_key(chunk_identity, "chunks", chunks_digest)
        chunks_raw = await storage.get(chunks_key)
        _chunks, evidence_spans = decode_chunk_artifact(
            chunks_raw,
            expected=chunk_identity,
            expected_digest=chunks_digest,
        )
    except (KeyError, ValueError) as exc:
        raise IngestFailure("chunk artifact is invalid") from exc

    vectors: list[EvidenceVector] = []
    for start in range(0, len(evidence_spans), batch_size):
        batch = evidence_spans[start : start + batch_size]
        texts = [span.text for span in batch]
        dense_batch, sparse_batch = await asyncio.gather(
            dense_embedder.embed(texts),
            sparse_embedder(texts),
        )
        if len(dense_batch) != len(batch) or len(sparse_batch) != len(batch):
            raise IngestFailure("embedding provider cardinality mismatch")
        for span, dense, sparse in zip(
            batch,
            dense_batch,
            sparse_batch,
            strict=True,
        ):
            if len(dense) != plan.dense_dimension:
                raise IngestFailure("embedding provider dimension mismatch")
            vectors.append(
                EvidenceVector(
                    span_index=span.span_index,
                    dense=tuple(float(value) for value in dense),
                    sparse_indices=tuple(int(value) for value in sparse.indices),
                    sparse_values=tuple(float(value) for value in sparse.values),
                )
            )
    try:
        artifact = encode_vector_artifact(
            identity,
            parent_digest=chunks_digest,
            embedding_profile_version=plan.embedding_profile_version,
            dense_dimension=plan.dense_dimension,
            vectors=vectors,
        )
    except ValueError as exc:
        raise IngestFailure("vector artifact is invalid") from exc
    await storage.put(artifact.key, artifact.data, content_type="application/json")
    return EmbeddedStageResult(
        identity=identity,
        artifact=artifact,
        vectors=vectors,
    )


def _validate_authority_evidence(
    evidence: list[PersistedEvidence],
    spans: list[EvidenceSpan],
) -> None:
    if len(evidence) != len(spans):
        raise IngestFailure("persisted evidence cardinality mismatch")
    for row, span in zip(evidence, spans, strict=True):
        if (
            row.ordinal != span.span_index
            or row.page_number != span.page_number
            or row.locator_kind != span.locator_kind
            or row.locator_label != span.locator_label
            or row.section_path != span.section_path
            or row.content_hash != hashlib.sha256(span.text.encode()).hexdigest()
        ):
            raise IngestFailure("persisted evidence identity mismatch")


async def authority_upsert_external(
    claim: StageClaim,
    plan: AuthorityPlan,
    storage: StageObjectStorage,
    *,
    chunks_digest: str,
    vectors_digest: str,
    authority_ready: AuthorityReady,
    qdrant: AuthorityPointWriter,
    batch_size: int = 128,
) -> AuthorityStageResult:
    """Upsert exact evidence IDs only into the probed physical generation."""

    if not 1 <= batch_size <= 256:
        raise ValueError("authority batch size is invalid")
    _identity(
        claim,
        plan.source,
        expected_stage="authority_upsert",
    )
    chunk_identity = _identity(
        claim,
        plan.source,
        expected_stage="authority_upsert",
        artifact_stage="chunk",
    )
    vector_identity = _identity(
        claim,
        plan.source,
        expected_stage="authority_upsert",
        artifact_stage="embed",
    )
    try:
        chunk_raw = await storage.get(
            artifact_key(chunk_identity, "chunks", chunks_digest)
        )
        _chunks, spans = decode_chunk_artifact(
            chunk_raw,
            expected=chunk_identity,
            expected_digest=chunks_digest,
        )
        vector_raw = await storage.get(
            artifact_key(vector_identity, "vectors", vectors_digest)
        )
        vector_artifact = decode_vector_artifact(
            vector_raw,
            expected=vector_identity,
            expected_digest=vectors_digest,
            expected_parent_digest=chunks_digest,
            expected_embedding_profile=plan.source.embedding_profile_version,
            expected_dense_dimension=plan.source.dense_dimension,
        )
    except (KeyError, ValueError) as exc:
        raise IngestFailure("authority input artifact is invalid") from exc
    _validate_authority_evidence(plan.evidence, spans)
    if len(vector_artifact.vectors) != len(spans):
        raise IngestFailure("authority vector cardinality mismatch")

    spec = AuthorityCollectionSpec(
        generation_id=claim.authority_generation_id,
        dense_dimension=plan.source.dense_dimension,
    )
    points = [
        models.PointStruct(
            id=str(row.id),
            vector={
                "dense": list(vector.dense),
                "sparse": models.SparseVector(
                    indices=list(vector.sparse_indices),
                    values=list(vector.sparse_values),
                ),
            },
            payload={
                "tenant_id": str(plan.source.org_id),
                "workspace_id": str(plan.source.workspace_id),
                "document_id": str(plan.document_id),
                "document_version_id": str(plan.source.document_version_id),
                "evidence_span_id": str(row.id),
                "chunk_id": str(row.chunk_id),
                "is_current_approved": claim.pipeline_kind == "reindex",
                "projection_revision": plan.projection_revision,
                "page_number": row.page_number,
                "page": row.page_number,
                "chunk_index": row.ordinal,
                "document_name": plan.document_name,
                "filename": plan.source.source_filename,
                "file_type": plan.source.source_mime,
                "document_checksum": plan.document_checksum,
                "version_label": plan.version_label,
                "revision_date": (
                    plan.revision_date.isoformat()
                    if plan.revision_date is not None
                    else None
                ),
                "section_path": list(row.section_path),
                "section": " > ".join(row.section_path),
                "locator_kind": row.locator_kind,
                "locator_label": row.locator_label,
                "content_hash": row.content_hash,
                "ocr_status": row.ocr_status,
                "content_type": row.content_type,
                "text": span.text,
                "source_mime": plan.source.source_mime,
            },
        )
        for row, span, vector in zip(
            plan.evidence,
            spans,
            vector_artifact.vectors,
            strict=True,
        )
    ]
    for start in range(0, len(points), batch_size):
        if not await authority_ready(claim.authority_generation_id):
            raise AuthorityStorageUnavailable("authority storage is not ready")
        await qdrant.upsert(
            spec.physical_collection,
            points=points[start : start + batch_size],
            wait=True,
        )
    receipt = (
        f"openrag.authority.v1:{spec.physical_collection}:"
        f"{vectors_digest}:{len(points)}"
    ).encode()
    return AuthorityStageResult(
        physical_collection=spec.physical_collection,
        output_digest=hashlib.sha256(receipt).hexdigest(),
        point_count=len(points),
    )
