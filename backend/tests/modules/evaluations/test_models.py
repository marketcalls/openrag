from sqlalchemy import Text

from openrag.modules.evaluations.models import (
    EvaluationCase,
    EvaluationCaseEvidence,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationPolicy,
    EvaluationRun,
)


def test_evaluation_storage_separates_approved_corpus_from_content_free_results() -> None:
    assert isinstance(EvaluationCase.__table__.c.question.type, Text)
    result_columns = EvaluationCaseResult.__table__.c

    assert "answer" not in result_columns
    assert "prompt" not in result_columns
    assert "reasoning" not in result_columns
    assert "answer_digest" in result_columns


def test_all_evaluation_entities_have_tenant_scope() -> None:
    for model in (
        EvaluationDataset,
        EvaluationDatasetVersion,
        EvaluationCase,
        EvaluationCaseEvidence,
        EvaluationPolicy,
        EvaluationRun,
        EvaluationCaseResult,
    ):
        assert "org_id" in model.__table__.c
        assert "workspace_id" in model.__table__.c


def test_runs_have_explicit_budgets_and_complete_lease_fencing() -> None:
    columns = EvaluationRun.__table__.c

    for name in ("max_cases", "max_tokens", "max_cost_microusd"):
        assert name in columns
    for name in ("lease_owner", "lease_token", "lease_expires_at"):
        assert name in columns
    assert "use_llm_judge" in columns


def test_automation_policies_are_bounded_and_runs_record_trigger_provenance() -> None:
    policy_columns = EvaluationPolicy.__table__.c
    run_columns = EvaluationRun.__table__.c

    for name in (
        "dataset_id",
        "model_id",
        "interval_hours",
        "next_run_at",
        "max_cases",
        "max_tokens",
        "max_cost_microusd",
        "trigger_on_config_change",
    ):
        assert name in policy_columns
    for name in ("trigger_kind", "trigger_key", "policy_id"):
        assert name in run_columns
