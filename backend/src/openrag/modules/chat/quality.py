"""Transactional scheduling for content-free grounded-answer quality audits."""

from typing import Protocol

from openrag.modules.chat.models import Message
from openrag.modules.chat.quality_models import AnswerQualityAudit


class AddSession(Protocol):
    def add(self, instance: object) -> None: ...


def schedule_answer_quality_audit(
    session: AddSession,
    *,
    message: Message,
) -> AnswerQualityAudit:
    """Schedule exactly the immutable policy snapshot used to release an answer."""

    if (
        message.role != "assistant"
        or message.answer_status != "grounded"
        or message.parent_message_id is None
        or message.grounding_policy_id is None
        or message.grounding_policy_version is None
        or message.grounding_policy_version < 1
        or message.verifier_model_id is None
    ):
        raise ValueError("quality audit requires a grounded assistant policy snapshot")
    audit = AnswerQualityAudit(
        org_id=message.org_id,
        workspace_id=message.workspace_id,
        message_id=message.id,
        grounding_policy_id=message.grounding_policy_id,
        grounding_policy_version=message.grounding_policy_version,
        verifier_model_id=message.verifier_model_id,
    )
    session.add(audit)
    return audit
