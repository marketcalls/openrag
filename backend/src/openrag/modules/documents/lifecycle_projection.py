"""Durable lifecycle projection from attested document events."""

from collections.abc import Mapping, Sequence
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.db import naive_utc
from openrag.modules.documents.models import (
    DocumentVersion,
    DocumentVersionProjection,
)
from openrag.modules.events.consumer import (
    ConsumerRedis,
    ConsumerResult,
    EventAuthorityError,
    StreamDelivery,
    consume_one,
    revalidate_document_lifecycle,
)
from openrag.modules.events.envelopes import EventEnvelopeBase
from openrag.modules.events.streams import (
    DOCUMENT_EVENTS_GROUP,
    DOCUMENT_EVENTS_STREAM,
)

DOCUMENT_LIFECYCLE_CONSUMER = "document-lifecycle-projection-v1"
_MAX_BATCH = 100


class LifecycleVersion(Protocol):
    @property
    def state(self) -> str: ...

    @property
    def provenance_state(self) -> str: ...

    @property
    def superseded_by_id(self) -> object | None: ...


class DocumentLifecycleRedis(ConsumerRedis, Protocol):
    async def xautoclaim(self, **kwargs: object) -> object: ...

    async def xreadgroup(self, **kwargs: object) -> object: ...

    async def xpending_range(self, **kwargs: object) -> object: ...


def is_current_eligible(version: LifecycleVersion) -> bool:
    """Derive retrieval eligibility only from authoritative lifecycle state."""

    return (
        version.state == "approved"
        and version.provenance_state == "ready"
        and version.superseded_by_id is None
    )


async def project_document_lifecycle(
    session: AsyncSession,
    envelope: EventEnvelopeBase,
) -> None:
    """Upsert the latest SQL projection in the same transaction as Inbox."""

    version = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == envelope.aggregate_id,
            DocumentVersion.org_id == envelope.org_id,
            DocumentVersion.workspace_id == envelope.workspace_id,
        )
    )
    if version is None or version.lifecycle_revision != envelope.lifecycle_revision:
        raise EventAuthorityError("event_not_authoritative")

    statement = insert(DocumentVersionProjection).values(
        org_id=envelope.org_id,
        workspace_id=envelope.workspace_id,
        document_version_id=version.id,
        is_current_eligible=is_current_eligible(version),
        applied_revision=envelope.lifecycle_revision,
        applied_at=naive_utc(),
        sync_state="queued",
        sync_attempts=0,
        sync_available_at=naive_utc(),
    )
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_document_version_projections_version",
            set_={
                "workspace_id": statement.excluded.workspace_id,
                "is_current_eligible": statement.excluded.is_current_eligible,
                "applied_revision": statement.excluded.applied_revision,
                "applied_at": statement.excluded.applied_at,
                "sync_state": statement.excluded.sync_state,
                "sync_attempts": statement.excluded.sync_attempts,
                "sync_available_at": statement.excluded.sync_available_at,
                "sync_lease_owner": None,
                "sync_lease_token": None,
                "sync_lease_expires_at": None,
                "sync_error_code": None,
            },
            where=(
                DocumentVersionProjection.applied_revision
                < statement.excluded.applied_revision
            ),
        )
    )


async def consume_document_lifecycle(
    session_factory: async_sessionmaker[AsyncSession],
    redis: ConsumerRedis,
    delivery: StreamDelivery,
) -> ConsumerResult:
    """Commit one current eligibility projection before acknowledging."""

    return await consume_one(
        session_factory,
        redis,
        consumer=DOCUMENT_LIFECYCLE_CONSUMER,
        delivery=delivery,
        revalidate=revalidate_document_lifecycle,
        apply_effect=project_document_lifecycle,
        poison_delivery_limit=8,
    )


def _text(value: object) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RuntimeError("document_lifecycle_stream_invalid") from exc
    if isinstance(value, str):
        return value
    raise RuntimeError("document_lifecycle_stream_invalid")


def _messages(value: object) -> list[tuple[str, Mapping[bytes, bytes]]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RuntimeError("document_lifecycle_stream_invalid")
    messages: list[tuple[str, Mapping[bytes, bytes]]] = []
    for item in value:
        if (
            not isinstance(item, Sequence)
            or isinstance(item, (str, bytes))
            or len(item) != 2
            or not isinstance(item[1], Mapping)
        ):
            raise RuntimeError("document_lifecycle_stream_invalid")
        fields = item[1]
        if not all(
            isinstance(key, bytes) and isinstance(field, bytes)
            for key, field in fields.items()
        ):
            raise RuntimeError("document_lifecycle_stream_invalid")
        messages.append((_text(item[0]), fields))
    return messages


def _claimed_messages(value: object) -> list[tuple[str, Mapping[bytes, bytes]]]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) < 2
    ):
        raise RuntimeError("document_lifecycle_stream_invalid")
    return _messages(value[1])


def _fresh_messages(value: object) -> list[tuple[str, Mapping[bytes, bytes]]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RuntimeError("document_lifecycle_stream_invalid")
    messages: list[tuple[str, Mapping[bytes, bytes]]] = []
    for stream_result in value:
        if (
            not isinstance(stream_result, Sequence)
            or isinstance(stream_result, (str, bytes))
            or len(stream_result) != 2
            or _text(stream_result[0]) != DOCUMENT_EVENTS_STREAM
        ):
            raise RuntimeError("document_lifecycle_stream_invalid")
        messages.extend(_messages(stream_result[1]))
    return messages


def _delivery_counts(value: object) -> dict[str, int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RuntimeError("document_lifecycle_pending_invalid")
    counts: dict[str, int] = {}
    for row in value:
        if not isinstance(row, Mapping):
            raise RuntimeError("document_lifecycle_pending_invalid")
        message_id = row.get("message_id", row.get(b"message_id"))
        delivered = row.get("times_delivered", row.get(b"times_delivered"))
        if not isinstance(delivered, int) or delivered < 1:
            raise RuntimeError("document_lifecycle_pending_invalid")
        counts[_text(message_id)] = delivered
    return counts


async def consume_document_lifecycle_batch(
    session_factory: async_sessionmaker[AsyncSession],
    redis: DocumentLifecycleRedis,
    *,
    consumer: str,
    batch_size: int = 20,
    reclaim_idle_ms: int = 30_000,
) -> dict[str, int]:
    """Reclaim stale lifecycle events before processing a bounded fresh batch."""

    if not 1 <= len(consumer) <= 120:
        raise ValueError("consumer_invalid")
    if not 1 <= batch_size <= _MAX_BATCH:
        raise ValueError("batch_size_invalid")
    if not 30_000 <= reclaim_idle_ms <= 3_600_000:
        raise ValueError("reclaim_idle_ms_invalid")

    claimed = _claimed_messages(
        await redis.xautoclaim(
            name=DOCUMENT_EVENTS_STREAM,
            groupname=DOCUMENT_EVENTS_GROUP,
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
                groupname=DOCUMENT_EVENTS_GROUP,
                consumername=consumer,
                streams={DOCUMENT_EVENTS_STREAM: ">"},
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
            name=DOCUMENT_EVENTS_STREAM,
            groupname=DOCUMENT_EVENTS_GROUP,
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
        result = await consume_document_lifecycle(
            session_factory,
            redis,
            StreamDelivery(
                stream=DOCUMENT_EVENTS_STREAM,
                group=DOCUMENT_EVENTS_GROUP,
                message_id=message_id,
                fields=fields,
                delivery_count=pending.get(message_id, 1),
            ),
        )
        counts[result] += 1
    return counts
