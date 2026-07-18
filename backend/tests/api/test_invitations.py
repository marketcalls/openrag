import httpx

from openrag.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_invite_flow(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    headers = await auth(client, "a@acme.com")
    response = await client.post(
        "/api/v1/auth/invitations",
        json={"email": "new@acme.com", "role": "user"},
        headers=headers,
    )
    assert response.status_code == 201
    token = response.json()["invite_token"]

    accept_response = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "newpw12345"},
    )
    assert accept_response.status_code == 201

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "new@acme.com", "password": "newpw12345"},
    )
    assert login_response.status_code == 200

    reuse_response = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "other12345"},
    )
    assert reuse_response.status_code == 401


async def test_invite_requires_admin(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    response = await client.post(
        "/api/v1/auth/invitations",
        json={"email": "x@x.com", "role": "user"},
    )
    assert response.status_code == 401
