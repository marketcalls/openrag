import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from openrag.modules.runs.events import RunEventEnvelope, encode_sse, new_run_event


def test_run_event_encodes_replayable_sse() -> None:
    event = new_run_event(
        sequence=7,
        event_type="message.delta",
        run_id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        chat_id=uuid4(),
        payload={"delta": "hello"},
    )
    encoded = encode_sse(event)
    lines = encoded.strip().splitlines()
    assert lines[0] == f"id: {event.event_id}"
    assert lines[1] == "event: message.delta"
    assert json.loads(lines[2].removeprefix("data: "))["sequence"] == 7


def test_unknown_event_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RunEventEnvelope(
            event_id=uuid4(),
            sequence=1,
            event_type="reasoning.secret",
            run_id=uuid4(),
            org_id=uuid4(),
            workspace_id=uuid4(),
            chat_id=uuid4(),
            occurred_at="2026-07-18T00:00:00Z",
            payload={},
        )


def test_payload_limit_is_enforced() -> None:
    with pytest.raises(ValueError, match="event payload exceeds"):
        new_run_event(
            sequence=1,
            event_type="message.delta",
            run_id=uuid4(),
            org_id=uuid4(),
            workspace_id=uuid4(),
            chat_id=uuid4(),
            payload={"delta": "x" * 70_000},
        )
