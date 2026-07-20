import asyncio
import re

import httpx
from fastapi import FastAPI
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openrag.api.middleware.correlation import TraceCorrelationMiddleware
from openrag.core.telemetry import TelemetryRuntime, current_trace_id


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TraceCorrelationMiddleware)

    @app.get("/trace")
    async def trace() -> dict[str, str]:
        await asyncio.sleep(0)
        return {"trace_id": current_trace_id()}

    return app


async def test_invalid_inbound_trace_id_is_replaced() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()),
        base_url="http://test",
    ) as client:
        response = await client.get("/trace", headers={"X-Trace-ID": "../../secret"})

    trace_id = response.headers["X-Trace-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", trace_id)
    assert response.json() == {"trace_id": trace_id}


async def test_valid_trace_id_is_preserved_and_duplicate_response_header_removed() -> None:
    trace_id = "b" * 32
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()),
        base_url="http://test",
    ) as client:
        response = await client.get("/trace", headers={"X-Trace-ID": trace_id})

    assert response.headers.get_list("X-Trace-ID") == [trace_id]
    assert response.json()["trace_id"] == trace_id


async def test_concurrent_requests_do_not_share_trace_context() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()),
        base_url="http://test",
    ) as client:
        responses = await asyncio.gather(*(client.get("/trace") for _item in range(20)))

    trace_ids = {response.headers["X-Trace-ID"] for response in responses}
    assert len(trace_ids) == 20
    assert all(response.json()["trace_id"] in trace_ids for response in responses)


async def test_exported_server_span_uses_route_template_not_raw_identifier() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = TelemetryRuntime(
        endpoint="memory://",
        resource=provider.resource,
        tracer_provider=provider,
    )
    app = FastAPI()
    app.add_middleware(TraceCorrelationMiddleware, telemetry=runtime)

    @app.get("/items/{item_id}")
    async def item(item_id: str) -> dict[str, bool]:
        return {"found": bool(item_id)}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/items/customer-secret-123")

    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "HTTP GET /items/{item_id}"
    assert spans[0].attributes["http.route"] == "/items/{item_id}"
    assert spans[0].attributes["http.response.status_code"] == 200
    assert "customer-secret-123" not in str(spans[0].attributes)
    runtime.shutdown()
