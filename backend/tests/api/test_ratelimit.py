import asyncio

import httpx
import pytest
from redis.asyncio import Redis

from openrag.core.errors import RateLimitExceeded
from openrag.core.ratelimit import check_rate_limit
from openrag.modules.auth.models import User


async def test_check_rate_limit_blocks_then_window_resets(
    redis_client: Redis,
) -> None:
    for _ in range(2):
        await check_rate_limit(
            redis_client,
            "rl:test:1",
            limit=2,
            window_seconds=1,
        )
    with pytest.raises(RateLimitExceeded):
        await check_rate_limit(
            redis_client,
            "rl:test:1",
            limit=2,
            window_seconds=1,
        )
    await asyncio.sleep(1.1)
    await check_rate_limit(
        redis_client,
        "rl:test:1",
        limit=2,
        window_seconds=1,
    )


async def test_login_rate_limited(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    for _ in range(10):
        await client.post(
            "/api/v1/auth/login",
            json={"email": seeded_user.email, "password": "bad"},
        )
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "pw123456"},
    )

    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_refresh_rate_limited(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "pw123456"},
    )
    for _ in range(30):
        await client.post("/api/v1/auth/refresh")

    response = await client.post("/api/v1/auth/refresh")

    assert response.status_code == 429


async def test_invitation_accept_rate_limited(
    client: httpx.AsyncClient,
) -> None:
    request = {"token": "bogus", "password": "irrelevant1"}
    for _ in range(10):
        await client.post("/api/v1/auth/invitations/accept", json=request)

    response = await client.post(
        "/api/v1/auth/invitations/accept",
        json=request,
    )

    assert response.status_code == 429
