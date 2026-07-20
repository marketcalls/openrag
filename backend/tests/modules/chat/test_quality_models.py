from uuid import uuid4

from sqlalchemy import inspect

from openrag.modules.chat.quality_models import AnswerQualityAudit


def test_answer_quality_audit_is_content_free_and_lease_fenced() -> None:
    table = inspect(AnswerQualityAudit).local_table

    assert set(table.c.keys()) >= {
        "org_id",
        "workspace_id",
        "message_id",
        "grounding_policy_id",
        "grounding_policy_version",
        "verifier_model_id",
        "status",
        "attempts",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "grounding_score",
        "completeness_score",
        "passed",
        "result_code",
        "error_code",
        "started_at",
        "finished_at",
    }
    assert "answer" not in table.c
    assert "question" not in table.c
    assert "reasoning" not in table.c
    constraints = " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if hasattr(constraint, "sqltext")
    )
    assert "queued" in constraints and "running" in constraints
    assert "grounding_score" in constraints
    assert "lease_owner" in constraints and "lease_token" in constraints


def test_quality_audit_defaults_to_a_safe_queued_record() -> None:
    audit = AnswerQualityAudit(
        org_id=uuid4(),
        workspace_id=uuid4(),
        message_id=uuid4(),
        grounding_policy_id=uuid4(),
        grounding_policy_version=2,
        verifier_model_id=uuid4(),
    )

    assert audit.status is None or audit.status == "queued"
    assert audit.grounding_score is None
    assert audit.completeness_score is None
    assert audit.passed is None
    assert audit.result_code is None
