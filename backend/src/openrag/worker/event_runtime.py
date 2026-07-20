"""Runtime composition for the isolated durable event relay component."""

import argparse
import asyncio
from typing import cast

from sqlalchemy import select

from openrag.core.config import Settings, get_settings
from openrag.core.db import build_engine, build_session_factory
from openrag.modules.documents.lifecycle_projection import (
    DocumentLifecycleRedis,
    consume_document_lifecycle_batch,
)
from openrag.modules.documents.start_events import (
    DocumentStartRedis,
    consume_document_start_batch,
)
from openrag.modules.events.dispatcher import (
    EventRedis,
    claim_outbox,
    dispatch_claim,
)
from openrag.modules.events.readiness import (
    EventTransportStatus,
    ReadinessRedis,
    check_event_transport,
)
from openrag.modules.events.redis_runtime import build_event_redis
from openrag.modules.events.streams import StreamAdminRedis, ensure_streams
from openrag.modules.runs.commands import (
    RunCommandRedis,
    consume_run_command_batch,
)


async def dispatch_outbox_once(
    settings: Settings | None = None,
) -> dict[str, int]:
    """Provision topology, claim a bounded batch, and durably relay it."""

    resolved = settings or get_settings()
    engine = build_engine(resolved.database_url)
    session_factory = build_session_factory(engine)
    redis = build_event_redis(resolved)
    counts = {
        "claimed": 0,
        "published": 0,
        "retry": 0,
        "dead_lettered": 0,
        "lease_lost": 0,
    }
    try:
        async with redis.client() as connection:
            await ensure_streams(cast(StreamAdminRedis, connection))
            claims = await claim_outbox(
                session_factory,
                owner="event-relay",
                batch_size=resolved.event_dispatch_batch_size,
                lease_seconds=resolved.event_dispatch_lease_seconds,
            )
            counts["claimed"] = len(claims)
            for claim in claims:
                result = await dispatch_claim(
                    session_factory,
                    cast(EventRedis, connection),
                    claim,
                    waitaof_timeout_ms=resolved.event_waitaof_timeout_ms,
                )
                counts[result] += 1
    finally:
        await redis.aclose()
        await engine.dispose()
    return counts


async def consume_document_starts_once(
    *,
    consumer: str,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Provision topology and process one reclaimed/fresh command batch."""

    resolved = settings or get_settings()
    engine = build_engine(resolved.database_url)
    session_factory = build_session_factory(engine)
    redis = build_event_redis(resolved)
    try:
        async with redis.client() as connection:
            await ensure_streams(cast(StreamAdminRedis, connection))
            return await consume_document_start_batch(
                session_factory,
                cast(DocumentStartRedis, connection),
                consumer=consumer,
                batch_size=resolved.event_dispatch_batch_size,
                reclaim_idle_ms=max(
                    30_000,
                    resolved.event_dispatch_lease_seconds * 1_000,
                ),
            )
    finally:
        await redis.aclose()
        await engine.dispose()


async def consume_document_lifecycle_once(
    *,
    consumer: str,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Provision topology and process one lifecycle projection batch."""

    resolved = settings or get_settings()
    engine = build_engine(resolved.database_url)
    session_factory = build_session_factory(engine)
    redis = build_event_redis(resolved)
    try:
        async with redis.client() as connection:
            await ensure_streams(cast(StreamAdminRedis, connection))
            return await consume_document_lifecycle_batch(
                session_factory,
                cast(DocumentLifecycleRedis, connection),
                consumer=consumer,
                batch_size=resolved.event_dispatch_batch_size,
                reclaim_idle_ms=max(
                    30_000,
                    resolved.event_dispatch_lease_seconds * 1_000,
                ),
            )
    finally:
        await redis.aclose()
        await engine.dispose()


async def consume_run_commands_once(
    *,
    consumer: str,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Queue attested run commands without holding SQL during execution."""

    resolved = settings or get_settings()
    engine = build_engine(resolved.database_url)
    session_factory = build_session_factory(engine)
    redis = build_event_redis(resolved)
    try:
        async with redis.client() as connection:
            await ensure_streams(cast(StreamAdminRedis, connection))
            return await consume_run_command_batch(
                session_factory,
                cast(RunCommandRedis, connection),
                consumer=consumer,
                batch_size=resolved.event_dispatch_batch_size,
                reclaim_idle_ms=max(
                    30_000,
                    resolved.event_dispatch_lease_seconds * 1_000,
                ),
            )
    finally:
        await redis.aclose()
        await engine.dispose()


async def event_runtime_readiness(
    settings: Settings | None = None,
) -> EventTransportStatus:
    """Check PostgreSQL plus authenticated event Redis and exact topology."""

    resolved = settings or get_settings()
    engine = build_engine(resolved.database_url)
    redis = build_event_redis(resolved)
    try:
        async with engine.connect() as connection:
            await connection.execute(select(1))
        async with redis.client() as connection:
            await ensure_streams(cast(StreamAdminRedis, connection))
            return await check_event_transport(
                cast(ReadinessRedis, connection)
            )
    finally:
        await redis.aclose()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("ready",))
    parser.parse_args()
    asyncio.run(event_runtime_readiness())


if __name__ == "__main__":
    main()
