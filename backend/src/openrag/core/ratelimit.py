import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from fastapi import Request

from openrag.core.errors import RateLimitExceeded


class FixedWindowLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        now = time.monotonic()
        window = [
            timestamp
            for timestamp in self._hits[key]
            if now - timestamp < self.window_seconds
        ]
        if len(window) >= self.limit:
            self._hits[key] = window
            raise RateLimitExceeded("rate limit exceeded, retry later")
        window.append(now)
        self._hits[key] = window


def rate_limit(
    scope: str,
    limit: int = 10,
    window_seconds: int = 60,
) -> Callable[[Request], Awaitable[None]]:
    async def guard(request: Request) -> None:
        limiters: dict[tuple[str, int, int], FixedWindowLimiter] | None = getattr(
            request.app.state,
            "rate_limiters",
            None,
        )
        if limiters is None:
            limiters = {}
            request.app.state.rate_limiters = limiters

        limiter = limiters.setdefault(
            (scope, limit, window_seconds),
            FixedWindowLimiter(limit, window_seconds),
        )
        client_ip = request.client.host if request.client else "unknown"
        limiter.check(f"{scope}:{client_ip}")

    return guard
