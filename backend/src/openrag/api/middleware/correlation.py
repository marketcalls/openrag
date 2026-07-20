"""Pure ASGI request correlation that preserves streaming response behavior."""

from time import perf_counter

from opentelemetry.metrics import Counter, Histogram
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, unbind_contextvars

from openrag.core.telemetry import (
    TelemetryRuntime,
    new_trace_id,
    reset_trace_id,
    set_trace_id,
)

_HEADER = b"x-trace-id"


def _inbound_trace_id(scope: Scope) -> str | None:
    values = [value for name, value in scope.get("headers", []) if name.lower() == _HEADER]
    if len(values) != 1:
        return None
    try:
        return str(values[0].decode("ascii"))
    except UnicodeDecodeError:
        return None


class TraceCorrelationMiddleware:
    def __init__(self, app: ASGIApp, telemetry: TelemetryRuntime | None = None) -> None:
        self.app = app
        self.tracer: Tracer | None = None
        self.request_counter: Counter | None = None
        self.request_duration: Histogram | None = None
        if telemetry is not None and telemetry.tracer_provider is not None:
            self.tracer = telemetry.tracer_provider.get_tracer("openrag.http")
        if telemetry is not None and telemetry.meter_provider is not None:
            meter = telemetry.meter_provider.get_meter("openrag.http")
            self.request_counter = meter.create_counter(
                "http.server.requests",
                unit="{request}",
                description="Completed HTTP requests by safe route and status class.",
            )
            self.request_duration = meter.create_histogram(
                "http.server.duration_ms",
                unit="ms",
                description="HTTP request duration in milliseconds.",
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self.tracer is None:
            await self._handle_http(
                scope,
                receive,
                send,
                trace_id=new_trace_id(_inbound_trace_id(scope)),
                span=None,
            )
            return

        method = str(scope.get("method", "UNKNOWN"))
        with self.tracer.start_as_current_span(
            f"HTTP {method}",
            kind=SpanKind.SERVER,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            trace_id = f"{span.get_span_context().trace_id:032x}"
            await self._handle_http(scope, receive, send, trace_id=trace_id, span=span)

    async def _handle_http(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        trace_id: str,
        span: Span | None,
    ) -> None:
        started = perf_counter()
        status_code = 500
        token = set_trace_id(trace_id)
        bind_contextvars(trace_id=trace_id)

        async def correlated_send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != _HEADER
                ]
                headers.append((_HEADER, trace_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, correlated_send)
        except BaseException as exc:
            if span is not None:
                span.set_attribute("exception.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR))
            raise
        finally:
            route = scope.get("route")
            route_template = getattr(route, "path", None)
            safe_route = route_template if isinstance(route_template, str) else "unmatched"
            method = str(scope.get("method", "UNKNOWN"))
            attributes: dict[str, str | int] = {
                "http.request.method": method,
                "http.route": safe_route,
                "http.response.status_code": status_code,
                "status_class": f"{status_code // 100}xx",
            }
            if span is not None:
                span.update_name(f"HTTP {method} {safe_route}")
                for key, value in attributes.items():
                    span.set_attribute(key, value)
                if status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))
            if self.request_counter is not None:
                self.request_counter.add(1, attributes=attributes)
            if self.request_duration is not None:
                self.request_duration.record(
                    (perf_counter() - started) * 1000,
                    attributes=attributes,
                )
            unbind_contextvars("trace_id")
            reset_trace_id(token)
