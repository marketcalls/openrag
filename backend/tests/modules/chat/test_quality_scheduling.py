from uuid import UUID

import pytest

from openrag.modules.chat.models import Message
from openrag.modules.chat.quality import schedule_answer_quality_audit
from openrag.modules.chat.quality_models import AnswerQualityAudit


class RecordingSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, instance: object) -> None:
        self.added.append(instance)


def _grounded_message() -> Message:
    return Message(
        id=UUID(int=1),
        org_id=UUID(int=2),
        workspace_id=UUID(int=3),
        chat_id=UUID(int=4),
        parent_message_id=UUID(int=5),
        role="assistant",
        content="Grounded answer [1].",
        answer_status="grounded",
        grounding_policy_id=UUID(int=6),
        grounding_policy_version=3,
        verifier_model_id=UUID(int=7),
    )


def test_grounded_answer_schedules_one_content_free_quality_audit() -> None:
    session = RecordingSession()
    message = _grounded_message()

    audit = schedule_answer_quality_audit(session, message=message)

    assert session.added == [audit]
    assert isinstance(audit, AnswerQualityAudit)
    assert audit.org_id == message.org_id
    assert audit.workspace_id == message.workspace_id
    assert audit.message_id == message.id
    assert audit.grounding_policy_id == message.grounding_policy_id
    assert audit.grounding_policy_version == message.grounding_policy_version
    assert audit.verifier_model_id == message.verifier_model_id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", "user"),
        ("answer_status", "refused"),
        ("parent_message_id", None),
        ("grounding_policy_id", None),
        ("grounding_policy_version", None),
        ("verifier_model_id", None),
    ],
)
def test_quality_audit_rejects_non_grounded_or_incomplete_snapshot(
    field: str,
    value: object,
) -> None:
    session = RecordingSession()
    message = _grounded_message()
    setattr(message, field, value)

    with pytest.raises(ValueError, match="quality audit requires"):
        schedule_answer_quality_audit(session, message=message)

    assert session.added == []
