from collections.abc import AsyncIterator

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from openrag.api.app import create_app
from openrag.core.db import build_session_factory
from openrag.modules.auth.models import User


@pytest.fixture
async def crashy_client(
    engine: AsyncEngine,
    redis_client: Redis,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
    )

    @app.get("/probe/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom internal secret detail")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_catch_all_returns_generic_problem_json(
    crashy_client: httpx.AsyncClient,
) -> None:
    response = await crashy_client.get("/probe/boom")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["title"] == "Internal error"
    assert "kaboom" not in response.text


async def test_integrity_error_maps_to_409(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    member = User(
        org_id=seeded_user.org_id,
        email="member@acme.com",
        password_hash=seeded_user.password_hash,
        role="user",
    )
    session.add(member)
    await session.commit()

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "pw123456"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    workspace = await client.post(
        "/api/v1/workspaces",
        json={"name": "Conflict probe"},
        headers=headers,
    )
    workspace_id = workspace.json()["id"]
    body = {"user_id": str(member.id)}

    first = await client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json=body,
        headers=headers,
    )
    second = await client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json=body,
        headers=headers,
    )

    assert first.status_code == 204
    assert second.status_code == 409
    assert second.headers["content-type"].startswith("application/problem+json")
