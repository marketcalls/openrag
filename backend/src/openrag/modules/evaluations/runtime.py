"""Lease-fenced execution and content-free scoring for RAG evaluations."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.db import naive_utc
from openrag.modules.evaluations.metrics import citation_metrics, rank_metrics
from openrag.modules.evaluations.models import EvaluationRun

MAX_EVALUATION_ATTEMPTS = 1000


@dataclass(frozen=True, slots=True)
class EvaluationLeaseClaim:
    run_id: UUID
    org_id: UUID
    workspace_id: UUID
    dataset_version_id: UUID
    token: UUID
    owner: str
    attempt: int
    recovered: bool


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
                and_(
                    EvaluationRun.status == "running",
                    EvaluationRun.lease_expires_at.is_not(None),
                    EvaluationRun.lease_expires_at <= now,
                ),
            )
            & (EvaluationRun.attempts < MAX_EVALUATION_ATTEMPTS)
        )
        .order_by(EvaluationRun.created_at, EvaluationRun.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def _validate_lease(owner: str, lease_seconds: int) -> None:
    if not 1 <= len(owner) <= 200:
        raise ValueError("evaluation_lease_owner_invalid")
    if not 30 <= lease_seconds <= 600:
        raise ValueError("evaluation_lease_seconds_invalid")


async def claim_next_evaluation_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    lease_seconds: int,
) -> EvaluationLeaseClaim | None:
    _validate_lease(owner, lease_seconds)
    now = naive_utc()
    async with session_factory.begin() as session:
        run = await session.scalar(build_claim_query(now))
        if run is None:
            return None
        recovered = run.status == "running"
        token = uuid4()
        run.status = "running"
        run.started_at = run.started_at or now
        run.lease_owner = owner
        run.lease_token = token
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        run.attempts += 1
        await session.flush()
        return EvaluationLeaseClaim(
            run_id=run.id,
            org_id=run.org_id,
            workspace_id=run.workspace_id,
            dataset_version_id=run.dataset_version_id,
            token=token,
            owner=owner,
            attempt=run.attempts,
            recovered=recovered,
        )


async def renew_evaluation_lease(
    session_factory: async_sessionmaker[AsyncSession],
    claim: EvaluationLeaseClaim,
    *,
    lease_seconds: int,
) -> bool:
    _validate_lease(claim.owner, lease_seconds)
    async with session_factory.begin() as session:
        result = await session.execute(
            update(EvaluationRun)
            .where(
                EvaluationRun.id == claim.run_id,
                EvaluationRun.status == "running",
                EvaluationRun.lease_token == claim.token,
                EvaluationRun.lease_owner == claim.owner,
            )
            .values(
                lease_expires_at=naive_utc() + timedelta(seconds=lease_seconds)
            )
            .returning(EvaluationRun.id)
        )
        return result.scalar_one_or_none() == claim.run_id
