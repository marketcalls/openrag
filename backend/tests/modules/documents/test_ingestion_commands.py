from types import SimpleNamespace
from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.documents import service
from openrag.modules.documents.models import DocumentVersion
from openrag.modules.events.models import OutboxEvent


class RecordingSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.added.append(value)


def test_ingestion_request_is_content_free_and_revision_deduplicated() -> None:
    generation_id = UUID("260f51ce-8c05-4d87-9579-96da4f27497e")
    version = SimpleNamespace(
        id=UUID("10000000-0000-0000-0000-000000000001"),
        org_id=UUID("20000000-0000-0000-0000-000000000002"),
        workspace_id=UUID("30000000-0000-0000-0000-000000000003"),
        document_id=UUID("40000000-0000-0000-0000-000000000004"),
        lifecycle_revision=7,
        source_storage_key="SENTINEL/private/source",
        content_hash="SENTINEL/hash",
    )
    recording = RecordingSession()
    service._persist_ingestion_request(
        cast(AsyncSession, recording),
        cast(DocumentVersion, version),
        generation_id,
    )

    assert len(recording.added) == 1
    event = cast(OutboxEvent, recording.added[0])
    assert event.aggregate_id == version.id
    assert event.dedupe_key == f"document-version:{version.id}:ingestion:7"
    assert event.payload["payload"] == {
        "document_id": str(version.document_id),
        "attempt": 7,
        "authority_generation_id": str(generation_id),
    }
    assert "SENTINEL" not in repr(event.payload)
