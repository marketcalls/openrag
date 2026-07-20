from datetime import datetime
from uuid import UUID

from qdrant_client import models

from openrag.modules.documents.enrichment_points import (
    EnrichmentEvidence,
    build_hypothetical_question_points,
)


def _evidence() -> EnrichmentEvidence:
    return EnrichmentEvidence(
        org_id=UUID(int=1),
        workspace_id=UUID(int=2),
        document_id=UUID(int=3),
        document_version_id=UUID(int=4),
        evidence_span_id=UUID(int=5),
        projection_revision=7,
        page_number=2,
        ordinal=11,
        document_name="Safety Manual",
        version_label="Rev 4",
        revision_date=datetime(2026, 7, 1),
        section_path=("PPE", "Zone 2"),
        locator_kind="page",
        locator_label="2",
        content_hash="a" * 64,
        text="Certified PPE is required in zone 2.",
        source_mime="application/pdf",
    )


def test_hypothetical_points_preserve_parent_authority_and_citable_text() -> None:
    sparse = models.SparseVector(indices=[1, 9], values=[0.5, 1.0])
    points = build_hypothetical_question_points(
        _evidence(),
        summary="Zone 2 PPE requirements.",
        keywords=("ppe", "zone 2"),
        questions=("What PPE is required in zone 2?",),
        dense_vectors=[[0.1, 0.2, 0.3]],
        sparse_vectors=[sparse],
    )

    assert len(points) == 1
    payload = points[0].payload or {}
    assert payload["tenant_id"] == str(UUID(int=1))
    assert payload["workspace_id"] == str(UUID(int=2))
    assert payload["document_version_id"] == str(UUID(int=4))
    assert payload["evidence_span_id"] == str(UUID(int=5))
    assert payload["content_hash"] == "a" * 64
    assert payload["text"] == "Certified PPE is required in zone 2."
    assert payload["kind"] == "hypothetical_question"
    assert "question" not in payload
    assert points[0].vector == {"dense": [0.1, 0.2, 0.3], "sparse": sparse}


def test_hypothetical_point_ids_are_deterministic_and_question_specific() -> None:
    evidence = _evidence()
    sparse = models.SparseVector(indices=[1], values=[1.0])
    first = build_hypothetical_question_points(
        evidence,
        summary=None,
        keywords=(),
        questions=("Question one?", "Question two?"),
        dense_vectors=[[0.1], [0.2]],
        sparse_vectors=[sparse, sparse],
    )
    second = build_hypothetical_question_points(
        evidence,
        summary=None,
        keywords=(),
        questions=("Question one?", "Question two?"),
        dense_vectors=[[0.1], [0.2]],
        sparse_vectors=[sparse, sparse],
    )

    assert [point.id for point in first] == [point.id for point in second]
    assert first[0].id != first[1].id
