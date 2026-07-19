"""External-I/O adapters for replay-safe document parse and chunk stages."""

import asyncio
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

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
    StageArtifact,
    artifact_key,
    decode_parsed_artifact,
    encode_chunk_artifact,
    encode_parsed_artifact,
)
from openrag.modules.documents.stages import (
    StageCheckpoint,
    StageClaim,
    parse_stage_checkpoint,
)


class StageObjectStorage(Protocol):
    async def get(self, key: str) -> bytes: ...

    async def put(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class StageSourcePlan:
    org_id: UUID
    workspace_id: UUID
    document_version_id: UUID
    source_storage_key: str
    source_filename: str
    source_mime: str

    def __post_init__(self) -> None:
        bounded = (
            (self.source_storage_key, 1024),
            (self.source_filename, 500),
            (self.source_mime, 255),
        )
        if any(
            not value
            or len(value) > limit
            or value != value.strip()
            or any(ord(character) < 32 for character in value)
            for value, limit in bounded
        ):
            raise ValueError("stage source plan is invalid")


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
