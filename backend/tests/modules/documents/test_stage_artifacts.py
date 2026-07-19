import hashlib
from dataclasses import replace
from uuid import uuid4

import pytest

from openrag.modules.documents.pipeline import chunk_blocks
from openrag.modules.documents.stage_artifacts import (
    ArtifactIdentity,
    artifact_key,
    decode_chunk_artifact,
    decode_parsed_artifact,
    encode_chunk_artifact,
    encode_parsed_artifact,
)
from openrag.modules.documents.stages import StageCheckpoint
from tests.modules.documents.test_provenance import fixture_provenance


def _identity(stage: str = "parse") -> ArtifactIdentity:
    return ArtifactIdentity(
        org_id=uuid4(),
        workspace_id=uuid4(),
        document_version_id=uuid4(),
        checkpoint=StageCheckpoint(
            stage=stage,
            pipeline_kind="rebuild",
            pipeline_attempt=2,
            authority_generation_id=uuid4(),
        ),
    )


def test_parsed_artifact_is_canonical_identity_bound_and_lossless() -> None:
    identity = _identity()
    blocks, _chunks, _spans = fixture_provenance()

    first = encode_parsed_artifact(
        identity,
        blocks=blocks,
        page_count=2,
        ocr_pages=(2,),
        low_confidence_ocr_pages=(2,),
    )
    second = encode_parsed_artifact(
        identity,
        blocks=blocks,
        page_count=2,
        ocr_pages=(2,),
        low_confidence_ocr_pages=(2,),
    )

    assert first == second
    assert len(first.digest) == 64
    assert first.key == artifact_key(identity, "parsed", first.digest)
    decoded = decode_parsed_artifact(
        first.data,
        expected=identity,
        expected_digest=first.digest,
    )
    assert decoded.blocks == blocks
    assert decoded.page_count == 2
    assert decoded.ocr_pages == (2,)
    assert decoded.low_confidence_ocr_pages == (2,)


def test_chunk_artifact_roundtrip_rejects_cross_wired_identity() -> None:
    identity = _identity("chunk")
    blocks, chunks, spans = fixture_provenance()
    artifact = encode_chunk_artifact(identity, chunks=chunks, evidence_spans=spans)

    decoded_chunks, decoded_spans = decode_chunk_artifact(
        artifact.data,
        expected=identity,
        expected_digest=artifact.digest,
    )

    assert decoded_chunks == chunks
    assert decoded_spans == spans
    foreign = replace(identity, document_version_id=uuid4())
    with pytest.raises(ValueError, match="identity mismatch"):
        decode_chunk_artifact(
            artifact.data,
            expected=foreign,
            expected_digest=artifact.digest,
        )
    with pytest.raises(ValueError, match="digest mismatch"):
        decode_chunk_artifact(
            artifact.data,
            expected=identity,
            expected_digest="0" * 64,
        )


def test_artifact_decoder_rejects_tampered_schema_and_noncontiguous_output() -> None:
    chunk_identity = _identity("chunk")
    blocks, _chunks, _spans = fixture_provenance()
    chunks, spans = chunk_blocks(blocks, target_chars=100)
    invalid_chunks = [replace(chunks[0], chunk_index=3), *chunks[1:]]

    with pytest.raises(ValueError, match="contiguous"):
        encode_chunk_artifact(
            chunk_identity,
            chunks=invalid_chunks,
            evidence_spans=spans,
        )

    parsed_identity = _identity()
    parsed = encode_parsed_artifact(parsed_identity, blocks=blocks, page_count=2)
    tampered = parsed.data.replace(
        b'"schema":"openrag.parsed.v1"',
        b'"schema":"openrag.parsed.v0"',
    )
    with pytest.raises(ValueError, match="schema"):
        decode_parsed_artifact(
            tampered,
            expected=parsed_identity,
            expected_digest=hashlib.sha256(tampered).hexdigest(),
        )
