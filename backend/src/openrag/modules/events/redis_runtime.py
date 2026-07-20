"""Fail-closed construction for the isolated durable event Redis client."""

from pathlib import Path
from typing import cast

from redis.asyncio import Redis

from openrag.core.config import Settings


class EventRedisConfigurationError(RuntimeError):
    """Safe configuration error that never includes secret material."""


def read_event_redis_password(settings: Settings) -> str:
    path_value = settings.event_redis_password_file
    if path_value is None:
        raise EventRedisConfigurationError(
            "event_redis_password_file_required"
        )
    path = Path(path_value)
    try:
        if not path.is_file() or path.stat().st_size > 1024:
            raise EventRedisConfigurationError(
                "event_redis_password_file_invalid"
            )
        password = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise EventRedisConfigurationError(
            "event_redis_password_file_unreadable"
        ) from exc
    if not 16 <= len(password) <= 512:
        raise EventRedisConfigurationError("event_redis_password_invalid")
    return password


def build_event_redis(settings: Settings) -> Redis:
    if settings.event_redis_url is None:
        raise EventRedisConfigurationError("event_redis_url_required")
    return cast(
        Redis,
        Redis.from_url(
            settings.event_redis_url,
            password=read_event_redis_password(settings),
            socket_connect_timeout=3,
            socket_timeout=20,
            health_check_interval=15,
        ),
    )
