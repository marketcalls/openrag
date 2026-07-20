"""Streaming request-body limit for document uploads."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from openrag.core.errors import PayloadTooLarge
from openrag.core.telemetry import current_trace_id

_DETAIL = "request body exceeds the upload limit"


def _is_document_upload(scope: Scope) -> bool:
    if scope.get("method") != "POST":
        return False
    parts = str(scope.get("path", "")).strip("/").split("/")
    return (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "workspaces"]
        and bool(parts[3])
        and parts[4] == "documents"
    )


def _content_length(scope: Scope) -> int | None:
    values = [
        value for name, value in scope.get("headers", []) if name.lower() == b"content-length"
    ]
    if len(values) != 1:
        return None
    try:
        parsed = int(values[0])
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(
        status_code=413,
        content={
            "type": "about:blank",
            "title": "Payload too large",
            "status": 413,
            "detail": _DETAIL,
            "trace_id": current_trace_id(),
        },
        media_type="application/problem+json",
    )
    await response(scope, receive, send)


class UploadBodyLimitMiddleware:
    """Bound raw upload bodies before multipart parsing or file spooling."""

    def __init__(self, app: ASGIApp, maximum_bytes: int) -> None:
        self.app = app
        self.maximum_bytes = maximum_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_document_upload(scope):
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > self.maximum_bytes:
            await _reject(scope, receive, send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.maximum_bytes:
                    raise PayloadTooLarge(_DETAIL)
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except PayloadTooLarge:
            if response_started:
                raise
            await _reject(scope, receive, send)
