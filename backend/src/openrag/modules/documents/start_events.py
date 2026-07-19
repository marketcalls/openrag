"""Replay-safe transaction boundary for document ingestion start commands."""

from collections.abc import Mapping
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.db import naive_utc
from openrag.modules.documents.models import DocumentVersion, IngestStageAttempt
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
    DocumentVersionIngestionRequestedV1,
    DocumentVersionRebuildRequestedV1,
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


def _parse_start_command(encoded: bytes) -> EventEnvelopeBase:
    envelope = parse_registered_envelope(encoded)
    if envelope.event_type not in {
        INGESTION_REQUESTED_EVENT_TYPE,
        REBUILD_REQUESTED_EVENT_TYPE,
    }:
        raise ValueError("schema_not_registered")
    return parse_base_envelope(encoded)


START_SCHEMA_PARSERS: Mapping[tuple[int, str], SchemaParser] = {
    (1, INGESTION_REQUESTED_EVENT_TYPE): _parse_start_command,
    (1, REBUILD_REQUESTED_EVENT_TYPE): _parse_start_command,
}


def _command_payload(
    envelope: EventEnvelopeBase,
) -> tuple[
    str,
    DocumentVersionIngestionRequestedV1 | DocumentVersionRebuildRequestedV1,
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
            pipeline_kind=pipeline_kind,
            stage="parse",
            state="queued",
            checkpoint=checkpoint,
            attempts=0,
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
