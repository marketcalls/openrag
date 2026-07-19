from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.documents.models import DocumentVersion, IngestStageAttempt
from openrag.modules.events.envelopes import (
    INGESTION_REQUESTED_EVENT_TYPE,
    LIFECYCLE_EVENT_TYPE,
)
from openrag.modules.events.models import OutboxEvent


async def _auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_upload_and_retry_commit_durable_ingestion_commands(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    stack_env: None,
) -> None:
    headers = await _auth(client, seeded_user.email)
    workspace = await client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Legacy behavior"},
    )
    workspace_id = workspace.json()["id"]

    upload = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=headers,
        files={"file": ("legacy.txt", b"legacy behavior", "text/plain")},
    )
    assert upload.status_code == 201
    version_id = UUID(upload.json()["id"])
    upload_events = list((await session.scalars(select(OutboxEvent))).all())
    assert [event.event_type for event in upload_events] == [
        INGESTION_REQUESTED_EVENT_TYPE
    ]
    assert await session.scalar(
        select(func.count()).select_from(IngestStageAttempt)
    ) == 0

    version = await session.get(DocumentVersion, version_id)
    assert version is not None
    version.state = "failed"
    version.provenance_state = "none"
    await session.commit()
    previous_revision = version.lifecycle_revision

    retry = await client.post(
        f"/api/v1/document-versions/{version_id}/retry",
        headers=headers,
    )

    assert retry.status_code == 202
    events = list((await session.scalars(select(OutboxEvent))).all())
    assert sorted(event.event_type for event in events) == sorted([
        INGESTION_REQUESTED_EVENT_TYPE,
        LIFECYCLE_EVENT_TYPE,
        INGESTION_REQUESTED_EVENT_TYPE,
    ])
    assert [
        event.payload["payload"]["attempt"]
        for event in events
        if event.event_type == INGESTION_REQUESTED_EVENT_TYPE
    ] == [1, previous_revision + 1]
    assert await session.scalar(
        select(func.count()).select_from(IngestStageAttempt)
    ) == 0
