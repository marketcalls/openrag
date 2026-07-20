from uuid import uuid4

import pytest

from openrag.modules.chat.models import Chat, Message
from openrag.modules.chat.summaries import schedule_branch_summary


class AddOnlySession:
    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, item: object) -> None:
        self.items.append(item)


def test_summary_job_is_branch_and_tenant_bound() -> None:
    chat = Chat(
        id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
    )
    assistant = Message(
        id=uuid4(),
        org_id=chat.org_id,
        workspace_id=chat.workspace_id,
        chat_id=chat.id,
        role="assistant",
        content="answer",
        model_id=uuid4(),
    )
    session = AddOnlySession()

    job = schedule_branch_summary(
        session,
        chat=chat,
        assistant_message=assistant,
    )

    assert session.items == [job]
    assert (job.org_id, job.workspace_id, job.chat_id, job.user_id) == (
        chat.org_id,
        chat.workspace_id,
        chat.id,
        chat.user_id,
    )
    assert job.branch_head_message_id == assistant.id
    assert job.requested_model_id == assistant.model_id


def test_summary_job_rejects_non_assistant_or_cross_chat_message() -> None:
    chat = Chat(
        id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
    )
    message = Message(
        id=uuid4(),
        org_id=chat.org_id,
        workspace_id=chat.workspace_id,
        chat_id=uuid4(),
        role="user",
        content="question",
    )

    with pytest.raises(ValueError, match="assistant"):
        schedule_branch_summary(
            AddOnlySession(),
            chat=chat,
            assistant_message=message,
        )
