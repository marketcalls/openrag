#!/usr/bin/env python3
"""Credential-safe live smoke for durable OpenRAG run events."""

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

import httpx


class SmokeFailure(RuntimeError):
    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(stage)


@dataclass(frozen=True, slots=True)
class SmokeConfig:
    api_url: str
    email: str
    password: str = field(repr=False)
    second_email: str | None
    second_password: str | None = field(repr=False)
    deadline_seconds: float


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SmokeFailure(f"config:{name}")
    return value


def _api_url(value: str) -> str:
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL as exc:
        raise SmokeFailure("config:api-url") from exc
    loopback = url.host in {"127.0.0.1", "localhost", "::1"}
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.userinfo
        or url.query
        or url.fragment
        or (url.scheme == "http" and not loopback)
    ):
        raise SmokeFailure("config:api-url")
    return str(url).rstrip("/")


def config_from_environment() -> SmokeConfig:
    try:
        deadline = float(os.getenv("OPENRAG_SMOKE_DEADLINE_SECONDS", "90"))
    except ValueError as exc:
        raise SmokeFailure("config:deadline") from exc
    if not 10 <= deadline <= 600:
        raise SmokeFailure("config:deadline")
    second_email = os.getenv("OPENRAG_SMOKE_SECOND_EMAIL", "").strip() or None
    second_password = os.getenv("OPENRAG_SMOKE_SECOND_PASSWORD", "").strip() or None
    if (second_email is None) != (second_password is None):
        raise SmokeFailure("config:second-user")
    return SmokeConfig(
        api_url=_api_url(
            os.getenv("OPENRAG_SMOKE_API_URL", "http://127.0.0.1:8000")
        ),
        email=_required("OPENRAG_SMOKE_EMAIL"),
        password=_required("OPENRAG_SMOKE_PASSWORD"),
        second_email=second_email,
        second_password=second_password,
        deadline_seconds=deadline,
    )


def _mapping(value: object, stage: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SmokeFailure(stage)
    return value


async def _json(response: httpx.Response, stage: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise SmokeFailure(stage)
    try:
        return _mapping(response.json(), stage)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SmokeFailure(stage) from exc


async def _wait_ready(client: httpx.AsyncClient, deadline: float) -> None:
    while monotonic() < deadline:
        try:
            response = await client.get("/readyz")
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1)
    raise SmokeFailure("ready")


async def _login(
    client: httpx.AsyncClient,
    email: str,
    password: str,
) -> str:
    payload = await _json(
        await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        ),
        "login",
    )
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise SmokeFailure("login-contract")
    return token


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _workspace_id(
    client: httpx.AsyncClient,
    headers: dict[str, str],
) -> str:
    response = await client.get("/api/v1/workspaces", headers=headers)
    if response.status_code >= 400:
        raise SmokeFailure("workspace-list")
    try:
        rows = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SmokeFailure("workspace-list-contract") from exc
    if not isinstance(rows, list):
        raise SmokeFailure("workspace-list-contract")
    if rows:
        workspace_id = _mapping(rows[0], "workspace-list-contract").get("id")
    else:
        created = await _json(
            await client.post(
                "/api/v1/workspaces",
                headers=headers,
                json={"name": f"Run smoke {uuid4().hex[:8]}"},
            ),
            "workspace-create",
        )
        workspace_id = created.get("id")
    try:
        return str(UUID(str(workspace_id)))
    except ValueError as exc:
        raise SmokeFailure("workspace-contract") from exc


async def _chat_id(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    workspace_id: str,
) -> str:
    created = await _json(
        await client.post(
            "/api/v1/chats",
            headers=headers,
            json={"workspace_id": workspace_id, "title": "Run event smoke"},
        ),
        "chat-create",
    )
    try:
        return str(UUID(str(created.get("id"))))
    except ValueError as exc:
        raise SmokeFailure("chat-contract") from exc


async def _accept_twice(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    chat_id: str,
) -> tuple[str, str]:
    request_id = str(uuid4())
    body = {
        "content": "Cancel this durable smoke run.",
        "client_request_id": request_id,
        "reasoning_effort": "off",
    }
    url = f"/api/v1/chats/{chat_id}/runs"
    first = await _json(
        await client.post(url, headers=headers, json=body),
        "run-accept",
    )
    second = await _json(
        await client.post(url, headers=headers, json=body),
        "run-idempotency",
    )
    run_id = first.get("run_id")
    if run_id != second.get("run_id") or second.get("created") is not False:
        raise SmokeFailure("run-idempotency-contract")
    try:
        run_id = str(UUID(str(run_id)))
    except ValueError as exc:
        raise SmokeFailure("run-contract") from exc
    events_url = first.get("events_url")
    if not isinstance(events_url, str) or not events_url.startswith("/api/v1/runs/"):
        raise SmokeFailure("run-contract")
    return run_id, events_url


async def _cancel(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    run_id: str,
) -> None:
    response = await client.post(f"/api/v1/runs/{run_id}/cancel", headers=headers)
    if response.status_code != 202:
        raise SmokeFailure("cancel")


async def _wait_cancelled(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    events_url: str,
    run_id: str,
    deadline: float,
) -> None:
    seen_ids: set[UUID] = set()
    last_sequence = 0
    async with client.stream("GET", events_url, headers=headers) as response:
        if response.status_code != 200:
            raise SmokeFailure("events-open")
        event_name: str | None = None
        data: str | None = None
        async for line in response.aiter_lines():
            if monotonic() >= deadline:
                raise SmokeFailure("events-timeout")
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data = line.removeprefix("data:").strip()
            elif line == "" and event_name is not None and data is not None:
                try:
                    envelope = _mapping(json.loads(data), "events-contract")
                    event_id = UUID(str(envelope.get("event_id")))
                    sequence = int(envelope.get("sequence", 0))
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise SmokeFailure("events-contract") from exc
                if (
                    envelope.get("event_type") != event_name
                    or envelope.get("run_id") != run_id
                    or event_id in seen_ids
                    or sequence <= last_sequence
                ):
                    raise SmokeFailure("events-order")
                seen_ids.add(event_id)
                last_sequence = sequence
                if event_name == "run.cancelled":
                    return
                if event_name in {"run.completed", "run.failed"}:
                    raise SmokeFailure("cancel-lost-race")
                event_name = None
                data = None
    raise SmokeFailure("events-eof")


async def _assert_tenant_denial(
    client: httpx.AsyncClient,
    config: SmokeConfig,
    run_id: str,
) -> None:
    if config.second_email is None or config.second_password is None:
        return
    token = await _login(client, config.second_email, config.second_password)
    response = await client.get(
        f"/api/v1/runs/{run_id}",
        headers=_headers(token),
    )
    if response.status_code != 404:
        raise SmokeFailure("tenant-denial")


async def run_smoke(
    config: SmokeConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    deadline = monotonic() + config.deadline_seconds
    timeout = httpx.Timeout(15, read=config.deadline_seconds)
    async with httpx.AsyncClient(
        base_url=config.api_url,
        timeout=timeout,
        follow_redirects=False,
        transport=transport,
    ) as client:
        await _wait_ready(client, deadline)
        token = await _login(client, config.email, config.password)
        headers = _headers(token)
        workspace_id = await _workspace_id(client, headers)
        chat_id = await _chat_id(client, headers, workspace_id)
        run_id, events_url = await _accept_twice(client, headers, chat_id)
        await _cancel(client, headers, run_id)
        await _wait_cancelled(client, headers, events_url, run_id, deadline)
        await _assert_tenant_denial(client, config, run_id)


def main() -> int:
    try:
        asyncio.run(run_smoke(config_from_environment()))
    except SmokeFailure as exc:
        print(f"OpenRAG run-event smoke failed at {exc.stage}", file=sys.stderr)
        return 1
    except (httpx.HTTPError, TimeoutError):
        print("OpenRAG run-event smoke failed at transport", file=sys.stderr)
        return 1
    print("OpenRAG run-event smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
