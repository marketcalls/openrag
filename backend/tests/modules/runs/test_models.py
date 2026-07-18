from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.chat.models import Chat, Message
from openrag.modules.events.models import InboxEvent, OutboxEvent
from openrag.modules.runs.models import AgentRun


@pytest.fixture
async def run_env(
    session: AsyncSession,
    chat_env: dict[str, Any],
    seeded_user: User,
) -> dict[str, Any]:
    workspace = chat_env["workspace"]
    chat = Chat(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        user_id=seeded_user.id,
    )
    session.add(chat)
    await session.flush()
    user_message = Message(
        chat_id=chat.id,
        parent_message_id=None,
        sibling_index=0,
        role="user",
        content="hello",
    )
    session.add(user_message)
    await session.commit()
    return {
        "user": seeded_user,
        "workspace": workspace,
        "chat": chat,
        "user_message": user_message,
    }


async def test_agent_run_defaults_to_accepted(
    session: AsyncSession,
    run_env: dict[str, Any],
) -> None:
    run = AgentRun(
        org_id=run_env["user"].org_id,
        workspace_id=run_env["workspace"].id,
        user_id=run_env["user"].id,
        chat_id=run_env["chat"].id,
        input_message_id=run_env["user_message"].id,
        client_request_id=uuid4(),
    )
    session.add(run)
    await session.commit()
    assert run.status == "accepted"
    assert run.cancel_requested_at is None
    assert run.finished_at is None


async def test_agent_run_idempotency_is_enforced(
    session: AsyncSession,
    run_env: dict[str, Any],
) -> None:
    request_id = uuid4()
    values = {
        "org_id": run_env["user"].org_id,
        "workspace_id": run_env["workspace"].id,
        "user_id": run_env["user"].id,
        "chat_id": run_env["chat"].id,
        "input_message_id": run_env["user_message"].id,
        "client_request_id": request_id,
    }
    session.add_all([AgentRun(**values), AgentRun(**values)])
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_outbox_and_inbox_dedupe_keys_are_unique(
    session: AsyncSession,
) -> None:
    event_id = uuid4()
    outbox = OutboxEvent(
        event_id=event_id,
        aggregate_type="agent_run",
        aggregate_id=uuid4(),
        event_type="run.requested.v1",
        payload={"run_id": str(uuid4())},
        dedupe_key=f"run.requested:{event_id}",
    )
    session.add(outbox)
    await session.commit()
    session.add_all(
        [
            InboxEvent(consumer="agent-runner", event_id=event_id),
            InboxEvent(consumer="agent-runner", event_id=event_id),
        ]
    )
    with pytest.raises(IntegrityError):
        await session.commit()
