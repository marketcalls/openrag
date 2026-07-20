from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource

from openrag.core.config import Settings
from openrag.core.telemetry import TelemetryRuntime, build_telemetry


def test_telemetry_is_noop_without_endpoint() -> None:
    runtime = build_telemetry(Settings(_env_file=None, otel_endpoint=None))

    assert runtime.export_enabled is False
    assert runtime.tracer_provider is None
    assert runtime.meter_provider is None
    assert runtime.logger_provider is None
    runtime.shutdown()


def test_telemetry_builds_bounded_batch_exporters() -> None:
    runtime = build_telemetry(
        Settings(
            _env_file=None,
            otel_endpoint='http://otel-collector:4317',
            environment='test',
            release='2026.07.20',
        )
    )

    assert runtime.export_enabled is True
    assert runtime.endpoint == 'http://otel-collector:4317'
    assert runtime.tracer_provider is not None
    assert runtime.meter_provider is not None
    assert runtime.logger_provider is not None
    assert runtime.resource.attributes['service.name'] == 'openrag'
    assert runtime.resource.attributes['deployment.environment.name'] == 'test'
    assert runtime.resource.attributes['service.version'] == '2026.07.20'
    runtime.shutdown()


def test_rag_metrics_have_only_bounded_operational_labels() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    runtime = TelemetryRuntime(
        endpoint='memory://',
        resource=Resource.create({'service.name': 'test'}),
        meter_provider=provider,
    )

    runtime.record_rag_run(
        route='rag',
        outcome='grounded',
        error_category='none',
        latency_ms=1200,
        ttft_ms=220,
        retrieval_pass_ratio=1.0,
        citation_coverage_ratio=0.75,
        estimated_cost_microusd=4200,
    )
    runtime.record_evaluation(
        groundedness=0.95,
        answer_relevance=0.9,
        correct_refusal=1.0,
    )
    runtime.record_runtime_health(
        event_loop_lag_seconds=0.012,
        database_pool_utilization_ratio=0.5,
    )

    data = reader.get_metrics_data()
    metrics = {
        metric.name: metric
        for resource_metric in data.resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
    }
    assert {
        'rag.runs',
        'rag.latency_ms',
        'rag.ttft_ms',
        'retrieval.pass_ratio',
        'citation.coverage_ratio',
        'rag.estimated_cost_microusd',
        'evaluation.groundedness_ratio',
        'evaluation.answer_relevance_ratio',
        'evaluation.correct_refusal_ratio',
        'event_loop.lag_seconds',
        'db.pool_utilization_ratio',
    } <= set(metrics)
    for metric in metrics.values():
        for point in metric.data.data_points:
            assert set(point.attributes) <= {'route', 'outcome', 'error_category'}
    runtime.shutdown()
