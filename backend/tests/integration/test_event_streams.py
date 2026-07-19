import hashlib
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from testcontainers.redis import RedisContainer

from openrag.modules.documents.lifecycle import DocumentVersionState
from openrag.modules.events.consumer import StreamDelivery, consume_one
from openrag.modules.events.dispatcher import claim_outbox, dispatch_claim
from openrag.modules.events.envelopes import (
    DocumentVersionLifecycleV1,
    DocumentVersionRebuildRequestedV1,
    build_envelope,
    parse_base_envelope,
)
from openrag.modules.events.models import InboxEvent, OutboxEvent
from openrag.modules.events.outbox import add_registered_event
from openrag.modules.events.readiness import check_event_transport
from openrag.modules.events.streams import (
    DOCUMENT_COMMANDS_STREAM,
    DOCUMENT_EVENTS_DLQ_STREAM,
    DOCUMENT_EVENTS_GROUP,
    DOCUMENT_EVENTS_STREAM,
    ensure_streams,
)


@pytest.fixture(scope="module")
def durable_event_redis_url() -> Iterator[str]:
    container = RedisContainer("redis:7.4-alpine").with_command(
        "redis-server --appendonly yes --appendfsync always --save ''"
    )
    with container:
        yield (
            f"redis://{container.get_container_host_ip()}:"
            f"{container.get_exposed_port(6379)}/0"
        )


@pytest.fixture
async def durable_event_redis(
    durable_event_redis_url: str,
) -> AsyncIterator[Redis]:
    client = Redis.from_url(durable_event_redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


async def test_real_stream_is_provisioned_and_aof_confirmed(
    durable_event_redis: Redis,
) -> None:
    await ensure_streams(durable_event_redis)

    message_id = await durable_event_redis.xadd(
        DOCUMENT_EVENTS_STREAM,
        {
            b"envelope_bytes": b"{}",
            b"envelope_digest": b"0" * 64,
        },
    )
    local_fsyncs, replica_fsyncs = await durable_event_redis.waitaof(1, 0, 5000)
    groups = await durable_event_redis.xinfo_groups(DOCUMENT_EVENTS_STREAM)
    messages = await durable_event_redis.xrange(
        DOCUMENT_EVENTS_STREAM,
        min=message_id,
        max=message_id,
    )

    assert local_fsyncs >= 1
    assert replica_fsyncs == 0
    group_name = groups[0].get(b"name", groups[0].get("name"))
    assert group_name in {
        DOCUMENT_EVENTS_GROUP,
        DOCUMENT_EVENTS_GROUP.encode(),
    }
    assert set(messages[0][1]) == {b"envelope_bytes", b"envelope_digest"}
    assert (await check_event_transport(durable_event_redis)).ready is True


async def test_real_dispatcher_marks_postgres_only_after_waitaof(
    durable_event_redis: Redis,
    engine: AsyncEngine,
) -> None:
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory.begin() as session:
        event = add_registered_event(
            session,
            payload=DocumentVersionLifecycleV1(
                document_id=UUID("71000000-0000-0000-0000-000000000001"),
                previous_state=DocumentVersionState.REVIEW,
                new_state=DocumentVersionState.APPROVED,
            ),
            org_id=UUID("72000000-0000-0000-0000-000000000002"),
            workspace_id=UUID("73000000-0000-0000-0000-000000000003"),
            aggregate_id=UUID("74000000-0000-0000-0000-000000000004"),
            lifecycle_revision=2,
            correlation_id=UUID("75000000-0000-0000-0000-000000000005"),
            occurred_at=datetime(2026, 7, 20, 1, tzinfo=UTC),
            event_id=UUID("76000000-0000-0000-0000-000000000006"),
        )
    claim = (
        await claim_outbox(
            session_factory,
            owner="integration-relay",
            batch_size=1,
            lease_seconds=30,
        )
    )[0]

    async with durable_event_redis.client() as connection:
        result = await dispatch_claim(
            session_factory,
            connection,  # type: ignore[arg-type]
            claim,
            waitaof_timeout_ms=5000,
        )

    assert result == "published"
    async with session_factory() as session:
        persisted = await session.get(OutboxEvent, event.id)
        assert persisted is not None
    assert persisted.published_at is not None
    assert persisted.published_message_id is not None


async def test_real_dispatcher_routes_start_commands_to_dedicated_stream(
    durable_event_redis: Redis,
    engine: AsyncEngine,
) -> None:
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory.begin() as session:
        event = add_registered_event(
            session,
            payload=DocumentVersionRebuildRequestedV1(
                document_id=UUID("76100000-0000-0000-0000-000000000001"),
                authority_generation_id=UUID(
                    "76100000-0000-0000-0000-000000000002"
                ),
            ),
            org_id=UUID("76100000-0000-0000-0000-000000000003"),
            workspace_id=UUID("76100000-0000-0000-0000-000000000004"),
            aggregate_id=UUID("76100000-0000-0000-0000-000000000005"),
            lifecycle_revision=1,
            correlation_id=UUID("76100000-0000-0000-0000-000000000006"),
            occurred_at=datetime(2026, 7, 20, 1, tzinfo=UTC),
        )
    claim = (
        await claim_outbox(
            session_factory,
            owner="integration-relay",
            batch_size=1,
            lease_seconds=30,
        )
    )[0]

    result = await dispatch_claim(
        session_factory,
        durable_event_redis,  # type: ignore[arg-type]
        claim,
        waitaof_timeout_ms=5000,
    )

    assert result == "published"
    messages = await durable_event_redis.xrange(DOCUMENT_COMMANDS_STREAM)
    assert len(messages) == 1
    assert messages[0][1][b"envelope_digest"] == event.envelope_digest.encode()


async def test_attested_future_schema_stays_pending_then_new_consumer_reclaims(
    durable_event_redis: Redis,
    engine: AsyncEngine,
) -> None:
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    future_type = "document.version.future.v2"
    async with session_factory.begin() as session:
        event = add_registered_event(
            session,
            payload=DocumentVersionLifecycleV1(
                document_id=UUID("77000000-0000-0000-0000-000000000001"),
                previous_state=DocumentVersionState.REVIEW,
                new_state=DocumentVersionState.APPROVED,
            ),
            org_id=UUID("77000000-0000-0000-0000-000000000002"),
            workspace_id=UUID("77000000-0000-0000-0000-000000000003"),
            aggregate_id=UUID("77000000-0000-0000-0000-000000000004"),
            lifecycle_revision=2,
            correlation_id=UUID("77000000-0000-0000-0000-000000000005"),
            occurred_at=datetime(2026, 7, 20, 2, tzinfo=UTC),
            event_id=UUID("77000000-0000-0000-0000-000000000006"),
        )
        event.payload["schema_version"] = 2
        event.payload["event_type"] = future_type
        encoded = json.dumps(
            event.payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        event.event_type = future_type
        event.envelope_digest = hashlib.sha256(encoded).hexdigest()
        event.published_stream = DOCUMENT_EVENTS_STREAM
    await ensure_streams(durable_event_redis)
    message_id = await durable_event_redis.xadd(
        DOCUMENT_EVENTS_STREAM,
        {
            b"envelope_bytes": encoded,
            b"envelope_digest": event.envelope_digest.encode(),
        },
    )
    await durable_event_redis.waitaof(1, 0, 5000)
    fresh = await durable_event_redis.xreadgroup(
        DOCUMENT_EVENTS_GROUP,
        "old-consumer",
        {DOCUMENT_EVENTS_STREAM: ">"},
        count=1,
    )
    first_id, first_fields = fresh[0][1][0]
    old_delivery = StreamDelivery(
        stream=DOCUMENT_EVENTS_STREAM,
        group=DOCUMENT_EVENTS_GROUP,
        message_id=first_id.decode(),
        fields=first_fields,
        delivery_count=1,
    )

    async def unexpected(*_args: object) -> None:
        raise AssertionError("old consumer must defer the future schema")

    old_result = await consume_one(
        session_factory,
        durable_event_redis,  # type: ignore[arg-type]
        consumer="old-projector-v1",
        delivery=old_delivery,
        revalidate=unexpected,
        apply_effect=unexpected,
    )
    pending = await durable_event_redis.xpending(
        DOCUMENT_EVENTS_STREAM,
        DOCUMENT_EVENTS_GROUP,
    )
    assert old_result == "deferred"
    assert pending["pending"] == 1

    claimed = await durable_event_redis.xautoclaim(
        DOCUMENT_EVENTS_STREAM,
        DOCUMENT_EVENTS_GROUP,
        "new-consumer",
        min_idle_time=0,
        start_id="0-0",
        count=1,
    )
    claimed_id, claimed_fields = claimed[1][0]
    assert claimed_id == message_id
    new_delivery = StreamDelivery(
        stream=DOCUMENT_EVENTS_STREAM,
        group=DOCUMENT_EVENTS_GROUP,
        message_id=claimed_id.decode(),
        fields=claimed_fields,
        delivery_count=2,
    )
    effects: list[bool] = []

    async def allow(_session: object, _envelope: object) -> None:
        return None

    async def effect(_session: object, _envelope: object) -> None:
        effects.append(True)

    new_result = await consume_one(
        session_factory,
        durable_event_redis,  # type: ignore[arg-type]
        consumer="new-projector-v2",
        delivery=new_delivery,
        revalidate=allow,  # type: ignore[arg-type]
        apply_effect=effect,  # type: ignore[arg-type]
        schema_parsers={(2, future_type): parse_base_envelope},
    )
    duplicate = await consume_one(
        session_factory,
        durable_event_redis,  # type: ignore[arg-type]
        consumer="new-projector-v2",
        delivery=new_delivery,
        revalidate=allow,  # type: ignore[arg-type]
        apply_effect=effect,  # type: ignore[arg-type]
        schema_parsers={(2, future_type): parse_base_envelope},
    )
    pending_after = await durable_event_redis.xpending(
        DOCUMENT_EVENTS_STREAM,
        DOCUMENT_EVENTS_GROUP,
    )

    assert (new_result, duplicate) == ("processed", "duplicate")
    assert effects == [True]
    assert pending_after["pending"] == 0


async def test_unattested_future_schema_flood_does_not_retain_pending_entries(
    durable_event_redis: Redis,
    engine: AsyncEngine,
) -> None:
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await ensure_streams(durable_event_redis)
    future_type = "document.version.forged.v9"
    for index in range(10):
        envelope = build_envelope(
            payload=DocumentVersionLifecycleV1(
                document_id=UUID(int=1000 + index),
                previous_state=DocumentVersionState.REVIEW,
                new_state=DocumentVersionState.APPROVED,
            ),
            org_id=UUID(int=2000 + index),
            workspace_id=UUID(int=3000 + index),
            aggregate_id=UUID(int=4000 + index),
            lifecycle_revision=2,
            correlation_id=UUID(int=5000 + index),
            occurred_at=datetime(2026, 7, 20, 2, tzinfo=UTC),
            event_id=UUID(int=6000 + index),
        )
        raw = envelope.model_dump(mode="json")
        raw["schema_version"] = 9
        raw["event_type"] = future_type
        encoded = json.dumps(
            raw,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        await durable_event_redis.xadd(
            DOCUMENT_EVENTS_STREAM,
            {
                b"envelope_bytes": encoded,
                b"envelope_digest": hashlib.sha256(encoded).hexdigest().encode(),
            },
        )
    await durable_event_redis.waitaof(1, 0, 5000)
    deliveries = await durable_event_redis.xreadgroup(
        DOCUMENT_EVENTS_GROUP,
        "old-consumer",
        {DOCUMENT_EVENTS_STREAM: ">"},
        count=10,
    )

    async def unexpected(*_args: object) -> None:
        raise AssertionError("unattested future schema must not run callbacks")

    results = []
    for message_id, fields in deliveries[0][1]:
        results.append(
            await consume_one(
                session_factory,
                durable_event_redis,  # type: ignore[arg-type]
                consumer="old-projector-v1",
                delivery=StreamDelivery(
                    stream=DOCUMENT_EVENTS_STREAM,
                    group=DOCUMENT_EVENTS_GROUP,
                    message_id=message_id.decode(),
                    fields=fields,
                    delivery_count=1,
                ),
                revalidate=unexpected,
                apply_effect=unexpected,
                poison_delivery_limit=1,
            )
        )

    pending = await durable_event_redis.xpending(
        DOCUMENT_EVENTS_STREAM,
        DOCUMENT_EVENTS_GROUP,
    )
    async with session_factory() as session:
        inbox_count = await session.scalar(
            select(func.count()).select_from(InboxEvent)
        )
    assert results == ["rejected"] * 10
    assert pending["pending"] == 0
    assert await durable_event_redis.xlen(DOCUMENT_EVENTS_DLQ_STREAM) == 10
    assert inbox_count == 0
