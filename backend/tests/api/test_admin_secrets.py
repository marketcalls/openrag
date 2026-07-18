import httpx

from openrag.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_write_and_list_never_expose_value(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
) -> None:
    headers = await auth(client, seeded_superadmin.email)

    written = await client.put(
        "/api/v1/admin/secrets/openai_key",
        json={"value": "sk-verysecret-abcd"},
        headers=headers,
    )

    assert written.status_code == 200
    assert "sk-verysecret" not in written.text
    assert written.json()["fingerprint"].startswith("...abcd sha256:")

    listing = await client.get("/api/v1/admin/secrets", headers=headers)
    assert listing.status_code == 200
    assert "sk-verysecret" not in listing.text
    assert [item["name"] for item in listing.json()] == ["openai_key"]
    assert listing.json()[0]["last_used_at"] is None


async def test_admin_role_is_denied(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    headers = await auth(client, seeded_user.email)

    listing = await client.get("/api/v1/admin/secrets", headers=headers)
    written = await client.put(
        "/api/v1/admin/secrets/provider_key",
        json={"value": "value"},
        headers=headers,
    )

    assert listing.status_code == 403
    assert written.status_code == 403
