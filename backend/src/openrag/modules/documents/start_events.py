"""Replay-safe transaction boundary for document ingestion start commands."""

from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.db import naive_utc
from openrag.modules.documents.models import (
    DocumentVersion,
    DocumentVersionProjection,
    IngestStageAttempt,
)
from openrag.modules.embeddings.models import EmbeddingDeployment, EmbeddingProfile
from openrag.modules.events.consumer import (
    ConsumerRedis,
    ConsumerResult,
    EventAuthorityError,
    SchemaParser,
    StreamDelivery,
    consume_one,
)
from openrag.modules.events.envelopes import (
    INGESTION_REQUESTED_EVENT_TYPE,
    REBUILD_REQUESTED_EVENT_TYPE,
    REINDEX_REQUESTED_EVENT_TYPE,
    DocumentVersionIngestionRequestedV1,
    DocumentVersionRebuildRequestedV1,
    DocumentVersionReindexRequestedV1,
    EventEnvelopeBase,
    parse_base_envelope,
    parse_registered_envelope,
)
from openrag.modules.events.streams import (
    DOCUMENT_COMMANDS_DLQ_STREAM,
    DOCUMENT_COMMANDS_GROUP,
    DOCUMENT_COMMANDS_STREAM,
)

DOCUMENT_START_CONSUMER = "document-starts-v1"
_MAX_START_BATCH = 100


class DocumentStartRedis(ConsumerRedis, Protocol):
    async def xautoclaim(self, **kwargs: object) -> object: ...

    async def xreadgroup(self, **kwargs: object) -> object: ...

    async def xpending_range(self, **kwargs: object) -> object: ...


def _parse_start_command(encoded: bytes) -> EventEnvelopeBase:
    envelope = parse_registered_envelope(encoded)
    if envelope.event_type not in {
        INGESTION_REQUESTED_EVENT_TYPE,
        REINDEX_REQUESTED_EVENT_TYPE,
        REBUILD_REQUESTED_EVENT_TYPE,
    }:
        raise ValueError("schema_not_registered")
    return parse_base_envelope(encoded)


START_SCHEMA_PARSERS: Mapping[tuple[int, str], SchemaParser] = {
    (1, INGESTION_REQUESTED_EVENT_TYPE): _parse_start_command,
    (1, REINDEX_REQUESTED_EVENT_TYPE): _parse_start_command,
    (1, REBUILD_REQUESTED_EVENT_TYPE): _parse_start_command,
}


def _command_payload(
    envelope: EventEnvelopeBase,
) -> tuple[
    str,
    (
        DocumentVersionIngestionRequestedV1
        | DocumentVersionRebuildRequestedV1
        | DocumentVersionReindexRequestedV1
    ),
]:
    if envelope.event_type == INGESTION_REQUESTED_EVENT_TYPE:
        return (
            "ingestion",
            DocumentVersionIngestionRequestedV1.model_validate(envelope.payload),
        )
    if envelope.event_type == REBUILD_REQUESTED_EVENT_TYPE:
        return (
            "rebuild",
            DocumentVersionRebuildRequestedV1.model_validate(envelope.payload),
        )
    if envelope.event_type == REINDEX_REQUESTED_EVENT_TYPE:
        return (
            "reindex",
            DocumentVersionReindexRequestedV1.model_validate(envelope.payload),
        )
    raise EventAuthorityError("event_not_authoritative")


async def revalidate_document_start(
    session: AsyncSession,
    envelope: EventEnvelopeBase,
) -> None:
    """Lock and validate the exact version and allowed start state."""

    pipeline_kind, payload = _command_payload(envelope)
    version = await session.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.id == envelope.aggregate_id,
            DocumentVersion.org_id == envelope.org_id,
            DocumentVersion.workspace_id == envelope.workspace_id,
        )
        .with_for_update()
    )
    reindex_authorized = False
    if isinstance(payload, DocumentVersionReindexRequestedV1):
        deployment = await session.scalar(
            select(EmbeddingDeployment).where(
                EmbeddingDeployment.id == payload.deployment_id,
                EmbeddingDeployment.generation_id
                == payload.authority_generation_id,
                EmbeddingDeployment.status == "building",
            )
        )
        profile = (
            await session.get(EmbeddingProfile, deployment.profile_id)
            if deployment is not None
            else None
        )
        projection = await session.scalar(
            select(DocumentVersionProjection).where(
                DocumentVersionProjection.org_id == envelope.org_id,
                DocumentVersionProjection.workspace_id == envelope.workspace_id,
                DocumentVersionProjection.document_version_id
                == envelope.aggregate_id,
                DocumentVersionProjection.is_current_eligible.is_(True),
            )
        )
        reindex_authorized = (
            deployment is not None
            and profile is not None
            and profile.enabled
            and payload.embedding_profile_version
            == f"embedding/v1/{profile.config_digest}"
            and projection is not None
        )
    valid_state = (
        version is not None
        and version.document_id == payload.document_id
        and version.lifecycle_revision == envelope.lifecycle_revision
        and version.source_deleted_at is None
        and version.source_storage_key is not None
        and (
            (
                pipeline_kind == "ingestion"
                and version.state == "processing"
                and version.provenance_state in {"none", "failed"}
            )
            or (
                pipeline_kind == "rebuild"
                and version.state == "approved"
                and version.provenance_state == "legacy_pending"
            )
            or (
                pipeline_kind == "reindex"
                and version.state == "approved"
                and version.provenance_state == "ready"
                and reindex_authorized
            )
        )
    )
    if not valid_state:
        raise EventAuthorityError("event_not_authoritative")


async def queue_first_ingest_stage(
    session: AsyncSession,
    envelope: EventEnvelopeBase,
) -> None:
    """Insert one logical first stage without running external work."""

    pipeline_kind, payload = _command_payload(envelope)
    attempt = (
        payload.attempt
        if isinstance(payload, DocumentVersionIngestionRequestedV1)
        else 1
    )
    checkpoint = (
        f"parse:{pipeline_kind}:{attempt}:{payload.authority_generation_id.hex}"
    )
    now = naive_utc()
    await session.execute(
        insert(IngestStageAttempt)
        .values(
            id=uuid4(),
            created_at=now,
            org_id=envelope.org_id,
            workspace_id=envelope.workspace_id,
            document_version_id=envelope.aggregate_id,
            embedding_deployment_id=(
                payload.deployment_id
                if isinstance(payload, DocumentVersionReindexRequestedV1)
                else None
            ),
            embedding_profile_version=(
                payload.embedding_profile_version
                if isinstance(payload, DocumentVersionReindexRequestedV1)
                else None
            ),
            pipeline_kind=pipeline_kind,
            stage="parse",
            state="queued",
            checkpoint=checkpoint,
            attempts=0,
            available_at=now,
        )
        .on_conflict_do_nothing(
            constraint="uq_ingest_stage_attempts_checkpoint"
        )
    )


async def consume_document_start(
    session_factory: async_sessionmaker[AsyncSession],
    redis: ConsumerRedis,
    delivery: StreamDelivery,
) -> ConsumerResult:
    """Commit Inbox plus first stage before acknowledging one command."""

    return await consume_one(
        session_factory,
        redis,
        consumer=DOCUMENT_START_CONSUMER,
        delivery=delivery,
        revalidate=revalidate_document_start,
        apply_effect=queue_first_ingest_stage,
        poison_delivery_limit=8,
        schema_parsers=START_SCHEMA_PARSERS,
        expected_stream=DOCUMENT_COMMANDS_STREAM,
        expected_group=DOCUMENT_COMMANDS_GROUP,
        dlq_stream=DOCUMENT_COMMANDS_DLQ_STREAM,
    )


def _text(value: object) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RuntimeError("document_start_stream_invalid") from exc
    if isinstance(value, str):
        return value
    raise RuntimeError("document_start_stream_invalid")


def _messages(value: object) -> list[tuple[str, Mapping[bytes, bytes]]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RuntimeError("document_start_stream_invalid")
    messages: list[tuple[str, Mapping[bytes, bytes]]] = []
    for item in value:
        if (
            not isinstance(item, Sequence)
            or isinstance(item, (str, bytes))
            or len(item) != 2
            or not isinstance(item[1], Mapping)
        ):
            raise RuntimeError("document_start_stream_invalid")
        fields = item[1]
        if not all(
            isinstance(key, bytes) and isinstance(field, bytes)
            for key, field in fields.items()
        ):
            raise RuntimeError("document_start_stream_invalid")
        messages.append((_text(item[0]), fields))
    return messages


def _claimed_messages(value: object) -> list[tuple[str, Mapping[bytes, bytes]]]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) < 2
    ):
        raise RuntimeError("document_start_stream_invalid")
    return _messages(value[1])


def _fresh_messages(value: object) -> list[tuple[str, Mapping[bytes, bytes]]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RuntimeError("document_start_stream_invalid")
    messages: list[tuple[str, Mapping[bytes, bytes]]] = []
    for stream_result in value:
        if (
            not isinstance(stream_result, Sequence)
            or isinstance(stream_result, (str, bytes))
            or len(stream_result) != 2
            or _text(stream_result[0]) != DOCUMENT_COMMANDS_STREAM
        ):
            raise RuntimeError("document_start_stream_invalid")
        messages.extend(_messages(stream_result[1]))
    return messages


def _delivery_counts(value: object) -> dict[str, int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RuntimeError("document_start_pending_invalid")
    counts: dict[str, int] = {}
    for row in value:
        if not isinstance(row, Mapping):
            raise RuntimeError("document_start_pending_invalid")
        message_id = row.get("message_id", row.get(b"message_id"))
        delivered = row.get("times_delivered", row.get(b"times_delivered"))
        if not isinstance(delivered, int) or delivered < 1:
            raise RuntimeError("document_start_pending_invalid")
        counts[_text(message_id)] = delivered
    return counts


async def consume_document_start_batch(
    session_factory: async_sessionmaker[AsyncSession],
    redis: DocumentStartRedis,
    *,
    consumer: str,
    batch_size: int = 20,
    reclaim_idle_ms: int = 30_000,
) -> dict[str, int]:
    """Reclaim stale commands first, then process a bounded fresh batch."""

    if not 1 <= len(consumer) <= 120:
        raise ValueError("consumer_invalid")
    if not 1 <= batch_size <= _MAX_START_BATCH:
        raise ValueError("batch_size_invalid")
    if not 30_000 <= reclaim_idle_ms <= 3_600_000:
        raise ValueError("reclaim_idle_ms_invalid")

    claimed = _claimed_messages(
        await redis.xautoclaim(
            name=DOCUMENT_COMMANDS_STREAM,
            groupname=DOCUMENT_COMMANDS_GROUP,
            consumername=consumer,
            min_idle_time=reclaim_idle_ms,
            start_id="0-0",
            count=batch_size,
        )
    )
    fresh: list[tuple[str, Mapping[bytes, bytes]]] = []
    remaining = batch_size - len(claimed)
    if remaining > 0:
        fresh = _fresh_messages(
            await redis.xreadgroup(
                groupname=DOCUMENT_COMMANDS_GROUP,
                consumername=consumer,
                streams={DOCUMENT_COMMANDS_STREAM: ">"},
                count=remaining,
                block=1,
            )
        )
    combined = claimed + fresh
    counts = {
        "claimed": len(claimed),
        "fresh": len(fresh),
        "processed": 0,
        "duplicate": 0,
        "pending": 0,
        "deferred": 0,
        "rejected": 0,
    }
    if not combined:
        return counts

    pending = _delivery_counts(
        await redis.xpending_range(
            name=DOCUMENT_COMMANDS_STREAM,
            groupname=DOCUMENT_COMMANDS_GROUP,
            min="-",
            max="+",
            count=batch_size,
            consumername=consumer,
        )
    )
    seen: set[str] = set()
    for message_id, fields in combined:
        if message_id in seen:
            continue
        seen.add(message_id)
        result = await consume_document_start(
            session_factory,
            redis,
            StreamDelivery(
                stream=DOCUMENT_COMMANDS_STREAM,
                group=DOCUMENT_COMMANDS_GROUP,
                message_id=message_id,
                fields=fields,
                delivery_count=pending.get(message_id, 1),
            ),
        )
        counts[result] += 1
    return counts
