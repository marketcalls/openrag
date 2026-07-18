import httpx

from openrag.modules.auth.models import User


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
