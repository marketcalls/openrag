from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from testcontainers.redis import RedisContainer

from openrag.modules.documents.lifecycle import DocumentVersionState
from openrag.modules.events.dispatcher import claim_outbox, dispatch_claim
from openrag.modules.events.envelopes import DocumentVersionLifecycleV1
from openrag.modules.events.models import OutboxEvent
from openrag.modules.events.outbox import add_registered_event
from openrag.modules.events.readiness import check_event_transport
from openrag.modules.events.streams import (
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
