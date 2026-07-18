from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User


@pytest.fixture
def captured_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[tuple[Any, ...]]]:
    calls: dict[str, list[tuple[Any, ...]]] = {"ingest": [], "delete": []}
    monkeypatch.setattr(
        "openrag.api.routes.documents.enqueue_ingest",
        lambda document_id, size: calls["ingest"].append((document_id, size)),
    )
    monkeypatch.setattr(
        "openrag.api.routes.documents.enqueue_delete",
        lambda document_id, actor_id: calls["delete"].append(
            (document_id, actor_id)
        ),
    )
    return calls


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def make_workspace(
    client: httpx.AsyncClient,
    headers: dict[str, str],
) -> str:
    response = await client.post(
        "/api/v1/workspaces",
        json={"name": "Documents"},
        headers=headers,
    )
    return str(response.json()["id"])


async def test_upload_list_delete_flow(
    client: httpx.AsyncClient,
    seeded_user: User,
    stack_env: None,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)

    upload = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
        files={
            "file": (
                "notes.txt",
                b"the flux capacitor hums",
                "text/plain",
            )
        },
    )

    assert upload.status_code == 201
    body = upload.json()
    assert body["status"] == "queued"
    assert body["filename"] == "notes.txt"
    assert len(captured_enqueues["ingest"]) == 1

    listing = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
    )
    assert [document["id"] for document in listing.json()] == [body["id"]]

    duplicate = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
        files={"file": ("copy.txt", b"the flux capacitor hums", "text/plain")},
    )
    assert duplicate.status_code == 409

    deletion = await client.delete(
        f"/api/v1/documents/{body['id']}",
        headers=headers,
    )
    assert deletion.status_code == 202
    assert len(captured_enqueues["delete"]) == 1


async def test_non_member_user_gets_403(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    plain_user = User(
        org_id=seeded_user.org_id,
        email="plain@acme.com",
        password_hash=seeded_user.password_hash,
        role="user",
    )
    session.add(plain_user)
    await session.commit()
    admin_headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, admin_headers)
    user_headers = await auth(client, plain_user.email)

    upload = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=user_headers,
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    listing = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=user_headers,
    )

    assert upload.status_code == 403
    assert listing.status_code == 403


async def test_delete_unknown_document_returns_404(
    client: httpx.AsyncClient,
    seeded_user: User,
    stack_env: None,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    headers = await auth(client, seeded_user.email)

    response = await client.delete(
        "/api/v1/documents/00000000-0000-0000-0000-000000000000",
        headers=headers,
    )

    assert response.status_code == 404
    assert captured_enqueues["delete"] == []


async def test_oversized_upload_returns_413(
    client: httpx.AsyncClient,
    seeded_user: User,
    stack_env: None,
    monkeypatch: pytest.MonkeyPatch,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    from openrag.core.config import get_settings

    monkeypatch.setenv("OPENRAG_MAX_UPLOAD_MB", "0")
    get_settings.cache_clear()
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
        files={"file": ("big.txt", b"too big for zero", "text/plain")},
    )

    assert response.status_code == 413
    get_settings.cache_clear()
