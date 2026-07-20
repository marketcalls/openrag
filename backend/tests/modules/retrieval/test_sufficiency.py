from dataclasses import dataclass

import pytest

from openrag.modules.retrieval.sufficiency import (
    EvidenceStatus,
    SufficiencyPolicy,
    evaluate_evidence,
)


@dataclass(frozen=True)
class ScoredEvidence:
    dense_score: float | None


def test_no_eligible_evidence_refuses() -> None:
    decision = evaluate_evidence(
        "What is the evacuation policy?",
        [],
        SufficiencyPolicy(min_dense_score=0.7),
    )

    assert decision.status is EvidenceStatus.INSUFFICIENT
    assert decision.reason_code == "no_eligible_evidence"


def test_missing_calibrated_score_refuses_even_with_evidence() -> None:
    decision = evaluate_evidence(
        "What is the evacuation policy?",
        [ScoredEvidence(dense_score=None)],
        SufficiencyPolicy(min_dense_score=0.7),
    )

    assert decision.status is EvidenceStatus.INSUFFICIENT
    assert decision.reason_code == "score_unavailable"


def test_below_threshold_refuses_before_generation() -> None:
    decision = evaluate_evidence(
        "What is the evacuation policy?",
        [ScoredEvidence(dense_score=0.69)],
        SufficiencyPolicy(min_dense_score=0.7),
    )

    assert decision.status is EvidenceStatus.INSUFFICIENT
    assert decision.reason_code == "below_threshold"


def test_threshold_match_is_sufficient() -> None:
    decision = evaluate_evidence(
        "What is the evacuation policy?",
        [ScoredEvidence(dense_score=0.7)],
        SufficiencyPolicy(min_dense_score=0.7),
    )

    assert decision.status is EvidenceStatus.SUFFICIENT
    assert decision.reason_code is None
    assert decision.best_dense_score == 0.7


def test_deterministically_identified_conflict_never_silently_arbitrates() -> None:
    decision = evaluate_evidence(
        "What is the approved evacuation time?",
        [ScoredEvidence(dense_score=0.91), ScoredEvidence(dense_score=0.88)],
        SufficiencyPolicy(min_dense_score=0.7),
        deterministic_conflict=True,
    )

    assert decision.status is EvidenceStatus.CONFLICT
    assert decision.reason_code == "conflicting_evidence"


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan")])
def test_policy_rejects_uncalibrated_thresholds(value: float) -> None:
    with pytest.raises(ValueError, match="min_dense_score"):
        SufficiencyPolicy(min_dense_score=value)
