"""Runtime composition for the isolated durable event relay component."""

import argparse
import asyncio
from pathlib import Path
from typing import cast

from redis.asyncio import Redis
from sqlalchemy import select

from openrag.core.config import Settings, get_settings
from openrag.core.db import build_engine, build_session_factory
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
from openrag.modules.events.streams import StreamAdminRedis, ensure_streams


class EventRuntimeConfigurationError(RuntimeError):
    """Fail-closed event component configuration error."""


def _read_event_redis_password(settings: Settings) -> str:
    path_value = settings.event_redis_password_file
    if path_value is None:
        raise EventRuntimeConfigurationError("event_redis_password_file_required")
    path = Path(path_value)
    try:
        if not path.is_file() or path.stat().st_size > 1024:
            raise EventRuntimeConfigurationError("event_redis_password_file_invalid")
        password = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise EventRuntimeConfigurationError(
            "event_redis_password_file_unreadable"
        ) from exc
    if not 16 <= len(password) <= 512:
        raise EventRuntimeConfigurationError("event_redis_password_invalid")
    return password


def _build_event_redis(settings: Settings) -> Redis:
    if settings.event_redis_url is None:
        raise EventRuntimeConfigurationError("event_redis_url_required")
    return cast(
        Redis,
        Redis.from_url(
            settings.event_redis_url,
            password=_read_event_redis_password(settings),
            socket_connect_timeout=3,
            socket_timeout=10,
            health_check_interval=15,
        ),
    )


async def dispatch_outbox_once(
    settings: Settings | None = None,
) -> dict[str, int]:
    """Provision topology, claim a bounded batch, and durably relay it."""

    resolved = settings or get_settings()
    engine = build_engine(resolved.database_url)
    session_factory = build_session_factory(engine)
    redis = _build_event_redis(resolved)
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
    redis = _build_event_redis(resolved)
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


async def event_runtime_readiness(
    settings: Settings | None = None,
) -> EventTransportStatus:
    """Check PostgreSQL plus authenticated event Redis and exact topology."""

    resolved = settings or get_settings()
    engine = build_engine(resolved.database_url)
    redis = _build_event_redis(resolved)
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
