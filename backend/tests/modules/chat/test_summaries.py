from uuid import uuid4

import pytest

from openrag.modules.chat.models import Chat, Message
from openrag.modules.chat.summaries import (
    choose_branch_summary,
    history_after_summary,
    schedule_branch_summary,
)
from openrag.modules.chat.summary_models import ConversationBranchSummary


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


def test_summary_selection_is_branch_bound_and_trims_only_covered_prefix() -> None:
    chat_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    root = Message(
        id=uuid4(),
        org_id=org_id,
        workspace_id=workspace_id,
        chat_id=chat_id,
        role="user",
        content="root",
    )
    answer = Message(
        id=uuid4(),
        org_id=org_id,
        workspace_id=workspace_id,
        chat_id=chat_id,
        parent_message_id=root.id,
        role="assistant",
        content="answer",
    )
    latest = Message(
        id=uuid4(),
        org_id=org_id,
        workspace_id=workspace_id,
        chat_id=chat_id,
        parent_message_id=answer.id,
        role="user",
        content="latest",
    )
    ancestor = ConversationBranchSummary(
        org_id=org_id,
        workspace_id=workspace_id,
        chat_id=chat_id,
        user_id=user_id,
        branch_head_message_id=answer.id,
        covers_through_message_id=root.id,
        content="root summary",
        source_message_count=1,
        source_digest="a" * 64,
        summary_tokens=3,
        model_id=uuid4(),
        prompt_contract_version="summary-v1",
        status="active",
    )
    sibling = ConversationBranchSummary(
        org_id=org_id,
        workspace_id=workspace_id,
        chat_id=chat_id,
        user_id=user_id,
        branch_head_message_id=uuid4(),
        covers_through_message_id=uuid4(),
        content="sibling secret",
        source_message_count=99,
        source_digest="b" * 64,
        summary_tokens=3,
        model_id=uuid4(),
        prompt_contract_version="summary-v1",
        status="active",
    )
    branch = [root, answer, latest]

    selected = choose_branch_summary([sibling, ancestor], branch)

    assert selected is ancestor
    assert history_after_summary(branch, selected) == [answer, latest]


def test_invalid_coverage_is_not_applied_to_branch_history() -> None:
    message = Message(
        id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        chat_id=uuid4(),
        role="user",
        content="only message",
    )
    summary = ConversationBranchSummary(
        org_id=message.org_id,
        workspace_id=message.workspace_id,
        chat_id=message.chat_id,
        user_id=uuid4(),
        branch_head_message_id=message.id,
        covers_through_message_id=uuid4(),
        content="invalid",
        source_message_count=1,
        source_digest="c" * 64,
        summary_tokens=2,
        model_id=uuid4(),
        prompt_contract_version="summary-v1",
        status="active",
    )

    assert choose_branch_summary([summary], [message]) is None
    assert history_after_summary([message], summary) == [message]
