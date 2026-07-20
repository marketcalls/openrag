"""Lease-fenced execution and content-free scoring for RAG evaluations."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, or_, select

from openrag.modules.evaluations.metrics import citation_metrics, rank_metrics
from openrag.modules.evaluations.models import EvaluationRun


@dataclass(frozen=True, slots=True)
class EvaluationObservation:
    """Ephemeral executor output; generated answer content is intentionally absent."""

    retrieved_evidence_ids: tuple[UUID, ...]
    cited_evidence_ids: tuple[UUID, ...]
    did_refuse: bool
    answer_digest: str | None
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_microusd: int
    answer_relevance: float | None = None

    def __post_init__(self) -> None:
        if self.answer_digest is not None and (
            len(self.answer_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.answer_digest)
        ):
            raise ValueError("evaluation_answer_digest_invalid")
        numeric = (
            self.latency_ms,
            self.prompt_tokens,
            self.completion_tokens,
            self.estimated_cost_microusd,
        )
        if any(value < 0 for value in numeric):
            raise ValueError("evaluation_usage_invalid")
        if self.answer_relevance is not None and not 0 <= self.answer_relevance <= 1:
            raise ValueError("evaluation_answer_relevance_invalid")


@dataclass(frozen=True, slots=True)
class EvaluationCaseScore:
    retrieved_evidence_ids: tuple[UUID, ...]
    cited_evidence_ids: tuple[UUID, ...]
    did_refuse: bool
    answer_digest: str | None
    recall: float
    precision: float
    mrr: float
    ndcg: float
    citation_precision: float
    citation_recall: float
    groundedness: float
    answer_relevance: float | None
    correct_refusal: float
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_microusd: int


@dataclass(frozen=True, slots=True)
class EvaluationBudget:
    max_cases: int
    max_tokens: int
    max_cost_microusd: int

    def __post_init__(self) -> None:
        if min(self.max_cases, self.max_tokens, self.max_cost_microusd) < 1:
            raise ValueError("evaluation_budget_invalid")

    def admits(
        self,
        *,
        completed_cases: int,
        consumed_tokens: int,
        consumed_cost_microusd: int,
        next_tokens: int,
        next_cost_microusd: int,
    ) -> bool:
        values = (
            completed_cases,
            consumed_tokens,
            consumed_cost_microusd,
            next_tokens,
            next_cost_microusd,
        )
        if any(value < 0 for value in values):
            raise ValueError("evaluation_consumption_invalid")
        return (
            completed_cases < self.max_cases
            and consumed_tokens + next_tokens <= self.max_tokens
            and consumed_cost_microusd + next_cost_microusd <= self.max_cost_microusd
        )


def score_observation(
    *,
    expected_evidence_ids: set[UUID],
    should_refuse: bool,
    observation: EvaluationObservation,
    k: int,
) -> EvaluationCaseScore:
    retrieved = list(dict.fromkeys(observation.retrieved_evidence_ids))
    cited = set(observation.cited_evidence_ids)
    ranking = rank_metrics(
        retrieved=[str(identifier) for identifier in retrieved],
        relevant={str(identifier) for identifier in expected_evidence_ids},
        k=k,
    )
    citations = citation_metrics(
        cited={str(identifier) for identifier in cited},
        expected={str(identifier) for identifier in expected_evidence_ids},
        retrieved={str(identifier) for identifier in retrieved},
    )
    return EvaluationCaseScore(
        retrieved_evidence_ids=tuple(retrieved),
        cited_evidence_ids=tuple(dict.fromkeys(observation.cited_evidence_ids)),
        did_refuse=observation.did_refuse,
        answer_digest=observation.answer_digest,
        recall=ranking.recall,
        precision=ranking.precision,
        mrr=ranking.mrr,
        ndcg=ranking.ndcg,
        citation_precision=citations.precision,
        citation_recall=citations.recall,
        groundedness=citations.groundedness,
        answer_relevance=observation.answer_relevance,
        correct_refusal=1.0 if should_refuse == observation.did_refuse else 0.0,
        latency_ms=observation.latency_ms,
        prompt_tokens=observation.prompt_tokens,
        completion_tokens=observation.completion_tokens,
        estimated_cost_microusd=observation.estimated_cost_microusd,
    )


def build_claim_query(now: datetime) -> Select[tuple[EvaluationRun]]:
    """Claim oldest queued work or safely recover an expired running lease."""

    return (
        select(EvaluationRun)
        .where(
            or_(
                EvaluationRun.status == "queued",
                (
                    (EvaluationRun.status == "running")
                    & (EvaluationRun.lease_expires_at < now)
                ),
            )
        )
        .order_by(EvaluationRun.created_at, EvaluationRun.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
