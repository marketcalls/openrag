import json
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from openrag.api.middleware.request_body_limit import UploadBodyLimitMiddleware


async def _run(
    app: ASGIApp,
    *,
    path: str,
    chunks: list[bytes],
    content_length: str | None = None,
) -> tuple[list[Message], int]:
    headers: list[tuple[bytes, bytes]] = [(b"content-type", b"multipart/form-data")]
    if content_length is not None:
        headers.append((b"content-length", content_length.encode("ascii")))
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
        "state": {},
    }
    pending = list(chunks)
    responses: list[Message] = []
    receive_calls = 0

    async def receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        chunk = pending.pop(0)
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": bool(pending),
        }

    async def send(message: Message) -> None:
        responses.append(message)

    await app(scope, receive, send)
    return responses, receive_calls


def _downstream() -> tuple[ASGIApp, list[bytes]]:
    bodies: list[bytes] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        bodies.append(bytes(body))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    return app, bodies


def _response(messages: list[Message]) -> tuple[int, dict[str, Any]]:
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return int(start["status"]), json.loads(body)


async def test_declared_oversized_upload_is_rejected_without_reading_body() -> None:
    downstream, bodies = _downstream()
    middleware = UploadBodyLimitMiddleware(downstream, maximum_bytes=8)

    messages, receive_calls = await _run(
        middleware,
        path="/api/v1/workspaces/workspace-id/documents",
        chunks=[b"not read"],
        content_length="9",
    )

    status, problem = _response(messages)
    assert status == 413
    assert problem == {
        "type": "about:blank",
        "title": "Payload too large",
        "status": 413,
        "detail": "request body exceeds the upload limit",
        "trace_id": problem["trace_id"],
    }
    assert len(problem["trace_id"]) == 32
    assert receive_calls == 0
    assert bodies == []


async def test_chunked_upload_is_rejected_as_soon_as_limit_is_crossed() -> None:
    downstream, bodies = _downstream()
    middleware = UploadBodyLimitMiddleware(downstream, maximum_bytes=8)

    messages, receive_calls = await _run(
        middleware,
        path="/api/v1/workspaces/workspace-id/documents",
        chunks=[b"1234", b"5678", b"9", b"never-read"],
    )

    status, _ = _response(messages)
    assert status == 413
    assert receive_calls == 3
    assert bodies == []


async def test_non_upload_route_is_not_limited() -> None:
    downstream, bodies = _downstream()
    middleware = UploadBodyLimitMiddleware(downstream, maximum_bytes=8)

    messages, receive_calls = await _run(
        middleware,
        path="/api/v1/chats/chat-id/messages/stream",
        chunks=[b"more than eight bytes"],
        content_length="21",
    )

    assert (
        next(message for message in messages if message["type"] == "http.response.start")["status"]
        == 204
    )
    assert receive_calls == 1
    assert bodies == [b"more than eight bytes"]
