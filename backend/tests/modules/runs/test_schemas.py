from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from openrag.modules.runs.schemas import RunCreate, RunStatusOut


def test_run_create_requires_bounded_content_and_idempotency_key() -> None:
    request_id = uuid4()
    command = RunCreate(content="hello", client_request_id=request_id)
    assert command.client_request_id == request_id

    with pytest.raises(ValidationError):
        RunCreate(content="", client_request_id=request_id)
    with pytest.raises(ValidationError):
        RunCreate(content="x" * 32_001, client_request_id=request_id)


def test_run_status_contains_only_safe_operational_fields() -> None:
    values = {
        "run_id": uuid4(),
        "chat_id": uuid4(),
        "input_message_id": uuid4(),
        "assistant_message_id": None,
        "status": "running",
        "route": "rag",
        "error_code": None,
        "prompt_tokens": 12,
        "completion_tokens": 3,
        "accepted_at": datetime.now(UTC),
        "started_at": datetime.now(UTC),
        "first_token_at": None,
        "cancel_requested_at": None,
        "finished_at": None,
    }
    status = RunStatusOut.model_validate(values)

    assert "content" not in status.model_dump()
    assert "trace_id" not in status.model_dump()
    with pytest.raises(ValidationError):
        RunStatusOut.model_validate({**values, "status": "unknown"})
