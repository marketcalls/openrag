from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.audit.models import AuditEvent
from openrag.modules.auth.models import User
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


async def test_usage_meter_reports_real_user_tokens_and_quota(
    client: httpx.AsyncClient,
    chat_env: dict[str, Any],
    session: AsyncSession,
    seeded_user: User,
) -> None:
    headers = await auth(client, seeded_user.email)
    chat_id = await _create_chat(client, chat_env, headers)
    accepted = await client.post(
        f"/api/v1/chats/{chat_id}/runs",
        json={"content": "hello", "client_request_id": str(uuid4())},
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text
    run = await session.scalar(
        select(AgentRun).where(AgentRun.id == accepted.json()["run_id"])
    )
    assert run is not None
    run.prompt_tokens = 42
    run.completion_tokens = 7
    await session.commit()

    without_quota = await client.get("/api/v1/usage/me", headers=headers)
    assert without_quota.status_code == 200
    assert without_quota.json()["used_tokens"] == 49
    assert without_quota.json()["allocated_tokens"] is None
    assert without_quota.json()["blocked"] is False

    org_quota = await client.put(
        "/api/v1/usage/org/quota",
        json={
            "monthly_tokens": 1_000,
            "default_user_monthly_tokens": 100,
            "reset_day": 15,
        },
        headers=headers,
    )
    assert org_quota.status_code == 200, org_quota.text

    with_quota = await client.get("/api/v1/usage/me", headers=headers)
    assert with_quota.json()["used_tokens"] == 49
    assert with_quota.json()["allocated_tokens"] == 100
    assert with_quota.json()["org_used_tokens"] == 49
    assert with_quota.json()["org_allocated_tokens"] == 1_000


async def test_admin_can_override_user_quota_and_exhausted_user_is_blocked(
    client: httpx.AsyncClient,
    chat_env: dict[str, Any],
    session: AsyncSession,
    seeded_user: User,
) -> None:
    headers = await auth(client, seeded_user.email)
    chat_id = await _create_chat(client, chat_env, headers)
    accepted = await client.post(
        f"/api/v1/chats/{chat_id}/runs",
        json={"content": "first", "client_request_id": str(uuid4())},
        headers=headers,
    )
    run = await session.scalar(
        select(AgentRun).where(AgentRun.id == accepted.json()["run_id"])
    )
    assert run is not None
    run.prompt_tokens = 42
    run.completion_tokens = 7
    await session.commit()

    saved = await client.put(
        f"/api/v1/users/{seeded_user.id}/quota",
        json={"monthly_tokens": 49},
        headers=headers,
    )
    assert saved.status_code == 204, saved.text
    quota = await client.get(
        f"/api/v1/users/{seeded_user.id}/quota",
        headers=headers,
    )
    assert quota.status_code == 200
    assert quota.json()["monthly_tokens"] == 49
    assert quota.json()["used_tokens"] == 49
    assert quota.json()["allocated_tokens"] == 49

    blocked = await client.post(
        f"/api/v1/chats/{chat_id}/runs",
        json={"content": "second", "client_request_id": str(uuid4())},
        headers=headers,
    )
    assert blocked.status_code == 429
    assert blocked.json()["title"] == "Token quota exhausted"
    assert "resets" in blocked.json()["detail"]

    audits = (
        await session.execute(
            select(AuditEvent.action).where(
                AuditEvent.org_id == seeded_user.org_id,
                AuditEvent.target_id == str(seeded_user.id),
            )
        )
    ).scalars().all()
    assert "quota.user.updated" in audits


async def test_admin_cannot_read_or_change_another_organizations_user_quota(
    client: httpx.AsyncClient,
    session: AsyncSession,
    seeded_user: User,
    seeded_superadmin: User,
) -> None:
    headers = await auth(client, seeded_user.email)
    read = await client.get(
        f"/api/v1/users/{seeded_superadmin.id}/quota",
        headers=headers,
    )
    write = await client.put(
        f"/api/v1/users/{seeded_superadmin.id}/quota",
        json={"monthly_tokens": 1},
        headers=headers,
    )
    assert read.status_code == 404
    assert write.status_code == 404
    await session.rollback()
