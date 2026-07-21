import hashlib
import io
import zipfile
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.core.db import naive_utc
from openrag.modules.auth.models import User
from openrag.modules.documents.models import Document, DocumentVersion
from openrag.modules.documents.profiles import active_ingestion_profiles
from openrag.modules.events.envelopes import INGESTION_REQUESTED_EVENT_TYPE
from openrag.modules.events.models import OutboxEvent


@pytest.fixture
def captured_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[tuple[Any, ...]]]:
    calls: dict[str, list[tuple[Any, ...]]] = {"delete": []}
    monkeypatch.setattr(
        "openrag.api.routes.documents.enqueue_delete",
        lambda document_id, actor_id: calls["delete"].append((document_id, actor_id)),
    )
    return calls


async def ingestion_events(session: AsyncSession) -> list[OutboxEvent]:
    return list(
        (
            await session.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.event_type == INGESTION_REQUESTED_EVENT_TYPE)
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
            )
        ).all()
    )


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
    events = await ingestion_events(session)
    assert len(events) == 1
    assert events[0].aggregate_id == UUID(body["id"])
    assert events[0].dedupe_key == f"document-version:{body['id']}:ingestion:1"

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
    assert version.version_label == "Initial 1"
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
    after_deletion_request = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
    )
    assert after_deletion_request.json() == []

    reupload = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
        files={"file": ("notes-again.txt", b"the flux capacitor hums", "text/plain")},
    )
    assert reupload.status_code == 201, reupload.text
    assert reupload.json()["id"] != body["id"]


async def test_upload_rejects_mime_magic_mismatch_before_record_or_enqueue(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
        files={"file": ("forged.pdf", b"not a pdf", "application/pdf")},
    )

    assert response.status_code == 415
    assert response.json()["title"] == "Unsupported media type"
    assert await ingestion_events(session) == []
    assert list((await session.scalars(select(Document))).all()) == []


async def test_powerpoint_upload_is_supported_after_ooxml_validation(
    client: httpx.AsyncClient,
    seeded_user: User,
    stack_env: None,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
    session: AsyncSession,
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("ppt/presentation.xml", b"<presentation/>")
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
        files={
            "file": (
                "briefing.pptx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
        },
    )

    assert response.status_code == 201
    assert response.json()["mime"] == (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert len(await ingestion_events(session)) == 1


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
    session: AsyncSession,
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
    assert await ingestion_events(session) == []


async def test_delete_processing_document_cancels_and_schedules_cleanup(
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
        files={"file": ("live.txt", b"still processing", "text/plain")},
    )

    document_id = UUID(upload.json()["id"])
    response = await client.delete(f"/api/v1/documents/{document_id}", headers=headers)

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "deletion scheduled"}
    assert captured_enqueues["delete"] == [(document_id, seeded_user.id)]
    version = await session.get(DocumentVersion, document_id)
    assert version is not None
    assert version.state == "failed"
    assert version.provenance_state == "failed"
    assert version.source_delete_requested_at is not None


async def test_delete_schedules_nonlegacy_failed_version(
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
        embedding_profile_version=active_ingestion_profiles(
            get_settings()
        ).embedding_profile_version,
        index_profile_version="hybrid/v1",
        state="failed",
        provenance_state="failed",
        created_by=seeded_user.id,
    )
    session.add(version)
    await session.commit()
    assert version.id != document.id

    response = await client.delete(f"/api/v1/documents/{document.id}", headers=headers)

    assert response.status_code == 202
    assert captured_enqueues["delete"] == [(version.id, seeded_user.id)]
    await session.refresh(version)
    assert version.source_delete_requested_at is not None


async def test_delete_review_document_rejects_and_schedules_cleanup(
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
        files={"file": ("review.txt", b"ready for review", "text/plain")},
    )
    document_id = UUID(upload.json()["id"])
    version = await session.get(DocumentVersion, document_id)
    assert version is not None
    version.state = "review"
    version.provenance_state = "ready"
    version.source_page_count = 1
    await session.commit()

    response = await client.delete(f"/api/v1/documents/{document_id}", headers=headers)

    assert response.status_code == 202
    assert response.json() == {"status": "deletion scheduled"}
    assert captured_enqueues["delete"] == [(document_id, seeded_user.id)]
    await session.refresh(version)
    assert version.state == "rejected"
    assert version.rejected_by == seeded_user.id
    assert version.source_delete_requested_at is not None


async def test_delete_retires_approved_document_without_destroying_governed_history(
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
        files={"file": ("approved.txt", b"approved content", "text/plain")},
    )
    document_id = UUID(upload.json()["id"])
    version = await session.get(DocumentVersion, document_id)
    document = await session.get(Document, document_id)
    assert version is not None and document is not None
    now = naive_utc()
    version.state = "approved"
    version.provenance_state = "ready"
    version.source_page_count = 1
    version.approved_by = seeded_user.id
    version.approved_at = now
    version.decision_at = now
    document.status = "indexed"
    document.page_count = 1
    await session.commit()

    response = await client.delete(f"/api/v1/documents/{document_id}", headers=headers)

    assert response.status_code == 202
    assert response.json() == {"status": "document retired"}
    assert captured_enqueues["delete"] == []
    await session.refresh(document)
    await session.refresh(version)
    assert document.status == "deleted"
    assert version.state == "approved"
    listing = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
    )
    assert listing.json() == []


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


async def test_legacy_retry_commits_transition_and_durable_command_once(
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
    events = await ingestion_events(session)
    assert [event.payload["payload"]["attempt"] for event in events] == [
        1,
        initial_revision + 1,
    ]
    await session.refresh(version)
    assert (version.state, version.provenance_state) == ("processing", "none")
    assert version.processing_error_code is None
    assert version.lifecycle_revision == initial_revision + 1


async def test_retry_route_schedules_nonlegacy_version_durably(
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
        embedding_profile_version=active_ingestion_profiles(
            get_settings()
        ).embedding_profile_version,
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

    assert response.status_code == 202, response.text
    events = await ingestion_events(session)
    assert len(events) == 1
    assert events[0].aggregate_id == version.id
    assert events[0].payload["payload"]["attempt"] == initial_revision + 1
    await session.refresh(version)
    assert (version.state, version.lifecycle_revision) == (
        "processing",
        initial_revision + 1,
    )


async def test_retry_never_calls_legacy_direct_dispatch(
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

    monkeypatch.setattr(
        "openrag.worker.tasks.enqueue_ingest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy dispatch must not be called")
        ),
    )
    response = await client.post(
        f"/api/v1/document-versions/{version_id}/retry",
        headers=headers,
    )

    assert response.status_code == 202
    events = await ingestion_events(session)
    assert [event.payload["payload"]["attempt"] for event in events] == [
        1,
        initial_revision + 1,
    ]
    await session.refresh(version)
    assert (version.state, version.lifecycle_revision) == (
        "processing",
        initial_revision + 1,
    )


async def test_initial_upload_never_calls_legacy_direct_dispatch(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
    monkeypatch: pytest.MonkeyPatch,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)

    monkeypatch.setattr(
        "openrag.worker.tasks.enqueue_ingest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy dispatch must not be called")
        ),
    )
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
        files={"file": ("initial.txt", b"initial source", "text/plain")},
    )

    assert response.status_code == 201, response.text
    document = (await session.execute(select(Document))).scalar_one()
    version = await session.get(DocumentVersion, document.id)
    assert version is not None
    assert (version.state, version.provenance_state) == ("processing", "none")
    assert version.processing_error_code is None
    assert (document.status, document.error) == ("queued", None)
    assert len(await ingestion_events(session)) == 1


async def test_upload_controlled_version_uses_server_profiles_and_durable_command(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
    captured_enqueues: dict[str, list[tuple[Any, ...]]],
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)
    initial = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
        files={"file": ("manual.txt", b"revision one", "text/plain")},
    )
    document_id = initial.json()["id"]

    response = await client.post(
        f"/api/v1/documents/{document_id}/versions",
        headers=headers,
        data={"version_label": "Rev 2"},
        files={"file": ("manual-v2.txt", b"revision two", "text/plain")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["document_id"] == document_id
    assert body["sequence"] == 2
    assert body["version_label"] == "Rev 2"
    version = await session.get(DocumentVersion, UUID(body["id"]))
    assert version is not None
    assert version.parser_profile_version == "openrag-parser/v1"
    assert version.chunking_profile_version == "openrag-page-local/v1"
    assert version.embedding_profile_version.startswith("embedding/v1/")
    assert version.index_profile_version == "openrag-authority-hybrid/v1"
    events = await ingestion_events(session)
    assert [event.aggregate_id for event in events] == [
        UUID(document_id),
        version.id,
    ]
