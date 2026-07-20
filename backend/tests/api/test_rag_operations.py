from datetime import UTC, datetime, timedelta

import httpx

from openrag.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_non_platform_admin_cannot_read_rag_operations(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    headers = await auth(client, seeded_user.email)
    now = datetime.now(UTC)

    response = await client.get(
        "/api/v1/admin/rag-operations/overview",
        params={"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()},
        headers=headers,
    )

    assert response.status_code == 403


async def test_superadmin_reads_empty_bounded_overview(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
) -> None:
    headers = await auth(client, seeded_superadmin.email)
    now = datetime.now(UTC)

    response = await client.get(
        "/api/v1/admin/rag-operations/overview",
        params={"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "query_count": 0,
        "grounded_count": 0,
        "no_answer_count": 0,
        "failed_count": 0,
        "cancelled_count": 0,
        "grounded_rate": 0.0,
        "no_answer_rate": 0.0,
        "p50_latency_ms": None,
        "p95_latency_ms": None,
        "p99_latency_ms": None,
        "average_ttft_ms": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "estimated_cost_microusd": 0,
    }
