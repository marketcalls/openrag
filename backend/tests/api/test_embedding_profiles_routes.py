import httpx

from openrag.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


PROFILE = {
    "name": "Production BGE",
    "provider_kind": "litellm",
    "model_name": "huggingface/BAAI/bge-m3",
    "dimension": 1024,
    "max_input_tokens": 8192,
    "batch_size": 32,
}


async def test_superadmin_creates_lists_and_disables_immutable_profile(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
) -> None:
    headers = await auth(client, seeded_superadmin.email)

    created_response = await client.post(
        "/api/v1/admin/embedding-profiles",
        json=PROFILE,
        headers=headers,
    )

    assert created_response.status_code == 201
    created = created_response.json()
    assert created["config_digest"] and len(created["config_digest"]) == 64
    assert "api_key" not in created_response.text
    profile_id = created["id"]

    immutable = await client.patch(
        f"/api/v1/admin/embedding-profiles/{profile_id}",
        json={"dimension": 768},
        headers=headers,
    )
    assert immutable.status_code == 422

    disabled = await client.patch(
        f"/api/v1/admin/embedding-profiles/{profile_id}",
        json={"name": "BGE archived", "enabled": False},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    listing = await client.get(
        "/api/v1/admin/embedding-profiles",
        headers=headers,
    )
    assert [row["id"] for row in listing.json()] == [profile_id]


async def test_embedding_profile_management_is_platform_superadmin_only(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    headers = await auth(client, seeded_user.email)

    denied = await client.post(
        "/api/v1/admin/embedding-profiles",
        json=PROFILE,
        headers=headers,
    )

    assert denied.status_code == 403
