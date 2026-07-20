"""Transactional scheduling boundary for branch summary work."""

from typing import Protocol

from openrag.modules.chat.models import Chat, Message
from openrag.modules.chat.summary_models import ConversationSummaryJob


class AddSession(Protocol):
    def add(self, instance: object) -> None: ...


def schedule_branch_summary(
    session: AddSession,
    *,
    chat: Chat,
    assistant_message: Message,
) -> ConversationSummaryJob:
    if assistant_message.role != "assistant" or assistant_message.chat_id != chat.id:
        raise ValueError("summary jobs require an assistant message in the same chat")
    job = ConversationSummaryJob(
        org_id=chat.org_id,
        workspace_id=chat.workspace_id,
        chat_id=chat.id,
        user_id=chat.user_id,
        branch_head_message_id=assistant_message.id,
        requested_model_id=assistant_message.model_id,
    )
    session.add(job)
    return job
