import re
from concurrent.futures import ThreadPoolExecutor

from openrag.core.telemetry import (
    current_trace_id,
    new_trace_id,
    reset_trace_id,
    safe_log_fields,
    set_trace_id,
)


def test_recursive_redaction_bounds_nested_values() -> None:
    value = {
        "authorization": "Bearer secret",
        "nested": {
            "prompt": "private",
            "safe_code": "provider.timeout",
            "items": list(range(100)),
        },
        "long_value": "x" * 1000,
        "binary": b"secret bytes",
    }

    redacted = safe_log_fields(value)

    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["nested"]["prompt"] == "[REDACTED]"  # type: ignore[index]
    assert redacted["nested"]["safe_code"] == "provider.timeout"  # type: ignore[index]
    assert len(redacted["nested"]["items"]) == 51  # type: ignore[index,arg-type]
    assert redacted["nested"]["items"][-1] == "[TRUNCATED]"  # type: ignore[index]
    assert redacted["long_value"].endswith("[TRUNCATED]")  # type: ignore[union-attr]
    assert redacted["binary"] == "[BINARY:12]"


def test_exceptions_are_reduced_to_type_without_message() -> None:
    result = safe_log_fields({"error": RuntimeError("customer secret")})

    assert result == {"error": {"exception_type": "RuntimeError"}}
    assert "customer secret" not in str(result)


def test_trace_context_validates_and_resets() -> None:
    token = set_trace_id("a" * 32)
    try:
        assert current_trace_id() == "a" * 32
    finally:
        reset_trace_id(token)

    generated = new_trace_id("../../invalid")
    assert re.fullmatch(r"[0-9a-f]{32}", generated)
    assert generated != "../../invalid"


def test_trace_context_is_isolated_between_execution_contexts() -> None:
    def capture() -> str:
        trace_id = new_trace_id(None)
        token = set_trace_id(trace_id)
        try:
            return current_trace_id()
        finally:
            reset_trace_id(token)

    with ThreadPoolExecutor(max_workers=4) as executor:
        values = set(executor.map(lambda _item: capture(), range(20)))

    assert len(values) == 20
