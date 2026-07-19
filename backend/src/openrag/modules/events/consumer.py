"""Attested, idempotent Redis Stream consumer transaction boundary."""

import hashlib
import hmac
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.modules.documents.models import DocumentVersion
from openrag.modules.events.envelopes import (
    LIFECYCLE_EVENT_TYPE,
    MAX_ENVELOPE_BYTES,
    DocumentVersionLifecycleV1,
    EventEnvelopeBase,
    parse_base_envelope,
    parse_registered_envelope,
)
from openrag.modules.events.models import InboxEvent, OutboxEvent
from openrag.modules.events.streams import (
    DOCUMENT_EVENTS_DLQ_STREAM,
    DOCUMENT_EVENTS_GROUP,
    DOCUMENT_EVENTS_STREAM,
    EVENT_TRANSPORT_FIELDS,
    stream_for_aggregate_type,
)
from openrag.modules.tenancy.models import Organization, Workspace

_DIGEST_PATTERN = re.compile(rb"^[0-9a-f]{64}$")
_MESSAGE_ID_PATTERN = re.compile(r"^[0-9]+-[0-9]+$")


class EventAcknowledgementError(RuntimeError):
    """Safe signal that committed work must be redelivered for ACK."""


class EventRejectionError(RuntimeError):
    """Safe signal that a poison terminal record was not made durable."""


class EventAuthorityError(RuntimeError):
    """Raised by domain revalidation for stale or unauthorized state."""


class ConsumerRedis(Protocol):
    async def xadd(
        self,
        name: str,
        fields: dict[bytes, bytes],
    ) -> bytes | str: ...

    async def waitaof(
        self,
        num_local: int,
        num_replicas: int,
        timeout: int,
    ) -> object: ...

    async def xack(
        self,
        name: str,
        groupname: str,
        *ids: str,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class StreamDelivery:
    stream: str
    group: str
    message_id: str
    fields: Mapping[bytes, bytes]
    delivery_count: int


ConsumerCallback = Callable[
    [AsyncSession, EventEnvelopeBase], Awaitable[None]
]
SchemaParser = Callable[[bytes], EventEnvelopeBase]
ConsumerResult = Literal[
    "processed", "duplicate", "deferred", "pending", "rejected"
]


async def revalidate_document_lifecycle(
    session: AsyncSession,
    envelope: EventEnvelopeBase,
) -> None:
    """Lock and revalidate every authoritative document lifecycle dimension."""

    organization_id = await session.scalar(
        select(Organization.id).where(Organization.id == envelope.org_id)
    )
    workspace_id = await session.scalar(
        select(Workspace.id).where(
            Workspace.id == envelope.workspace_id,
            Workspace.org_id == envelope.org_id,
        )
    )
    version = await session.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.id == envelope.aggregate_id,
            DocumentVersion.org_id == envelope.org_id,
            DocumentVersion.workspace_id == envelope.workspace_id,
        )
        .with_for_update()
    )
    payload = DocumentVersionLifecycleV1.model_validate(envelope.payload)
    if (
        organization_id != envelope.org_id
        or workspace_id != envelope.workspace_id
        or version is None
        or version.document_id != payload.document_id
        or version.lifecycle_revision != envelope.lifecycle_revision
        or version.state != payload.new_state.value
    ):
        raise EventAuthorityError("event_not_authoritative")


def _parse_lifecycle_base(encoded: bytes) -> EventEnvelopeBase:
    parse_registered_envelope(encoded)
    return parse_base_envelope(encoded)


DEFAULT_SCHEMA_PARSERS: Mapping[tuple[int, str], SchemaParser] = {
    (1, LIFECYCLE_EVENT_TYPE): _parse_lifecycle_base,
}


def _delivery_identity_valid(delivery: StreamDelivery) -> bool:
    return (
        delivery.stream == DOCUMENT_EVENTS_STREAM
        and delivery.group == DOCUMENT_EVENTS_GROUP
        and 1 <= delivery.delivery_count <= 2_147_483_647
        and len(delivery.message_id) <= 64
        and _MESSAGE_ID_PATTERN.fullmatch(delivery.message_id) is not None
    )


def _canonical_payload_bytes(payload: dict[str, object]) -> bytes:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise ValueError("contract_invalid")
    return encoded


def _aof_confirmed(result: object) -> bool:
    if isinstance(result, (tuple, list)) and result:
        return isinstance(result[0], int) and result[0] >= 1
    return isinstance(result, int) and result >= 1


async def _ack(
    redis: ConsumerRedis,
    delivery: StreamDelivery,
) -> None:
    try:
        await redis.xack(
            delivery.stream,
            delivery.group,
            delivery.message_id,
        )
    except Exception as exc:
        raise EventAcknowledgementError("event_acknowledgement_failed") from exc


async def _reject(
    redis: ConsumerRedis,
    delivery: StreamDelivery,
    *,
    error_code: str,
    poison_delivery_limit: int,
    waitaof_timeout_ms: int,
) -> ConsumerResult:
    if delivery.delivery_count < poison_delivery_limit:
        return "pending"
    try:
        await redis.xadd(
            DOCUMENT_EVENTS_DLQ_STREAM,
            {
                b"error_code": error_code.encode("ascii"),
                b"source_message_id": delivery.message_id.encode("ascii"),
                b"source_stream": delivery.stream.encode("ascii"),
            },
        )
        durability = await redis.waitaof(1, 0, waitaof_timeout_ms)
    except Exception as exc:
        raise EventRejectionError("event_rejection_not_durable") from exc
    if not _aof_confirmed(durability):
        raise EventRejectionError("event_rejection_not_durable")
    await _ack(redis, delivery)
    return "rejected"


def _transport_parts(
    delivery: StreamDelivery,
) -> tuple[bytes, bytes] | None:
    if set(delivery.fields) != EVENT_TRANSPORT_FIELDS:
        return None
    encoded = delivery.fields.get(b"envelope_bytes")
    digest = delivery.fields.get(b"envelope_digest")
    if (
        not isinstance(encoded, bytes)
        or not isinstance(digest, bytes)
        or _DIGEST_PATTERN.fullmatch(digest) is None
    ):
        return None
    return encoded, digest


def _outbox_attests(
    row: OutboxEvent | None,
    *,
    base: EventEnvelopeBase,
    encoded: bytes,
    transport_digest: bytes,
    stream: str,
) -> bool:
    if (
        row is None
        or row.dead_lettered_at is not None
        or row.envelope_digest is None
        or row.event_id != base.event_id
        or row.event_type != base.event_type
        or row.aggregate_type != base.aggregate_type
        or row.aggregate_id != base.aggregate_id
        or (row.published_stream is not None and row.published_stream != stream)
    ):
        return False
    try:
        authoritative = _canonical_payload_bytes(dict(row.payload))
        authoritative_base = parse_base_envelope(authoritative)
        expected_stream = stream_for_aggregate_type(
            authoritative_base.aggregate_type
        )
    except ValueError:
        return False
    computed_digest = hashlib.sha256(encoded).hexdigest().encode("ascii")
    return (
        expected_stream == stream
        and hmac.compare_digest(transport_digest, computed_digest)
        and hmac.compare_digest(
            transport_digest,
            row.envelope_digest.encode("ascii"),
        )
        and hmac.compare_digest(encoded, authoritative)
        and authoritative_base == base
    )


async def consume_one(
    session_factory: async_sessionmaker[AsyncSession],
    redis: ConsumerRedis,
    *,
    consumer: str,
    delivery: StreamDelivery,
    revalidate: ConsumerCallback,
    apply_effect: ConsumerCallback,
    poison_delivery_limit: int = 5,
    waitaof_timeout_ms: int = 5000,
    schema_parsers: Mapping[tuple[int, str], SchemaParser] | None = None,
) -> ConsumerResult:
    """Attest, transact idempotently, commit, and only then acknowledge."""

    if not 1 <= len(consumer) <= 120:
        raise ValueError("consumer_invalid")
    if not _delivery_identity_valid(delivery):
        raise ValueError("delivery_identity_invalid")
    if not 1 <= poison_delivery_limit <= 100:
        raise ValueError("poison_delivery_limit_invalid")

    parts = _transport_parts(delivery)
    if parts is None:
        return await _reject(
            redis,
            delivery,
            error_code="contract_invalid",
            poison_delivery_limit=poison_delivery_limit,
            waitaof_timeout_ms=waitaof_timeout_ms,
        )
    encoded, transport_digest = parts
    try:
        base = parse_base_envelope(encoded)
    except ValueError:
        return await _reject(
            redis,
            delivery,
            error_code="contract_invalid",
            poison_delivery_limit=poison_delivery_limit,
            waitaof_timeout_ms=waitaof_timeout_ms,
        )

    outcome: ConsumerResult = "pending"
    parsers = schema_parsers or DEFAULT_SCHEMA_PARSERS
    try:
        async with session_factory.begin() as session:
            row = await session.scalar(
                select(OutboxEvent)
                .where(OutboxEvent.event_id == base.event_id)
                .with_for_update()
            )
            if not _outbox_attests(
                row,
                base=base,
                encoded=encoded,
                transport_digest=transport_digest,
                stream=delivery.stream,
            ):
                outcome = "rejected"
            elif (base.schema_version, base.event_type) not in parsers:
                outcome = "deferred"
            else:
                try:
                    envelope = parsers[
                        (base.schema_version, base.event_type)
                    ](encoded)
                except ValueError:
                    outcome = "pending"
                else:
                    existing = await session.scalar(
                        select(InboxEvent.id).where(
                            InboxEvent.consumer == consumer,
                            InboxEvent.event_id == envelope.event_id,
                        )
                    )
                    if existing is not None:
                        outcome = "duplicate"
                    else:
                        await revalidate(session, envelope)
                        inserted = await session.scalar(
                            insert(InboxEvent)
                            .values(
                                consumer=consumer,
                                event_id=envelope.event_id,
                                event_type=envelope.event_type,
                            )
                            .on_conflict_do_nothing(
                                constraint="uq_inbox_consumer_event"
                            )
                            .returning(InboxEvent.id)
                        )
                        if inserted is None:
                            outcome = "duplicate"
                        else:
                            await apply_effect(session, envelope)
                            outcome = "processed"
    except EventAuthorityError:
        outcome = "rejected"

    if outcome == "deferred":
        return outcome
    if outcome in {"rejected", "pending"}:
        return await _reject(
            redis,
            delivery,
            error_code=(
                "event_not_authoritative"
                if outcome == "rejected"
                else "contract_invalid"
            ),
            poison_delivery_limit=poison_delivery_limit,
            waitaof_timeout_ms=waitaof_timeout_ms,
        )
    await _ack(redis, delivery)
    return outcome
