"""Deterministic baseline evidence sufficiency policy."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ScoredEvidence(Protocol):
    @property
    def dense_score(self) -> float | None: ...


class EvidenceStatus(StrEnum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class SufficiencyPolicy:
    min_dense_score: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_dense_score) or not 0 <= self.min_dense_score <= 1:
            raise ValueError("min_dense_score must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    status: EvidenceStatus
    reason_code: str | None
    best_dense_score: float | None


def evaluate_evidence(
    query: str,
    evidence: Sequence[ScoredEvidence],
    policy: SufficiencyPolicy,
    *,
    deterministic_conflict: bool = False,
) -> EvidenceDecision:
    normalized = query.strip()
    if not 1 <= len(normalized) <= 2_000:
        raise ValueError("query must contain between 1 and 2000 characters")
    if len(evidence) > 32:
        raise ValueError("evidence_limit_exceeded")
    if not evidence:
        return EvidenceDecision(
            status=EvidenceStatus.INSUFFICIENT,
            reason_code="no_eligible_evidence",
            best_dense_score=None,
        )

    scores = [item.dense_score for item in evidence if item.dense_score is not None]
    if any(not math.isfinite(score) for score in scores):
        raise ValueError("evidence_score_invalid")
    best = max(scores, default=None)
    if best is None:
        return EvidenceDecision(
            status=EvidenceStatus.INSUFFICIENT,
            reason_code="score_unavailable",
            best_dense_score=None,
        )
    if deterministic_conflict:
        return EvidenceDecision(
            status=EvidenceStatus.CONFLICT,
            reason_code="conflicting_evidence",
            best_dense_score=best,
        )
    if best < policy.min_dense_score:
        return EvidenceDecision(
            status=EvidenceStatus.INSUFFICIENT,
            reason_code="below_threshold",
            best_dense_score=best,
        )
    return EvidenceDecision(
        status=EvidenceStatus.SUFFICIENT,
        reason_code=None,
        best_dense_score=best,
    )
