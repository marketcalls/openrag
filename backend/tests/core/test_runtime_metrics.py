import asyncio
from typing import cast

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from sqlalchemy.ext.asyncio import AsyncEngine

from openrag.core.runtime_metrics import RuntimeMetricsSampler, database_pool_utilization
from openrag.core.telemetry import TelemetryRuntime


class _Pool:
    def size(self) -> int:
        return 10

    def checkedout(self) -> int:
        return 6


class _SyncEngine:
    pool = _Pool()


class _Engine:
    sync_engine = _SyncEngine()


def test_database_pool_utilization_uses_declared_process_capacity() -> None:
    engine = cast(AsyncEngine, _Engine())

    assert database_pool_utilization(engine, capacity=15) == pytest.approx(0.4)


async def test_sampler_emits_real_runtime_gauges_and_stops_cleanly() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    runtime = TelemetryRuntime(
        endpoint="memory://",
        resource=Resource.create({"service.name": "test"}),
        meter_provider=provider,
    )
    sampler = RuntimeMetricsSampler(
        runtime=runtime,
        engine=cast(AsyncEngine, _Engine()),
        database_capacity=15,
        interval_seconds=0.001,
    )

    sampler.start()
    await asyncio.sleep(0.01)
    await sampler.stop()

    data = reader.get_metrics_data()
    metrics = {
        metric.name: metric
        for resource_metric in data.resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
    }
    assert "event_loop.lag_seconds" in metrics
    assert "db.pool_utilization_ratio" in metrics
    pool_points = metrics["db.pool_utilization_ratio"].data.data_points
    assert pool_points[-1].value == pytest.approx(0.4)
    runtime.shutdown()
