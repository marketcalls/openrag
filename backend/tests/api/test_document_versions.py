import hashlib
from uuid import UUID, uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.documents.models import Document, DocumentVersion


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def make_workspace(client: httpx.AsyncClient, headers: dict[str, str]) -> UUID:
    response = await client.post(
        "/api/v1/workspaces", json={"name": f"Docs {uuid4()}"}, headers=headers
    )
    return UUID(response.json()["id"])


async def seed_version(
    session: AsyncSession,
    user: User,
    workspace_id: UUID,
    *,
    sequence: int = 1,
    state: str = "review",
    provenance_state: str = "ready",
    document: Document | None = None,
) -> tuple[Document, DocumentVersion]:
    if document is None:
        document = Document(
            org_id=user.org_id,
            workspace_id=workspace_id,
            name="Safety manual",
            department="HSE",
            document_type="Manual",
            external_identifier=f"MAN-{uuid4()}",
            created_by=user.id,
        )
        session.add(document)
        await session.flush()
    version = DocumentVersion(
        org_id=user.org_id,
        workspace_id=workspace_id,
        document_id=document.id,
        sequence=sequence,
        version_label=f"Rev {sequence}",
        version_key=f"rev {sequence}",
        content_hash=hashlib.sha256(f"version-{uuid4()}".encode()).hexdigest(),
        source_filename=f"manual-r{sequence}.pdf",
        source_mime="application/pdf",
        source_size_bytes=2048,
        source_storage_key=f"SENTINEL/private/{uuid4()}",
        source_page_count=12,
        parser_profile_version="docling/v1",
        ocr_profile_version="rapidocr/v1",
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
        index_profile_version="hybrid/v1",
        state=state,
        provenance_state=provenance_state,
        processing_error_code="safe_parser_failure" if state == "failed" else None,
        created_by=user.id,
    )
    session.add(version)
    await session.commit()
    return document, version


def assert_safe_version(body: dict[str, object]) -> None:
    assert {
        "id",
        "document_id",
        "sequence",
        "version_label",
        "state",
        "provenance_state",
        "page_count",
        "error_code",
        "revision_date",
        "effective_at",
        "expires_at",
        "created_at",
        "updated_at",
        "lifecycle_revision",
    } == set(body)
    serialized = repr(body)
    for forbidden in (
        "source_storage_key",
        "content_hash",
        "created_by",
        "approved_by",
        "parser_profile_version",
        "SENTINEL/private",
    ):
        assert forbidden not in serialized


async def test_version_list_and_detail_are_safe_and_descending(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)
    document, first = await seed_version(session, seeded_user, workspace_id)
    _, second = await seed_version(
        session, seeded_user, workspace_id, document=document, sequence=2
    )

    listing = await client.get(f"/api/v1/documents/{document.id}/versions", headers=headers)
    detail = await client.get(f"/api/v1/document-versions/{first.id}", headers=headers)

    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()] == [str(second.id), str(first.id)]
    for row in listing.json():
        assert_safe_version(row)
    assert detail.status_code == 200
    assert_safe_version(detail.json())


async def test_version_governance_routes_return_safe_models_and_transition_409(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)
    _document, version = await seed_version(session, seeded_user, workspace_id)

    approved = await client.post(
        f"/api/v1/document-versions/{version.id}/approve",
        headers=headers,
        json={"reason": "controlled copy reviewed"},
    )
    invalid = await client.post(
        f"/api/v1/document-versions/{version.id}/approve",
        headers=headers,
        json={"reason": None},
    )
    obsolete = await client.post(
        f"/api/v1/document-versions/{version.id}/obsolete",
        headers=headers,
        json={},
    )

    assert approved.status_code == 200
    assert_safe_version(approved.json())
    assert invalid.status_code == 409
    assert obsolete.status_code == 200
    assert_safe_version(obsolete.json())


async def test_reject_route_and_decision_body_are_strict_and_bounded(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)
    _document, version = await seed_version(session, seeded_user, workspace_id)

    extra = await client.post(
        f"/api/v1/document-versions/{version.id}/reject",
        headers=headers,
        json={"reason": "no", "actor_id": str(seeded_user.id)},
    )
    long_reason = await client.post(
        f"/api/v1/document-versions/{version.id}/reject",
        headers=headers,
        json={"reason": "x" * 501},
    )
    blank_reason = await client.post(
        f"/api/v1/document-versions/{version.id}/reject",
        headers=headers,
        json={"reason": "   "},
    )
    rejected = await client.post(
        f"/api/v1/document-versions/{version.id}/reject",
        headers=headers,
        json={"reason": "requires revision"},
    )

    assert extra.status_code == 422
    assert long_reason.status_code == 422
    assert blank_reason.status_code == 422
    assert rejected.status_code == 200
    assert_safe_version(rejected.json())


async def test_foreign_version_ids_are_404_without_oracle(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
) -> None:
    headers = await auth(client, seeded_user.email)

    for method, path in (
        ("GET", f"/api/v1/document-versions/{uuid4()}"),
        ("POST", f"/api/v1/document-versions/{uuid4()}/approve"),
        ("GET", f"/api/v1/documents/{uuid4()}/versions"),
    ):
        response = await client.request(
            method,
            path,
            headers=headers,
            json={"reason": None} if method == "POST" else None,
        )
        assert response.status_code == 404
