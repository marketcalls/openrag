from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from openrag.modules.evaluations.runtime import (
    EvaluationBudget,
    EvaluationObservation,
    build_claim_query,
    score_observation,
)


def test_claim_query_is_ordered_skip_locked_and_recovers_expired_leases() -> None:
    sql = str(
        build_claim_query(datetime(2026, 7, 20, tzinfo=UTC)).compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": False},
        )
    )

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "evaluation_runs.status" in sql
    assert "evaluation_runs.lease_expires_at" in sql
    assert "ORDER BY evaluation_runs.created_at" in sql


def test_observation_scores_only_identifiers_and_numeric_facts() -> None:
    expected_a, expected_b, unexpected = uuid4(), uuid4(), uuid4()
    observation = EvaluationObservation(
        retrieved_evidence_ids=(unexpected, expected_a, expected_b),
        cited_evidence_ids=(expected_a, unexpected),
        did_refuse=False,
        answer_digest="a" * 64,
        latency_ms=850,
        prompt_tokens=1200,
        completion_tokens=180,
        estimated_cost_microusd=2200,
    )

    score = score_observation(
        expected_evidence_ids={expected_a, expected_b},
        should_refuse=False,
        observation=observation,
        k=3,
    )

    assert score.recall == 1.0
    assert score.precision == 2 / 3
    assert score.mrr == 0.5
    assert score.citation_precision == 0.5
    assert score.citation_recall == 0.5
    assert score.groundedness == 1.0
    assert score.correct_refusal == 1.0
    assert not hasattr(score, "answer")


def test_budget_admission_accounts_for_cases_tokens_and_cost() -> None:
    budget = EvaluationBudget(max_cases=2, max_tokens=1000, max_cost_microusd=5000)

    assert budget.admits(
        completed_cases=1,
        consumed_tokens=600,
        consumed_cost_microusd=2000,
        next_tokens=300,
        next_cost_microusd=1000,
    )
    assert not budget.admits(
        completed_cases=2,
        consumed_tokens=600,
        consumed_cost_microusd=2000,
        next_tokens=1,
        next_cost_microusd=1,
    )
    assert not budget.admits(
        completed_cases=1,
        consumed_tokens=900,
        consumed_cost_microusd=2000,
        next_tokens=101,
        next_cost_microusd=1,
    )
