"""Authority-preserving Qdrant points for hypothetical-question retrieval."""

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from qdrant_client import models

_HQ_NAMESPACE = UUID("4e3f3260-81fd-47c4-a0ec-6dc2850cc267")


@dataclass(frozen=True, slots=True)
class EnrichmentEvidence:
    org_id: UUID
    workspace_id: UUID
    document_id: UUID
    document_version_id: UUID
    evidence_span_id: UUID
    projection_revision: int
    page_number: int
    ordinal: int
    document_name: str
    version_label: str
    revision_date: datetime | None
    section_path: tuple[str, ...]
    locator_kind: str
    locator_label: str
    content_hash: str
    text: str
    source_mime: str

    def __post_init__(self) -> None:
        if (
            self.projection_revision < 0
            or self.page_number < 1
            or self.ordinal < 0
            or not self.text
            or len(self.text) > 12_000
            or len(self.content_hash) != 64
            or any(value not in "0123456789abcdef" for value in self.content_hash)
            or not self.section_path
        ):
            raise ValueError("enrichment_evidence_invalid")


def build_hypothetical_question_points(
    evidence: EnrichmentEvidence,
    *,
    summary: str | None,
    keywords: tuple[str, ...],
    questions: tuple[str, ...],
    dense_vectors: list[list[float]],
    sparse_vectors: list[models.SparseVector],
) -> list[models.PointStruct]:
    """Create derived vectors whose payload always resolves to parent evidence."""

    if (
        len(questions) > 3
        or len(questions) != len(dense_vectors)
        or len(questions) != len(sparse_vectors)
        or any(not question or len(question) > 300 for question in questions)
    ):
        raise ValueError("enrichment_question_vectors_invalid")
    if any(
        not vector or any(not math.isfinite(value) for value in vector)
        for vector in dense_vectors
    ):
        raise ValueError("enrichment_dense_vector_invalid")

    payload = {
        "tenant_id": str(evidence.org_id),
        "workspace_id": str(evidence.workspace_id),
        "document_id": str(evidence.document_id),
        "document_version_id": str(evidence.document_version_id),
        "evidence_span_id": str(evidence.evidence_span_id),
        "is_current_approved": True,
        "projection_revision": evidence.projection_revision,
        "page_number": evidence.page_number,
        "page": evidence.page_number,
        "chunk_index": evidence.ordinal,
        "document_name": evidence.document_name,
        "version_label": evidence.version_label,
        "revision_date": (
            evidence.revision_date.isoformat()
            if evidence.revision_date is not None
            else None
        ),
        "section_path": list(evidence.section_path),
        "section": " > ".join(evidence.section_path),
        "locator_kind": evidence.locator_kind,
        "locator_label": evidence.locator_label,
        "content_hash": evidence.content_hash,
        "text": evidence.text,
        "source_mime": evidence.source_mime,
        "kind": "hypothetical_question",
        "summary": summary,
        "keywords": list(keywords),
    }
    points: list[models.PointStruct] = []
    for index, (question, dense, sparse) in enumerate(
        zip(questions, dense_vectors, sparse_vectors, strict=True)
    ):
        question_digest = hashlib.sha256(question.encode()).hexdigest()
        point_id = uuid5(
            _HQ_NAMESPACE,
            f"{evidence.evidence_span_id}:{index}:{question_digest}",
        )
        points.append(
            models.PointStruct(
                id=str(point_id),
                vector={"dense": dense, "sparse": sparse},
                payload=payload,
            )
        )
    return points
