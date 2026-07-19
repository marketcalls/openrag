import hashlib
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.documents.models import Document, DocumentVersion


@pytest.fixture
def captured_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[tuple[Any, ...]]]:
    calls: dict[str, list[tuple[Any, ...]]] = {"ingest": [], "delete": []}
    monkeypatch.setattr(
        "openrag.api.routes.documents.enqueue_ingest",
        lambda document_id, size, revision: calls["ingest"].append((document_id, size, revision)),
    )
    monkeypatch.setattr(
        "openrag.api.routes.documents.enqueue_delete",
        lambda document_id, actor_id: calls["delete"].append((document_id, actor_id)),
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
    session: AsyncSession,
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
    assert "error" not in body
    assert body["error_code"] is None
    assert "storage_key" not in body
    assert "content_hash" not in body
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

    version = (
        await session.execute(select(DocumentVersion).where(DocumentVersion.id == body["id"]))
    ).scalar_one()
    version.state = "failed"
    await session.commit()

    deletion = await client.delete(
        f"/api/v1/documents/{body['id']}",
        headers=headers,
    )
    assert deletion.status_code == 202
    assert len(captured_enqueues["delete"]) == 1
    await session.refresh(version)
    assert version.source_delete_requested_at is not None
    assert version.source_deleted_at is None


async def test_document_detail_patch_is_strict_bounded_and_safe(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)
    upload = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
        files={"file": ("source.txt", b"safe source", "text/plain")},
    )
    document_id = upload.json()["id"]
    document = await session.get(Document, UUID(document_id))
    assert document is not None
    document.status = "failed"
    document.error = "SENTINEL parser traceback password=do-not-return"
    await session.commit()

    patched = await client.patch(
        f"/api/v1/documents/{document_id}",
        headers=headers,
        json={
            "name": "Controlled safety manual",
            "department": "HSE",
            "document_type": "Manual",
            "external_identifier": "HSE-MAN-001",
        },
    )
    detail = await client.get(f"/api/v1/documents/{document_id}", headers=headers)

    assert patched.status_code == 200
    assert detail.status_code == 200
    assert patched.json() == detail.json()
    assert patched.json()["name"] == "Controlled safety manual"
    assert patched.json()["error_code"] == "processing_failed"
    assert "SENTINEL" not in repr(patched.json())
    assert {
        "id",
        "workspace_id",
        "name",
        "department",
        "document_type",
        "external_identifier",
        "filename",
        "mime",
        "size_bytes",
        "status",
        "page_count",
        "error_code",
        "created_at",
        "updated_at",
    } == set(patched.json())
    assert "storage_key" not in repr(patched.json())
    assert "content_hash" not in repr(patched.json())
    assert "created_by" not in repr(patched.json())

    for payload in (
        {"name": "   "},
        {"name": "x" * 256},
        {"department": "x" * 121},
        {"document_type": "x" * 121},
        {"external_identifier": "x" * 256},
        {"workspace_id": workspace_id},
        {"storage_key": "private/key"},
        {"created_by": str(seeded_user.id)},
        {"sequence": 9},
    ):
        rejected = await client.patch(
            f"/api/v1/documents/{document_id}", headers=headers, json=payload
        )
        assert rejected.status_code == 422


async def test_document_list_has_deterministic_created_id_desc_order(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)
    ids: list[UUID] = []
    for filename, content in (("a.txt", b"a"), ("b.txt", b"b")):
        response = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            headers=headers,
            files={"file": (filename, content, "text/plain")},
        )
        ids.append(UUID(response.json()["id"]))
    documents = list(
        (await session.execute(select(Document).where(Document.id.in_(ids)))).scalars()
    )
    tied = datetime(2026, 7, 19, 8, 30)
    for document in documents:
        document.created_at = tied
    await session.commit()

    listing = await client.get(f"/api/v1/workspaces/{workspace_id}/documents", headers=headers)

    assert listing.status_code == 200
    assert [UUID(row["id"]) for row in listing.json()] == sorted(ids, reverse=True)


async def test_legacy_upload_rejects_client_sequence_form_field(
    client: httpx.AsyncClient,
    seeded_user: User,
    stack_env: None,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
        files={"file": ("unsafe.txt", b"unsafe", "text/plain")},
        data={"sequence": "99"},
    )

    assert response.status_code == 422
    assert captured_enqueues["ingest"] == []


async def test_delete_processing_document_conflicts_without_enqueue(
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
        files={"file": ("live.txt", b"still processing", "text/plain")},
    )

    response = await client.delete(f"/api/v1/documents/{upload.json()['id']}", headers=headers)

    assert response.status_code == 409
    assert captured_enqueues["delete"] == []


async def test_legacy_delete_route_refuses_ambiguous_nonlegacy_version_identity(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)
    document = Document(
        org_id=seeded_user.org_id,
        workspace_id=UUID(workspace_id),
        name="Versioned document",
        created_by=seeded_user.id,
    )
    session.add(document)
    await session.flush()
    version = DocumentVersion(
        org_id=seeded_user.org_id,
        workspace_id=UUID(workspace_id),
        document_id=document.id,
        sequence=1,
        version_label="Rev 1",
        version_key="rev 1",
        content_hash=hashlib.sha256(b"versioned").hexdigest(),
        source_filename="versioned.pdf",
        source_mime="application/pdf",
        source_size_bytes=9,
        source_storage_key="versioned/source",
        parser_profile_version="docling/v1",
        ocr_profile_version="none/v1",
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
        index_profile_version="hybrid/v1",
        state="failed",
        provenance_state="failed",
        created_by=seeded_user.id,
    )
    session.add(version)
    await session.commit()
    assert version.id != document.id

    response = await client.delete(f"/api/v1/documents/{document.id}", headers=headers)

    assert response.status_code == 409
    assert captured_enqueues["delete"] == []
    await session.refresh(version)
    assert version.source_delete_requested_at is None


async def test_non_member_user_gets_403_for_workspace_collection(
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


async def test_legacy_retry_commits_transition_and_enqueues_once(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)
    upload = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
        files={"file": ("retry.txt", b"retry source", "text/plain")},
    )
    version_id = UUID(upload.json()["id"])
    version = await session.get(DocumentVersion, version_id)
    assert version is not None
    version.state = "failed"
    version.provenance_state = "none"
    version.processing_error_code = "parser_failed"
    await session.commit()
    initial_revision = version.lifecycle_revision

    response = await client.post(
        f"/api/v1/document-versions/{version_id}/retry",
        headers=headers,
    )

    assert response.status_code == 202
    assert response.json() == {"status": "retry scheduled"}
    assert captured_enqueues["ingest"] == [
        (version_id, len(b"retry source"), 1),
        (version_id, len(b"retry source"), initial_revision + 1),
    ]
    await session.refresh(version)
    assert (version.state, version.provenance_state) == ("processing", "none")
    assert version.processing_error_code is None
    assert version.lifecycle_revision == initial_revision + 1


async def test_retry_route_refuses_nonlegacy_version_without_enqueue(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = UUID(await make_workspace(client, headers))
    document = Document(
        org_id=seeded_user.org_id,
        workspace_id=workspace_id,
        name="Versioned document",
        created_by=seeded_user.id,
    )
    session.add(document)
    await session.flush()
    version = DocumentVersion(
        org_id=seeded_user.org_id,
        workspace_id=workspace_id,
        document_id=document.id,
        sequence=1,
        version_label="Rev 1",
        version_key="rev 1",
        content_hash=hashlib.sha256(b"retry-versioned").hexdigest(),
        source_filename="versioned.pdf",
        source_mime="application/pdf",
        source_size_bytes=15,
        source_storage_key="versioned/retry/source",
        parser_profile_version="docling/v1",
        ocr_profile_version="none/v1",
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
        index_profile_version="hybrid/v1",
        state="failed",
        provenance_state="failed",
        created_by=seeded_user.id,
    )
    session.add(version)
    await session.commit()
    initial_revision = version.lifecycle_revision

    response = await client.post(
        f"/api/v1/document-versions/{version.id}/retry",
        headers=headers,
    )

    assert response.status_code == 409
    assert captured_enqueues["ingest"] == []
    await session.refresh(version)
    assert (version.state, version.lifecycle_revision) == (
        "failed",
        initial_revision,
    )


async def test_retry_dispatch_failure_is_compensated_and_can_be_retried(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
    monkeypatch: pytest.MonkeyPatch,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)
    upload = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
        files={"file": ("dispatch.txt", b"dispatch source", "text/plain")},
    )
    version_id = UUID(upload.json()["id"])
    version = await session.get(DocumentVersion, version_id)
    assert version is not None
    version.state = "failed"
    version.provenance_state = "none"
    await session.commit()
    initial_revision = version.lifecycle_revision

    def fail_dispatch(_document_id: UUID, _size: int, _revision: int) -> None:
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr("openrag.api.routes.documents.enqueue_ingest", fail_dispatch)
    with pytest.raises(RuntimeError, match="queue unavailable"):
        await client.post(
            f"/api/v1/document-versions/{version_id}/retry",
            headers=headers,
        )

    await session.refresh(version)
    assert (version.state, version.provenance_state) == ("failed", "none")
    assert version.processing_error_code == "dispatch_failed"
    assert version.lifecycle_revision == initial_revision + 2

    retry_calls: list[tuple[UUID, int, int]] = []
    monkeypatch.setattr(
        "openrag.api.routes.documents.enqueue_ingest",
        lambda document_id, size, revision: retry_calls.append((document_id, size, revision)),
    )
    response = await client.post(
        f"/api/v1/document-versions/{version_id}/retry",
        headers=headers,
    )

    assert response.status_code == 202
    assert retry_calls == [(version_id, len(b"dispatch source"), initial_revision + 3)]
    await session.refresh(version)
    assert (version.state, version.lifecycle_revision) == (
        "processing",
        initial_revision + 3,
    )


async def test_initial_upload_dispatch_failure_is_retryable_and_mirrored(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
    monkeypatch: pytest.MonkeyPatch,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)

    def fail_dispatch(_document_id: UUID, _size: int, _revision: int) -> None:
        raise RuntimeError("initial queue unavailable")

    monkeypatch.setattr("openrag.api.routes.documents.enqueue_ingest", fail_dispatch)
    with pytest.raises(RuntimeError, match="initial queue unavailable"):
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            headers=headers,
            files={"file": ("initial.txt", b"initial source", "text/plain")},
        )

    document = (await session.execute(select(Document))).scalar_one()
    version = await session.get(DocumentVersion, document.id)
    assert version is not None
    assert (version.state, version.provenance_state) == ("failed", "none")
    assert version.processing_error_code == "dispatch_failed"
    assert (document.status, document.error) == ("failed", "dispatch_failed")
    failed_revision = version.lifecycle_revision

    retry_calls: list[tuple[UUID, int, int]] = []
    monkeypatch.setattr(
        "openrag.api.routes.documents.enqueue_ingest",
        lambda document_id, size, revision: retry_calls.append((document_id, size, revision)),
    )
    response = await client.post(
        f"/api/v1/document-versions/{version.id}/retry",
        headers=headers,
    )

    assert response.status_code == 202
    assert retry_calls == [(document.id, len(b"initial source"), failed_revision + 1)]
    await session.refresh(document)
    await session.refresh(version)
    assert (version.state, version.lifecycle_revision) == (
        "processing",
        failed_revision + 1,
    )
    assert (document.status, document.error) == ("processing", None)
