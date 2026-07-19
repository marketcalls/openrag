from datetime import datetime
from uuid import UUID, uuid4

import pytest

from openrag.modules.documents.pipeline import IngestFailure, ParseProfile
from openrag.modules.documents.stage_adapters import (
    StageSourcePlan,
    chunk_stage_external,
    parse_stage_external,
)
from openrag.modules.documents.stage_artifacts import (
    artifact_key,
    decode_chunk_artifact,
    decode_parsed_artifact,
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
