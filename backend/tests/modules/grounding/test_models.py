from sqlalchemy import inspect

from openrag.modules.documents.models import DocumentAuthorityReadiness
from openrag.modules.grounding.models import GroundingCalibrationRun, GroundingPolicy
from openrag.modules.models.models import Model


def test_grounding_policy_has_bounded_rates_and_no_sensitive_payload_columns() -> None:
    table = GroundingPolicy.__table__
    column_names = set(table.columns.keys())
    assert {
        "entailment_threshold",
        "measured_false_support_rate",
        "measured_false_refusal_rate",
        "calibration_dataset_hash",
        "credential_fingerprint",
    } <= column_names
    assert not ({"secret", "prompt", "evidence_text", "provider_response"} & column_names)
    assert any(
        "entailment_threshold" in str(check.sqltext)
        for check in table.constraints
        if hasattr(check, "sqltext")
    )


def test_readiness_and_calibration_shapes_are_bounded_and_secret_free() -> None:
    readiness_columns = set(inspect(DocumentAuthorityReadiness).columns.keys())
    calibration_columns = set(inspect(GroundingCalibrationRun).columns.keys())
    assert {"generation_id", "request_digest", "expires_at", "status"} <= readiness_columns
    assert {"generation_id", "idempotency_digest", "state", "attempts"} <= calibration_columns
    sensitive = {"secret", "prompt", "evidence_text", "provider_response", "credential"}
    assert not (sensitive & readiness_columns)
    assert not (sensitive & calibration_columns)


def test_model_capabilities_default_fail_closed() -> None:
    table = Model.__table__
    assert table.c.supports_chat_completion.default.arg is False
    assert table.c.supports_structured_json.default.arg is False
    assert table.c.supports_verifier.default.arg is False
    assert table.c.provider_preset_version.nullable is True
