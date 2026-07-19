from collections.abc import AsyncIterator, Iterator

import pytest
from redis.asyncio import Redis
from testcontainers.redis import RedisContainer

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
