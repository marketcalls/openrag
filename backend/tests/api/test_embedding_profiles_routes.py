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
    "api_key": "sk-embedding-secret",
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
    assert created["key_fingerprint"].startswith("...cret sha256:")
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


async def test_superadmin_requests_one_safe_embedding_generation(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
) -> None:
    headers = await auth(client, seeded_superadmin.email)
    profile = (
        await client.post(
            "/api/v1/admin/embedding-profiles",
            json=PROFILE,
            headers=headers,
        )
    ).json()

    requested = await client.post(
        "/api/v1/admin/embedding-deployments",
        json={"profile_id": profile["id"]},
        headers=headers,
    )

    assert requested.status_code == 202
    deployment = requested.json()
    assert deployment["status"] == "building"
    assert deployment["profile_id"] == profile["id"]
    assert deployment["generation_id"]
    assert deployment["total_versions"] == 0
    assert deployment["scan_complete"] is False

    duplicate = await client.post(
        "/api/v1/admin/embedding-deployments",
        json={"profile_id": profile["id"]},
        headers=headers,
    )
    assert duplicate.status_code == 409

    disabled = await client.patch(
        f"/api/v1/admin/embedding-profiles/{profile['id']}",
        json={"enabled": False},
        headers=headers,
    )
    assert disabled.status_code == 409

    listing = await client.get(
        "/api/v1/admin/embedding-deployments",
        headers=headers,
    )
    assert [row["id"] for row in listing.json()] == [deployment["id"]]


async def test_deployment_generation_identity_is_server_authoritative(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
) -> None:
    headers = await auth(client, seeded_superadmin.email)

    response = await client.post(
        "/api/v1/admin/embedding-deployments",
        json={
            "profile_id": "42be9246-631d-4a84-b669-a48953550895",
            "generation_id": "566e45b0-051c-4d86-87b3-6a528c7935c2",
        },
        headers=headers,
    )

    assert response.status_code == 422


async def test_embedding_activation_is_platform_superadmin_only(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    headers = await auth(client, seeded_user.email)

    response = await client.post(
        "/api/v1/admin/embedding-deployments/"
        "4bd2a478-9a6a-4e9d-8385-766a4fbed7ee/activate",
        headers=headers,
    )

    assert response.status_code == 403


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

    deployment_denied = await client.post(
        "/api/v1/admin/embedding-deployments",
        json={"profile_id": "42be9246-631d-4a84-b669-a48953550895"},
        headers=headers,
    )
    assert deployment_denied.status_code == 403
