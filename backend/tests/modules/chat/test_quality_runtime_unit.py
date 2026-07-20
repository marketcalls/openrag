import hashlib
from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from openrag.modules.chat.quality_models import AnswerQualityAudit
from openrag.modules.chat.quality_runtime import (
    QualityAuditEvidenceRow,
    QualityAuditObservation,
    build_quality_claim_query,
    extract_cited_evidence,
)


def test_quality_claim_query_is_parallel_worker_safe_and_bounded() -> None:
    statement = build_quality_claim_query(datetime(2026, 7, 20))
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "attempts" in sql
    assert "lease_expires_at" in sql
    assert "LIMIT" in sql


def test_quality_observation_accepts_only_bounded_scores() -> None:
    observation = QualityAuditObservation(
        status="passed",
        grounding_score=0.97,
        completeness_score=0.91,
        reason_code="validated",
    )

    assert observation.passed is True

    with pytest.raises(ValueError, match="quality_audit_score_invalid"):
        QualityAuditObservation(
            status="failed",
            grounding_score=1.1,
            completeness_score=0.5,
            reason_code="unsupported_claims",
        )


def test_quality_observation_never_contains_content_or_reasoning() -> None:
    fields = set(QualityAuditObservation.__dataclass_fields__)
    assert fields == {
        "status",
        "grounding_score",
        "completeness_score",
        "reason_code",
    }


def _audit() -> AnswerQualityAudit:
    return AnswerQualityAudit(
        id=UUID(int=1),
        org_id=UUID(int=2),
        workspace_id=UUID(int=3),
        message_id=UUID(int=4),
        grounding_policy_id=UUID(int=5),
        grounding_policy_version=6,
        verifier_model_id=UUID(int=7),
    )


def _evidence_row(*, marker: int = 1, text: str = "Invoice total ₹500") -> QualityAuditEvidenceRow:
    chunk_text = f"prefix {text} suffix"
    encoded = chunk_text.encode("utf-8")
    evidence = text.encode("utf-8")
    start = encoded.index(evidence)
    return QualityAuditEvidenceRow(
        org_id=UUID(int=2),
        workspace_id=UUID(int=3),
        message_id=UUID(int=4),
        marker=marker,
        grounding_policy_id=UUID(int=5),
        grounding_policy_version=6,
        verifier_model_id=UUID(int=7),
        citation_content_hash=hashlib.sha256(evidence).hexdigest(),
        span_content_hash=hashlib.sha256(evidence).hexdigest(),
        artifact_byte_start=start,
        artifact_byte_end=start + len(evidence),
        chunk_text=chunk_text,
    )


def test_cited_evidence_is_reconstructed_from_exact_hashed_utf8_span() -> None:
    evidence = extract_cited_evidence(_audit(), [_evidence_row()])

    assert evidence == ("[1] Invoice total ₹500",)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("org_id", UUID(int=99), "scope"),
        ("grounding_policy_version", 99, "policy"),
        ("citation_content_hash", "0" * 64, "hash"),
        ("artifact_byte_end", 999, "range"),
    ],
)
def test_cited_evidence_fails_closed_on_snapshot_tampering(
    field: str,
    value: object,
    error: str,
) -> None:
    row = _evidence_row()
    object.__setattr__(row, field, value)

    with pytest.raises(ValueError, match=error):
        extract_cited_evidence(_audit(), [row])


def test_cited_evidence_rejects_duplicate_or_unbounded_markers() -> None:
    with pytest.raises(ValueError, match="markers"):
        extract_cited_evidence(_audit(), [_evidence_row(), _evidence_row()])
    with pytest.raises(ValueError, match="count"):
        extract_cited_evidence(
            _audit(),
            [_evidence_row(marker=marker) for marker in range(1, 10)],
        )
