import time
from collections.abc import Awaitable, Callable

from fastapi import Request
from redis.asyncio import Redis

from openrag.core.errors import RateLimitExceeded


class FixedWindowLimiter:
    """Small in-process fallback for apps without shared Redis state."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._counts: dict[str, tuple[int, int]] = {}

    def check(self, key: str) -> None:
        bucket = int(time.time() // self.window_seconds)
        prior_bucket, prior_count = self._counts.get(key, (bucket, 0))
        count = prior_count + 1 if prior_bucket == bucket else 1
        self._counts[key] = (bucket, count)
        if count > self.limit:
            raise RateLimitExceeded("rate limit exceeded, retry later")


class RedisFixedWindowLimiter:
    """Multi-worker fixed-window limiter over shared Redis."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds

    async def check(self, redis: Redis, key: str) -> None:
        bucket = int(time.time() // self.window_seconds)
        redis_key = f"ratelimit:{key}:{bucket}"
        count = await redis.incr(redis_key)
        if count == 1:
            await redis.expire(redis_key, self.window_seconds)
        if count > self.limit:
            raise RateLimitExceeded("rate limit exceeded, retry later")


async def check_rate_limit(
    redis: Redis,
    key: str,
    limit: int,
    window_seconds: int,
) -> None:
    """Increment a shared fixed-window counter and reject excess requests."""
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_seconds)
    if count > limit:
        raise RateLimitExceeded("rate limit exceeded, retry later")


def rate_limit(
    scope: str,
    limit: int = 10,
    window_seconds: int = 60,
) -> Callable[[Request], Awaitable[None]]:
    async def guard(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        redis: Redis = request.app.state.redis
        await check_rate_limit(
            redis,
            f"rl:{scope}:{client_ip}",
            limit,
            window_seconds,
        )

    return guard
