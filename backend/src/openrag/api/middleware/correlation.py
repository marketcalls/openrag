"""Pure ASGI request correlation that preserves streaming response behavior."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, unbind_contextvars

from openrag.core.telemetry import new_trace_id, reset_trace_id, set_trace_id

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
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        trace_id = new_trace_id(_inbound_trace_id(scope))
        token = set_trace_id(trace_id)
        bind_contextvars(trace_id=trace_id)

        async def correlated_send(message: Message) -> None:
            if message["type"] == "http.response.start":
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
        finally:
            unbind_contextvars("trace_id")
            reset_trace_id(token)
