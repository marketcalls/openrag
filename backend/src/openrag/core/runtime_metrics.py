"""Lifecycle-safe sampling for process and database capacity signals."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from typing import Protocol, cast

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from openrag.core.telemetry import TelemetryRuntime


class _PoolState(Protocol):
    def size(self) -> int: ...

    def checkedout(self) -> int: ...


type QueueAgeProvider = Callable[[], Awaitable[Mapping[str, float]]]


def database_pool_utilization(engine: AsyncEngine, *, capacity: int) -> float:
    if capacity <= 0:
        raise ValueError("database_pool_capacity_invalid")
    pool = cast(_PoolState, engine.sync_engine.pool)
    return min(1.0, max(0.0, pool.checkedout() / capacity))


class RuntimeMetricsSampler:
    """Measure scheduler drift and checked-out SQL connections without labels."""

    def __init__(
        self,
        *,
        runtime: TelemetryRuntime,
        engine: AsyncEngine | None,
        database_capacity: int,
        interval_seconds: float,
        queue_age_provider: QueueAgeProvider | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("runtime_metric_interval_invalid")
        self._runtime = runtime
        self._engine = engine
        self._database_capacity = database_capacity
        self._interval_seconds = interval_seconds
        self._queue_age_provider = queue_age_provider
        self._task: asyncio.Task[None] | None = None
        self._logger = structlog.get_logger("openrag.runtime_metrics")

    def start(self) -> None:
        if not self._runtime.export_enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="openrag-runtime-metrics")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            deadline = loop.time() + self._interval_seconds
            await asyncio.sleep(self._interval_seconds)
            observed = loop.time()
            utilization = (
                database_pool_utilization(self._engine, capacity=self._database_capacity)
                if self._engine is not None
                else None
            )
            self._runtime.record_runtime_health(
                event_loop_lag_seconds=max(0.0, observed - deadline),
                database_pool_utilization_ratio=utilization,
            )
            if self._queue_age_provider is not None:
                try:
                    queue_ages = await self._queue_age_provider()
                except Exception as exc:  # noqa: BLE001 - sampling must continue
                    self._logger.warning(
                        "queue_metric_sampling_failed",
                        exception_type=type(exc).__name__,
                    )
                    continue
                for queue, age_seconds in queue_ages.items():
                    self._runtime.record_queue_age(queue=queue, age_seconds=age_seconds)
