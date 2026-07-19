import hashlib
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from qdrant_client import models

from openrag.modules.documents.pipeline import IngestFailure, ParseProfile
from openrag.modules.documents.stage_adapters import (
    AuthorityPlan,
    AuthorityStorageUnavailable,
    ChunkStageResult,
    EmbeddedStageResult,
    PersistedEvidence,
    StageSourcePlan,
    authority_upsert_external,
    chunk_stage_external,
    embed_stage_external,
    parse_stage_external,
)
from openrag.modules.documents.stage_artifacts import (
    artifact_key,
    decode_chunk_artifact,
    decode_parsed_artifact,
    decode_vector_artifact,
)
from openrag.modules.documents.stages import StageClaim


class MemoryStorage:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)
        self.puts: list[tuple[str, str]] = []

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def put(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        self.objects[key] = data
        self.puts.append((key, content_type))


def _claim(
    plan: StageSourcePlan,
    stage: str,
    *,
    generation_id: UUID | None = None,
) -> StageClaim:
    selected_generation = generation_id or uuid4()
    return StageClaim(
        attempt_id=uuid4(),
        org_id=plan.org_id,
        workspace_id=plan.workspace_id,
        document_version_id=plan.document_version_id,
        pipeline_kind="ingestion",
        stage=stage,
        checkpoint=f"{stage}:ingestion:1:{selected_generation.hex}",
        authority_generation_id=selected_generation,
        owner="test-worker",
        lease_token=uuid4(),
        lease_expires_at=datetime(2026, 7, 20, 4, 30),
        attempt_number=1,
    )


def _plan() -> StageSourcePlan:
    return StageSourcePlan(
        org_id=uuid4(),
        workspace_id=uuid4(),
        document_version_id=uuid4(),
        source_storage_key="immutable/source.txt",
        source_filename="source.txt",
        source_mime="text/plain",
        embedding_profile_version="test-embedding/v1",
        dense_dimension=3,
    )


async def test_parse_and_chunk_write_only_content_addressed_validated_artifacts() -> None:
    plan = _plan()
    storage = MemoryStorage(
        {
            plan.source_storage_key: (
                b"Safety policy section one.\n\nInvoice total is INR 4200."
            )
        }
    )
    parse_claim = _claim(plan, "parse")

    parsed = await parse_stage_external(
        parse_claim,
        plan,
        storage,
        ParseProfile(ocr_mode="disabled"),
    )

    assert parsed.artifact.key == artifact_key(
        parsed.identity,
        "parsed",
        parsed.artifact.digest,
    )
    decoded = decode_parsed_artifact(
        storage.objects[parsed.artifact.key],
        expected=parsed.identity,
        expected_digest=parsed.artifact.digest,
    )
    assert decoded.page_count == 1
    assert len(decoded.blocks) == 2

    chunk_claim = _claim(
        plan,
        "chunk",
        generation_id=parse_claim.authority_generation_id,
    )
    chunked = await chunk_stage_external(
        chunk_claim,
        plan,
        storage,
        parsed_digest=parsed.artifact.digest,
    )

    chunks, spans = decode_chunk_artifact(
        storage.objects[chunked.artifact.key],
        expected=chunked.identity,
        expected_digest=chunked.artifact.digest,
    )
    assert chunks == chunked.chunks
    assert spans == chunked.evidence_spans
    assert {content_type for _key, content_type in storage.puts} == {
        "application/json"
    }
    assert len(storage.puts) == 2


async def test_stage_adapter_rejects_cross_wired_claim_before_object_io() -> None:
    plan = _plan()
    claim = _claim(plan, "parse")
    storage = MemoryStorage({plan.source_storage_key: b"valid text"})
    foreign = StageSourcePlan(
        org_id=uuid4(),
        workspace_id=plan.workspace_id,
        document_version_id=plan.document_version_id,
        source_storage_key=plan.source_storage_key,
        source_filename=plan.source_filename,
        source_mime=plan.source_mime,
        embedding_profile_version=plan.embedding_profile_version,
        dense_dimension=plan.dense_dimension,
    )

    with pytest.raises(IngestFailure, match="identity"):
        await parse_stage_external(claim, foreign, storage, ParseProfile())

    assert storage.puts == []


async def test_chunk_stage_rejects_corrupted_parent_digest() -> None:
    plan = _plan()
    storage = MemoryStorage({plan.source_storage_key: b"valid text"})
    parse_claim = _claim(plan, "parse")
    parsed = await parse_stage_external(parse_claim, plan, storage, ParseProfile())
    storage.objects[parsed.artifact.key] += b"tampered"
    chunk_claim = _claim(
        plan,
        "chunk",
        generation_id=parse_claim.authority_generation_id,
    )

    with pytest.raises(IngestFailure, match="parsed artifact is invalid"):
        await chunk_stage_external(
            chunk_claim,
            plan,
            storage,
            parsed_digest=parsed.artifact.digest,
        )


class RecordingDenseEmbedder:
    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.texts: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [
            [float(index + 1) / self.dimension for index in range(self.dimension)]
            for _text in texts
        ]


async def _sparse(texts: list[str]) -> list[models.SparseVector]:
    return [models.SparseVector(indices=[1, 3], values=[0.2, 0.8]) for _ in texts]


async def test_embed_stage_vectors_page_local_evidence_with_profile_lineage() -> None:
    plan = _plan()
    storage = MemoryStorage({plan.source_storage_key: b"First page fact.\n\nSecond fact."})
    generation_id = uuid4()
    parsed = await parse_stage_external(
        _claim(plan, "parse", generation_id=generation_id),
        plan,
        storage,
        ParseProfile(),
    )
    chunked = await chunk_stage_external(
        _claim(plan, "chunk", generation_id=generation_id),
        plan,
        storage,
        parsed_digest=parsed.artifact.digest,
    )
    dense = RecordingDenseEmbedder(plan.dense_dimension)

    embedded = await embed_stage_external(
        _claim(plan, "embed", generation_id=generation_id),
        plan,
        storage,
        chunks_digest=chunked.artifact.digest,
        dense_embedder=dense,
        sparse_embedder=_sparse,
        batch_size=1,
    )

    assert dense.texts == [span.text for span in chunked.evidence_spans]
    decoded = decode_vector_artifact(
        storage.objects[embedded.artifact.key],
        expected=embedded.identity,
        expected_digest=embedded.artifact.digest,
        expected_parent_digest=chunked.artifact.digest,
        expected_embedding_profile=plan.embedding_profile_version,
        expected_dense_dimension=plan.dense_dimension,
    )
    assert len(decoded.vectors) == len(chunked.evidence_spans)
    assert [vector.span_index for vector in decoded.vectors] == list(
        range(len(decoded.vectors))
    )


async def test_embed_stage_rejects_provider_dimension_drift_before_write() -> None:
    plan = _plan()
    storage = MemoryStorage({plan.source_storage_key: b"Grounded fact."})
    generation_id = uuid4()
    parsed = await parse_stage_external(
        _claim(plan, "parse", generation_id=generation_id),
        plan,
        storage,
        ParseProfile(),
    )
    chunked = await chunk_stage_external(
        _claim(plan, "chunk", generation_id=generation_id),
        plan,
        storage,
        parsed_digest=parsed.artifact.digest,
    )
    prior_puts = len(storage.puts)

    with pytest.raises(IngestFailure, match="dimension"):
        await embed_stage_external(
            _claim(plan, "embed", generation_id=generation_id),
            plan,
            storage,
            chunks_digest=chunked.artifact.digest,
            dense_embedder=RecordingDenseEmbedder(2),
            sparse_embedder=_sparse,
        )

    assert len(storage.puts) == prior_puts


class RecordingQdrant:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, list[models.PointStruct], bool]] = []

    async def upsert(
        self,
        collection_name: str,
        *,
        points: list[models.PointStruct],
        wait: bool,
    ) -> None:
        self.upserts.append((collection_name, points, wait))


async def _authority_fixture() -> tuple[
    StageSourcePlan,
    MemoryStorage,
    UUID,
    ChunkStageResult,
    EmbeddedStageResult,
]:
    plan = _plan()
    storage = MemoryStorage({plan.source_storage_key: b"Invoice total is INR 4200."})
    generation_id = uuid4()
    parsed = await parse_stage_external(
        _claim(plan, "parse", generation_id=generation_id),
        plan,
        storage,
        ParseProfile(),
    )
    chunked = await chunk_stage_external(
        _claim(plan, "chunk", generation_id=generation_id),
        plan,
        storage,
        parsed_digest=parsed.artifact.digest,
    )
    embedded = await embed_stage_external(
        _claim(plan, "embed", generation_id=generation_id),
        plan,
        storage,
        chunks_digest=chunked.artifact.digest,
        dense_embedder=RecordingDenseEmbedder(plan.dense_dimension),
        sparse_embedder=_sparse,
    )
    return plan, storage, generation_id, chunked, embedded


async def test_authority_upsert_uses_postgres_evidence_ids_and_physical_generation() -> None:
    plan, storage, generation_id, chunked, embedded = await _authority_fixture()
    persisted = [
        PersistedEvidence(
            id=uuid4(),
            ordinal=span.span_index,
            page_number=span.page_number,
            locator_kind=span.locator_kind,
            locator_label=span.locator_label,
            section_path=span.section_path,
            content_hash=hashlib.sha256(span.text.encode()).hexdigest(),
        )
        for span in chunked.evidence_spans
    ]
    authority = AuthorityPlan(
        source=plan,
        document_id=uuid4(),
        document_name="Vendor Invoice",
        version_label="Rev 3",
        revision_date=datetime(2026, 7, 19),
        projection_revision=0,
        evidence=persisted,
    )
    readiness: list[UUID] = []

    async def ready(candidate: UUID) -> bool:
        readiness.append(candidate)
        return True

    qdrant = RecordingQdrant()
    result = await authority_upsert_external(
        _claim(plan, "authority_upsert", generation_id=generation_id),
        authority,
        storage,
        chunks_digest=chunked.artifact.digest,
        vectors_digest=embedded.artifact.digest,
        authority_ready=ready,
        qdrant=qdrant,
    )

    assert readiness == [generation_id]
    assert len(result.output_digest) == 64
    assert len(qdrant.upserts) == 1
    collection, points, wait = qdrant.upserts[0]
    assert collection == f"openrag_authority_v1_{generation_id.hex}"
    assert wait is True
    assert [point.id for point in points] == [str(row.id) for row in persisted]
    payload = points[0].payload
    assert payload is not None
    assert payload["document_name"] == "Vendor Invoice"
    assert payload["version_label"] == "Rev 3"
    assert payload["page_number"] == 1
    assert payload["section_path"] == ["Document"]
    assert payload["is_current_approved"] is False
    assert payload["projection_revision"] == 0


async def test_authority_upsert_fails_closed_when_immediate_probe_is_unready() -> None:
    plan, storage, generation_id, chunked, embedded = await _authority_fixture()
    span = chunked.evidence_spans[0]
    authority = AuthorityPlan(
        source=plan,
        document_id=uuid4(),
        document_name="Invoice",
        version_label="Rev 1",
        revision_date=None,
        projection_revision=0,
        evidence=[
            PersistedEvidence(
                id=uuid4(),
                ordinal=0,
                page_number=span.page_number,
                locator_kind=span.locator_kind,
                locator_label=span.locator_label,
                section_path=span.section_path,
                content_hash=hashlib.sha256(span.text.encode()).hexdigest(),
            )
        ],
    )

    async def unready(_candidate: UUID) -> bool:
        return False

    qdrant = RecordingQdrant()
    with pytest.raises(AuthorityStorageUnavailable):
        await authority_upsert_external(
            _claim(plan, "authority_upsert", generation_id=generation_id),
            authority,
            storage,
            chunks_digest=chunked.artifact.digest,
            vectors_digest=embedded.artifact.digest,
            authority_ready=unready,
            qdrant=qdrant,
        )

    assert qdrant.upserts == []
