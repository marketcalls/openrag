import httpx

from openrag.modules.auth.models import User


async def test_login_rate_limited(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    for _ in range(10):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": seeded_user.email, "password": "bad"},
        )
        assert response.status_code == 401

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "pw123456"},
    )

    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/problem+json")
