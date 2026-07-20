from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.chat.models import Message
from openrag.modules.events.models import OutboxEvent
from openrag.modules.runs.models import AgentRun
from tests.api.test_chat_stream import auth


async def _create_chat(
    client: httpx.AsyncClient,
    chat_env: dict[str, Any],
    headers: dict[str, str],
) -> str:
    response = await client.post(
        "/api/v1/chats",
        json={"workspace_id": str(chat_env["workspace"].id)},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def test_run_acceptance_is_idempotent_and_cancellable(
    client: httpx.AsyncClient,
    chat_env: dict[str, Any],
    session: AsyncSession,
    seeded_user: User,
) -> None:
    headers = await auth(client, seeded_user.email)
    chat_id = await _create_chat(client, chat_env, headers)
    request_id = str(uuid4())
    body = {"content": "hello", "client_request_id": request_id}

    first = await client.post(
        f"/api/v1/chats/{chat_id}/runs",
        json=body,
        headers=headers,
    )
    second = await client.post(
        f"/api/v1/chats/{chat_id}/runs",
        json=body,
        headers=headers,
    )

    assert first.status_code == second.status_code == 202
    assert first.json()["run_id"] == second.json()["run_id"]
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["events_url"].endswith("/events")

    run_id = first.json()["run_id"]
    status = await client.get(f"/api/v1/runs/{run_id}", headers=headers)
    assert status.status_code == 200
    assert status.json()["status"] == "accepted"
    assert "content" not in status.json()
    assert "trace_id" not in status.json()

    first_cancel = await client.post(
        f"/api/v1/runs/{run_id}/cancel",
        headers=headers,
    )
    second_cancel = await client.post(
        f"/api/v1/runs/{run_id}/cancel",
        headers=headers,
    )
    assert first_cancel.status_code == second_cancel.status_code == 202
    assert first_cancel.json()["cancel_requested_at"] is not None
    assert (
        second_cancel.json()["cancel_requested_at"]
        == first_cancel.json()["cancel_requested_at"]
    )

    assert await session.scalar(select(func.count()).select_from(Message)) == 1
    assert await session.scalar(select(func.count()).select_from(AgentRun)) == 1
    assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 2
