from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from openrag.api.app import create_app
from openrag.core.db import build_session_factory
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.tenancy.models import Organization


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(session_factory=build_session_factory(engine))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as http_client:
        yield http_client


@pytest.fixture
async def seeded_user(session: AsyncSession) -> User:
    organization = Organization(name="Acme")
    session.add(organization)
    await session.flush()
    user = User(
        org_id=organization.id,
        email="a@acme.com",
        password_hash=hash_password("pw123456"),
        role="admin",
    )
    session.add(user)
    await session.commit()
    return user


async def test_login_ok(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "a@acme.com", "password": "pw123456"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert "refresh_token" in response.cookies


async def test_login_bad_password_problem_json(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "a@acme.com", "password": "bad"},
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["title"] == "Authentication failed"


async def test_refresh_and_logout(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "a@acme.com", "password": "pw123456"},
    )
    refresh_response = await client.post("/api/v1/auth/refresh")
    assert refresh_response.status_code == 200
    assert (
        refresh_response.cookies["refresh_token"]
        != login_response.cookies["refresh_token"]
    )

    logout_response = await client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 204
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401
