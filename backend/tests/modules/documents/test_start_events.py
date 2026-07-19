import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from openrag.core.db import naive_utc
from openrag.modules.auth.models import User
from openrag.modules.documents.models import (
    Document,
    DocumentVersion,
    IngestStageAttempt,
)
from openrag.modules.documents.start_events import consume_document_start
from openrag.modules.events.consumer import StreamDelivery
from openrag.modules.events.envelopes import (
    DocumentVersionIngestionRequestedV1,
    DocumentVersionRebuildRequestedV1,
)
from openrag.modules.events.models import InboxEvent, OutboxEvent
from openrag.modules.events.outbox import add_registered_event
from openrag.modules.events.streams import (
    DOCUMENT_COMMANDS_DLQ_STREAM,
    DOCUMENT_COMMANDS_GROUP,
    DOCUMENT_COMMANDS_STREAM,
)
from openrag.modules.tenancy.models import Workspace


class RecordingRedis:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.session_factory = session_factory
        self.calls: list[tuple[str, object]] = []
        self.committed_before_ack = False

    async def xadd(self, name: str, fields: dict[bytes, bytes]) -> bytes:
        self.calls.append(("xadd", (name, fields)))
        return b"1700000000001-0"

    async def waitaof(
        self,
        num_local: int,
        num_replicas: int,
        timeout: int,
    ) -> tuple[int, int]:
        self.calls.append(("waitaof", (num_local, num_replicas, timeout)))
        return 1, 0

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        async with self.session_factory() as session:
            inbox = await session.scalar(select(func.count()).select_from(InboxEvent))
            attempts = await session.scalar(
                select(func.count()).select_from(IngestStageAttempt)
            )
        self.committed_before_ack = inbox == 1 and attempts == 1
        self.calls.append(("xack", (name, groupname, ids)))
        return 1


async def _seed_start_event(
    session: AsyncSession,
    *,
    user: User,
    pipeline_kind: str,
    authority_generation_id: UUID,
) -> tuple[OutboxEvent, DocumentVersion]:
    workspace = Workspace(org_id=user.org_id, name=f"{pipeline_kind} workspace")
    session.add(workspace)
    await session.flush()
    document = Document(
        org_id=user.org_id,
        workspace_id=workspace.id,
        name=f"{pipeline_kind}.pdf",
        filename=f"{pipeline_kind}.pdf",
        mime="application/pdf",
        size_bytes=100,
        content_hash=("a" if pipeline_kind == "ingestion" else "b") * 64,
        status="processing" if pipeline_kind == "ingestion" else "indexed",
        storage_key=f"starts/{pipeline_kind}.pdf",
        created_by=user.id,
    )
    session.add(document)
    await session.flush()
    if pipeline_kind == "ingestion":
        version = DocumentVersion(
            org_id=user.org_id,
            workspace_id=workspace.id,
            document_id=document.id,
            sequence=1,
            version_label="1.0",
            version_key="1.0",
            content_hash="a" * 64,
            source_filename="ingestion.pdf",
            source_mime="application/pdf",
            source_size_bytes=100,
            source_storage_key="starts/ingestion.pdf",
            parser_profile_version="parser/v1",
            ocr_profile_version="ocr/v1",
            chunking_profile_version="chunking/v1",
            embedding_profile_version="embedding/v1",
            index_profile_version="index/v1",
            state="processing",
            provenance_state="none",
            created_by=user.id,
        )
        payload = DocumentVersionIngestionRequestedV1(
            document_id=document.id,
            attempt=2,
            authority_generation_id=authority_generation_id,
        )
    else:
        approved_at = naive_utc()
        version = DocumentVersion(
            id=document.id,
            org_id=user.org_id,
            workspace_id=workspace.id,
            document_id=document.id,
            sequence=1,
            version_label="Legacy 1",
            version_key="legacy 1",
            content_hash="b" * 64,
            source_filename="rebuild.pdf",
            source_mime="application/pdf",
            source_size_bytes=100,
            source_storage_key="starts/rebuild.pdf",
            parser_profile_version="legacy/parser-v1",
            ocr_profile_version="legacy/ocr-unknown-v1",
            chunking_profile_version="legacy/chunking-v1",
            embedding_profile_version="legacy/embedding-v1",
            index_profile_version="legacy/index-v1",
            state="approved",
            provenance_state="legacy_pending",
            created_by=user.id,
            approved_by=user.id,
            approved_at=approved_at,
            decision_at=approved_at,
            legacy_approval_backfilled=True,
        )
        payload = DocumentVersionRebuildRequestedV1(
            document_id=document.id,
            authority_generation_id=authority_generation_id,
        )
    session.add(version)
    await session.flush()
    event = add_registered_event(
        session,
        payload=payload,
        org_id=user.org_id,
        workspace_id=workspace.id,
        aggregate_id=version.id,
        lifecycle_revision=version.lifecycle_revision,
        occurred_at=datetime(2026, 7, 20, 4, tzinfo=UTC),
    )
    await session.commit()
    return event, version


def _delivery(event: OutboxEvent, *, delivery_count: int = 1) -> StreamDelivery:
    return StreamDelivery(
        stream=DOCUMENT_COMMANDS_STREAM,
        group=DOCUMENT_COMMANDS_GROUP,
        message_id="1700000000000-0",
        fields={
            b"envelope_bytes": json.dumps(
                event.payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode(),
            b"envelope_digest": event.envelope_digest.encode(),
        },
        delivery_count=delivery_count,
    )


@pytest.mark.parametrize("pipeline_kind", ["ingestion", "rebuild"])
async def test_start_consumer_commits_one_first_stage_before_ack(
    engine: AsyncEngine,
    session: AsyncSession,
    seeded_user: User,
    pipeline_kind: str,
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    generation_id = uuid4()
    event, version = await _seed_start_event(
        session,
        user=seeded_user,
        pipeline_kind=pipeline_kind,
        authority_generation_id=generation_id,
    )
    redis = RecordingRedis(factory)

    first = await consume_document_start(factory, redis, _delivery(event))
    duplicate = await consume_document_start(factory, redis, _delivery(event))

    async with factory() as verify:
        attempts = list((await verify.scalars(select(IngestStageAttempt))).all())
        inbox_count = await verify.scalar(select(func.count()).select_from(InboxEvent))
    assert (first, duplicate) == ("processed", "duplicate")
    assert redis.committed_before_ack is True
    assert inbox_count == 1
    assert len(attempts) == 1
    assert attempts[0].document_version_id == version.id
    assert attempts[0].pipeline_kind == pipeline_kind
    assert attempts[0].stage == "parse"
    assert attempts[0].state == "queued"
    assert generation_id.hex in attempts[0].checkpoint


async def test_stale_start_is_not_queued_and_terminal_delivery_uses_command_dlq(
    engine: AsyncEngine,
    session: AsyncSession,
    seeded_user: User,
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    event, version = await _seed_start_event(
        session,
        user=seeded_user,
        pipeline_kind="ingestion",
        authority_generation_id=uuid4(),
    )
    version.state = "failed"
    await session.commit()
    redis = RecordingRedis(factory)

    pending = await consume_document_start(
        factory,
        redis,
        _delivery(event, delivery_count=1),
    )
    rejected = await consume_document_start(
        factory,
        redis,
        _delivery(event, delivery_count=8),
    )

    async with factory() as verify:
        assert await verify.scalar(
            select(func.count()).select_from(IngestStageAttempt)
        ) == 0
        assert await verify.scalar(select(func.count()).select_from(InboxEvent)) == 0
    assert (pending, rejected) == ("pending", "rejected")
    dlq_stream, fields = redis.calls[0][1]  # type: ignore[misc]
    assert dlq_stream == DOCUMENT_COMMANDS_DLQ_STREAM
    assert set(fields) == {
        b"error_code",
        b"source_message_id",
        b"source_stream",
    }
    assert [name for name, _ in redis.calls] == ["xadd", "waitaof", "xack"]
