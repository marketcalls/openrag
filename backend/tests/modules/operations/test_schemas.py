from uuid import uuid4

import pytest
from pydantic import ValidationError

from openrag.modules.operations.schemas import (
    ErrorOccurrenceCreate,
    RagOperationsRunOut,
    RagRunFactCreate,
)


def test_error_contract_rejects_unknown_category_and_extra_content() -> None:
    with pytest.raises(ValidationError):
        ErrorOccurrenceCreate(
            category="secret",  # type: ignore[arg-type]
            code="provider.timeout",
            service="api",
            environment="prod",
            exception_type="TimeoutError",
        )
    with pytest.raises(ValidationError):
        ErrorOccurrenceCreate(
            category="provider_transient",
            code="provider.timeout",
            service="api",
            environment="prod",
            exception_type="TimeoutError",
            message="contains customer data",  # type: ignore[call-arg]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", "UPPER CASE"),
        ("service", "api/service"),
        ("trace_id", "../invalid"),
        ("exception_type", "x" * 201),
        ("route_template", "x" * 201),
        ("release", "x" * 101),
    ],
)
def test_error_contract_bounds_safe_metadata(field: str, value: str) -> None:
    payload: dict[str, object] = {
        "category": "internal",
        "code": "internal.unhandled",
        "service": "api",
        "environment": "prod",
        "exception_type": "RuntimeError",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        ErrorOccurrenceCreate.model_validate(payload)


def test_run_scope_requires_tenant_ids_together() -> None:
    with pytest.raises(ValidationError, match="run scope"):
        ErrorOccurrenceCreate(
            category="internal",
            code="internal.unhandled",
            service="run-worker",
            environment="prod",
            exception_type="RuntimeError",
            run_id=uuid4(),
        )


def test_rag_fact_rejects_raw_fields_and_negative_metrics() -> None:
    valid = {
        "org_id": uuid4(),
        "workspace_id": uuid4(),
        "run_id": uuid4(),
        "route": "rag",
        "outcome": "grounded",
        "environment": "prod",
        "latency_ms": 900,
        "prompt_tokens": 100,
        "completion_tokens": 40,
    }

    with pytest.raises(ValidationError):
        RagRunFactCreate.model_validate({**valid, "latency_ms": -1})
    with pytest.raises(ValidationError):
        RagRunFactCreate.model_validate({**valid, "prompt": "private"})


def test_rag_fact_accepts_bounded_safe_measurements() -> None:
    value = RagRunFactCreate(
        org_id=uuid4(),
        workspace_id=uuid4(),
        run_id=uuid4(),
        route="conversation",
        outcome="conversational",
        environment="prod",
        release="2026.07.20",
        trace_id="a" * 32,
        latency_ms=320,
        ttft_ms=80,
        provider_ms=250,
        prompt_tokens=90,
        completion_tokens=20,
    )

    assert value.latency_ms == 320
    assert value.trace_id == "a" * 32
    assert not hasattr(value, "prompt")


def test_operations_run_contract_exposes_metrics_but_never_content() -> None:
    fields = set(RagOperationsRunOut.model_fields)

    assert {
        "run_id",
        "trace_id",
        "route",
        "outcome",
        "latency_ms",
        "ttft_ms",
        "retrieval_count",
        "citation_count",
        "prompt_tokens",
        "completion_tokens",
    } <= fields
    assert fields.isdisjoint(
        {
            "prompt",
            "response",
            "query",
            "document_text",
            "retrieved_text",
            "filename",
            "memory",
            "provider_payload",
        }
    )
