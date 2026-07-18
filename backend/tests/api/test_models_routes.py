from uuid import uuid4

import httpx

from openrag.modules.auth.models import User


async def auth(
    client: httpx.AsyncClient,
    email: str,
) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


OPENAI_BODY = {
    "litellm_model_name": "gpt-4o-mini",
    "display_name": "GPT-4o mini",
    "provider_kind": "openai",
    "api_key": "sk-live-abc",
}


async def test_superadmin_crud_and_key_never_returned(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
) -> None:
    headers = await auth(client, seeded_superadmin.email)

    response = await client.post(
        "/api/v1/admin/models",
        json=OPENAI_BODY,
        headers=headers,
    )
    assert response.status_code == 201
    assert "sk-live-abc" not in response.text
    created = response.json()
    model_id = created["id"]
    assert created["key_fingerprint"].startswith("...-abc sha256:")
    assert created["sync_status"] == "synced"

    response = await client.patch(
        f"/api/v1/admin/models/{model_id}",
        json={"enabled": False},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False

    listing = await client.get("/api/v1/admin/models", headers=headers)
    assert "sk-live-abc" not in listing.text
    assert [model["id"] for model in listing.json()] == [model_id]

    deleted = await client.delete(
        f"/api/v1/admin/models/{model_id}",
        headers=headers,
    )
    assert deleted.status_code == 204
    assert (
        await client.get("/api/v1/admin/models", headers=headers)
    ).json() == []


async def test_admin_role_denied_but_can_list_public(
    client: httpx.AsyncClient,
    seeded_user: User,
    seeded_superadmin: User,
) -> None:
    super_headers = await auth(client, seeded_superadmin.email)
    response = await client.post(
        "/api/v1/admin/models",
        json=OPENAI_BODY,
        headers=super_headers,
    )
    model_id = response.json()["id"]
    await client.post(
        "/api/v1/admin/models",
        json={
            "litellm_model_name": "llama3",
            "display_name": "Llama",
            "provider_kind": "ollama",
            "base_url": "http://ollama:11434",
        },
        headers=super_headers,
    )
    await client.patch(
        f"/api/v1/admin/models/{model_id}",
        json={"enabled": False},
        headers=super_headers,
    )

    admin_listing = (
        await client.get("/api/v1/admin/models", headers=super_headers)
    ).json()
    llama = next(
        model for model in admin_listing if model["display_name"] == "Llama"
    )
    assert llama["key_fingerprint"] is None

    admin_headers = await auth(client, seeded_user.email)
    denied = await client.post(
        "/api/v1/admin/models",
        json=OPENAI_BODY,
        headers=admin_headers,
    )
    assert denied.status_code == 403

    public = await client.get("/api/v1/models", headers=admin_headers)
    assert public.status_code == 200
    assert [model["display_name"] for model in public.json()] == ["Llama"]
    assert "litellm_model_name" not in public.text


async def test_workspace_default_model(
    client: httpx.AsyncClient,
    seeded_user: User,
    seeded_superadmin: User,
) -> None:
    super_headers = await auth(client, seeded_superadmin.email)
    response = await client.post(
        "/api/v1/admin/models",
        json={
            "litellm_model_name": "llama3",
            "display_name": "Llama",
            "provider_kind": "ollama",
            "base_url": "http://ollama:11434",
        },
        headers=super_headers,
    )
    model_id = response.json()["id"]

    admin_headers = await auth(client, seeded_user.email)
    workspace = await client.post(
        "/api/v1/workspaces",
        json={"name": "Finance"},
        headers=admin_headers,
    )
    workspace_id = workspace.json()["id"]

    response = await client.patch(
        f"/api/v1/workspaces/{workspace_id}",
        json={"default_model_id": model_id},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["default_model_id"] == model_id

    response = await client.patch(
        f"/api/v1/workspaces/{workspace_id}",
        json={"default_model_id": str(uuid4())},
        headers=admin_headers,
    )
    assert response.status_code == 404
