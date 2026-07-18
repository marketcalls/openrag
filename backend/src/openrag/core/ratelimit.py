from collections.abc import Awaitable, Callable

from fastapi import Request
from redis.asyncio import Redis

from openrag.core.errors import RateLimitExceeded


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
