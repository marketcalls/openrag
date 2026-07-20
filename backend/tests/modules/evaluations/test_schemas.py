from uuid import uuid4

import pytest
from pydantic import ValidationError

from openrag.modules.evaluations.schemas import (
    EvaluationCaseCreate,
    EvaluationDatasetVersionCreate,
    EvaluationEvidenceCreate,
    EvaluationRunCreate,
)


def evidence() -> EvaluationEvidenceCreate:
    return EvaluationEvidenceCreate(
        document_version_id=uuid4(),
        evidence_span_id=uuid4(),
    )


def test_answerable_case_requires_unique_expected_evidence() -> None:
    expected = evidence()

    case = EvaluationCaseCreate(
        question="What is the approved pressure limit?",
        expected_evidence=[expected],
    )

    assert case.should_refuse is False
    with pytest.raises(ValidationError, match="evaluation_evidence_duplicate"):
        EvaluationCaseCreate(
            question="What is the approved pressure limit?",
            expected_evidence=[expected, expected],
        )


def test_refusal_case_must_not_claim_expected_evidence() -> None:
    with pytest.raises(ValidationError, match="evaluation_refusal_evidence_invalid"):
        EvaluationCaseCreate(
            question="What is tomorrow's unapproved operating plan?",
            should_refuse=True,
            expected_evidence=[evidence()],
        )

    case = EvaluationCaseCreate(
        question="What is tomorrow's unapproved operating plan?",
        should_refuse=True,
    )
    assert case.expected_evidence == []


def test_dataset_version_has_bounded_nonempty_cases() -> None:
    with pytest.raises(ValidationError):
        EvaluationDatasetVersionCreate(cases=[])

    with pytest.raises(ValidationError):
        EvaluationCaseCreate(question="x" * 2001, expected_evidence=[evidence()])


def test_evaluation_run_requires_explicit_case_token_and_cost_budgets() -> None:
    valid = {
        "dataset_version_id": uuid4(),
        "model_id": uuid4(),
        "max_cases": 100,
        "max_tokens": 500_000,
        "max_cost_microusd": 25_000_000,
    }
    run = EvaluationRunCreate.model_validate(valid)

    assert run.max_cases == 100
    assert run.use_llm_judge is False
    for field in ("max_cases", "max_tokens", "max_cost_microusd"):
        invalid = valid | {field: 0}
        with pytest.raises(ValidationError):
            EvaluationRunCreate.model_validate(invalid)
