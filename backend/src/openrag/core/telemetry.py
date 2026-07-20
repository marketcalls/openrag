"""Correlation context and bounded recursive redaction for telemetry metadata."""

import math
import re
from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from itertools import islice
from pathlib import Path
from typing import Protocol, TypeGuard
from uuid import UUID, uuid4

from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Counter, Histogram
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from openrag.core.config import Settings

type TraceToken = Token[str | None]
type JsonSafe = None | bool | int | float | str | dict[str, JsonSafe] | list[JsonSafe]


class GaugeInstrument(Protocol):
    def set(self, amount: int | float, attributes: Mapping[str, str]) -> None: ...

_TRACE_RE = re.compile(r"^[0-9a-f]{32}$")
_TRACE_ID: ContextVar[str | None] = ContextVar("openrag_trace_id", default=None)
_MAX_DEPTH = 6
_MAX_ITEMS = 50
_MAX_STRING = 512
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set_cookie",
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "master_key",
        "private_key",
        "credential",
        "credentials",
        "prompt",
        "response",
        "request_body",
        "response_body",
        "body",
        "query",
        "retrieved_text",
        "document_text",
        "chunk_text",
        "content",
        "memory",
        "filename",
        "file_name",
        "tool_arguments",
        "tool_result",
        "reasoning",
        "chain_of_thought",
        "exception_message",
        "stacktrace",
        "exc_info",
    }
)


@dataclass(slots=True)
class TelemetryRuntime:
    """Owns bounded OTLP providers without mutating process-global SDK state."""

    endpoint: str | None
    resource: Resource
    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    logger_provider: LoggerProvider | None = None
    _closed: bool = field(default=False, init=False)
    _rag_runs: Counter | None = field(default=None, init=False)
    _rag_latency: Histogram | None = field(default=None, init=False)
    _rag_ttft: Histogram | None = field(default=None, init=False)
    _retrieval_pass: GaugeInstrument | None = field(default=None, init=False)
    _citation_coverage: GaugeInstrument | None = field(default=None, init=False)
    _rag_cost: Counter | None = field(default=None, init=False)
    _evaluation_groundedness: GaugeInstrument | None = field(default=None, init=False)
    _evaluation_relevance: GaugeInstrument | None = field(default=None, init=False)
    _evaluation_refusal: GaugeInstrument | None = field(default=None, init=False)

    @property
    def export_enabled(self) -> bool:
        return self.endpoint is not None

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.logger_provider is not None:
            self.logger_provider.shutdown()
        if self.meter_provider is not None:
            self.meter_provider.shutdown()
        if self.tracer_provider is not None:
            self.tracer_provider.shutdown()

    def _ensure_rag_instruments(self) -> None:
        if self.meter_provider is None or self._rag_runs is not None:
            return
        meter = self.meter_provider.get_meter("openrag.rag")
        self._rag_runs = meter.create_counter(
            "rag.runs",
            unit="{run}",
            description="Terminal RAG runs by bounded outcome labels.",
        )
        self._rag_latency = meter.create_histogram("rag.latency_ms", unit="ms")
        self._rag_ttft = meter.create_histogram("rag.ttft_ms", unit="ms")
        self._retrieval_pass = meter.create_gauge("retrieval.pass_ratio", unit="1")
        self._citation_coverage = meter.create_gauge("citation.coverage_ratio", unit="1")
        self._rag_cost = meter.create_counter("rag.estimated_cost_microusd", unit="{microusd}")

    def record_rag_run(
        self,
        *,
        route: str,
        outcome: str,
        error_category: str,
        latency_ms: int,
        ttft_ms: int | None,
        retrieval_pass_ratio: float,
        citation_coverage_ratio: float,
        estimated_cost_microusd: int,
    ) -> None:
        self._ensure_rag_instruments()
        attributes = {
            "route": route,
            "outcome": outcome,
            "error_category": error_category,
        }
        if self._rag_runs is not None:
            self._rag_runs.add(1, attributes)
        if self._rag_latency is not None:
            self._rag_latency.record(latency_ms, attributes)
        if self._rag_ttft is not None and ttft_ms is not None:
            self._rag_ttft.record(ttft_ms, attributes)
        if self._retrieval_pass is not None:
            self._retrieval_pass.set(retrieval_pass_ratio, attributes)
        if self._citation_coverage is not None:
            self._citation_coverage.set(citation_coverage_ratio, attributes)
        if self._rag_cost is not None:
            self._rag_cost.add(estimated_cost_microusd, attributes)

    def record_evaluation(
        self,
        *,
        groundedness: float | None,
        answer_relevance: float | None,
        correct_refusal: float | None,
    ) -> None:
        if self.meter_provider is None:
            return
        if self._evaluation_groundedness is None:
            meter = self.meter_provider.get_meter("openrag.evaluations")
            self._evaluation_groundedness = meter.create_gauge(
                "evaluation.groundedness_ratio", unit="1"
            )
            self._evaluation_relevance = meter.create_gauge(
                "evaluation.answer_relevance_ratio", unit="1"
            )
            self._evaluation_refusal = meter.create_gauge(
                "evaluation.correct_refusal_ratio", unit="1"
            )
        if groundedness is not None:
            self._evaluation_groundedness.set(groundedness, {})
        if answer_relevance is not None and self._evaluation_relevance is not None:
            self._evaluation_relevance.set(answer_relevance, {})
        if correct_refusal is not None and self._evaluation_refusal is not None:
            self._evaluation_refusal.set(correct_refusal, {})


_ACTIVE_RUNTIME: TelemetryRuntime | None = None


def build_telemetry(settings: Settings) -> TelemetryRuntime:
    """Build no-op local providers unless an explicit OTLP endpoint is configured."""

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": settings.release,
            "deployment.environment.name": settings.environment,
        }
    )
    endpoint = settings.otel_endpoint.strip() if settings.otel_endpoint else None
    if not endpoint:
        return TelemetryRuntime(endpoint=None, resource=resource)

    span_exporter = OTLPSpanExporter(
        endpoint=endpoint,
        insecure=settings.otel_insecure,
        timeout=settings.otel_export_timeout_ms / 1000,
    )
    tracer_provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(settings.otel_trace_sample_ratio)),
    )
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            span_exporter,
            max_queue_size=settings.otel_batch_queue_size,
            schedule_delay_millis=settings.otel_batch_delay_ms,
            max_export_batch_size=min(
                settings.otel_batch_size,
                settings.otel_batch_queue_size,
            ),
            export_timeout_millis=settings.otel_export_timeout_ms,
        )
    )

    metric_exporter = OTLPMetricExporter(
        endpoint=endpoint,
        insecure=settings.otel_insecure,
        timeout=settings.otel_export_timeout_ms / 1000,
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                metric_exporter,
                export_interval_millis=settings.otel_metric_interval_ms,
                export_timeout_millis=settings.otel_export_timeout_ms,
            )
        ],
    )

    log_exporter = OTLPLogExporter(
        endpoint=endpoint,
        insecure=settings.otel_insecure,
        timeout=settings.otel_export_timeout_ms / 1000,
    )
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            log_exporter,
            max_queue_size=settings.otel_batch_queue_size,
            schedule_delay_millis=settings.otel_batch_delay_ms,
            max_export_batch_size=min(
                settings.otel_batch_size,
                settings.otel_batch_queue_size,
            ),
            export_timeout_millis=settings.otel_export_timeout_ms,
        )
    )
    return TelemetryRuntime(
        endpoint=endpoint,
        resource=resource,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        logger_provider=logger_provider,
    )


def activate_telemetry(runtime: TelemetryRuntime) -> None:
    global _ACTIVE_RUNTIME
    _ACTIVE_RUNTIME = runtime


def deactivate_telemetry(runtime: TelemetryRuntime) -> None:
    global _ACTIVE_RUNTIME
    if _ACTIVE_RUNTIME is runtime:
        _ACTIVE_RUNTIME = None


def record_active_rag_run(
    *,
    route: str,
    outcome: str,
    error_category: str,
    latency_ms: int,
    ttft_ms: int | None,
    retrieval_pass_ratio: float,
    citation_coverage_ratio: float,
    estimated_cost_microusd: int,
) -> None:
    runtime = _ACTIVE_RUNTIME
    if runtime is None:
        return
    runtime.record_rag_run(
        route=route,
        outcome=outcome,
        error_category=error_category,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        retrieval_pass_ratio=retrieval_pass_ratio,
        citation_coverage_ratio=citation_coverage_ratio,
        estimated_cost_microusd=estimated_cost_microusd,
    )


def record_active_evaluation(
    *,
    groundedness: float | None,
    answer_relevance: float | None,
    correct_refusal: float | None,
) -> None:
    runtime = _ACTIVE_RUNTIME
    if runtime is not None:
        runtime.record_evaluation(
            groundedness=groundedness,
            answer_relevance=answer_relevance,
            correct_refusal=correct_refusal,
        )


def valid_trace_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _TRACE_RE.fullmatch(value) is not None


def new_trace_id(inbound: object = None) -> str:
    return inbound if valid_trace_id(inbound) else uuid4().hex


def set_trace_id(trace_id: str) -> TraceToken:
    if not valid_trace_id(trace_id):
        raise ValueError("trace_id_invalid")
    return _TRACE_ID.set(trace_id)


def reset_trace_id(token: TraceToken) -> None:
    _TRACE_ID.reset(token)


def current_trace_id() -> str:
    trace_id = _TRACE_ID.get()
    if trace_id is None:
        trace_id = new_trace_id()
        _TRACE_ID.set(trace_id)
    return trace_id


def _sensitive_key(key: str) -> bool:
    normalized = key.strip().casefold().replace("-", "_")
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("_password")
        or normalized.endswith("_secret")
        or normalized.endswith("_token")
        or normalized.endswith("_api_key")
        or normalized.endswith("_credential")
    )


def _bounded_string(value: str) -> str:
    if len(value) <= _MAX_STRING:
        return value
    return f"{value[:_MAX_STRING]}[TRUNCATED]"


def safe_log_fields(value: object, *, _depth: int = 0) -> JsonSafe:
    """Convert structured values without invoking untrusted ``repr`` methods."""

    if _depth > _MAX_DEPTH:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[NON_FINITE]"
    if isinstance(value, str):
        return _bounded_string(value)
    if isinstance(value, bytes):
        return f"[BINARY:{len(value)}]"
    if isinstance(value, BaseException):
        return {"exception_type": type(value).__name__}
    if isinstance(value, (UUID, datetime, date, Path, Enum)):
        return _bounded_string(str(value))
    if isinstance(value, Mapping):
        result: dict[str, JsonSafe] = {}
        mapping_items = list(islice(value.items(), _MAX_ITEMS))
        for raw_key, nested in mapping_items:
            key = _bounded_string(raw_key if isinstance(raw_key, str) else type(raw_key).__name__)
            result[key] = (
                "[REDACTED]" if _sensitive_key(key) else safe_log_fields(nested, _depth=_depth + 1)
            )
        if len(value) > _MAX_ITEMS:
            result["__truncated__"] = len(value) - _MAX_ITEMS
        return result
    if isinstance(value, Sequence):
        sequence_items: list[JsonSafe] = [
            safe_log_fields(item, _depth=_depth + 1) for item in value[:_MAX_ITEMS]
        ]
        if len(value) > _MAX_ITEMS:
            sequence_items.append("[TRUNCATED]")
        return sequence_items
    return {"type": type(value).__name__}
