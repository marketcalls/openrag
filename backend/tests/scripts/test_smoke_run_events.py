import json
from uuid import UUID

import httpx
import pytest

from scripts import smoke_run_events as smoke

PRIMARY_TOKEN = "primary-token-sentinel"  # noqa: S105 - inert test sentinel
SECOND_TOKEN = "second-token-sentinel"  # noqa: S105 - inert test sentinel
PASSWORD = "password-sentinel"  # noqa: S105 - inert test sentinel
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
CHAT_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")


def config() -> smoke.SmokeConfig:
    return smoke.SmokeConfig(
        api_url="http://127.0.0.1:8000",
        email="admin@example.com",
        password=PASSWORD,
        second_email="peer@example.com",
        second_password=PASSWORD,
        deadline_seconds=10,
    )


def frame(event_id: UUID, sequence: int, event_type: str) -> str:
    return (
        f"id: {event_id}\n"
        f"event: {event_type}\n"
        "data: "
        + json.dumps(
            {
                "schema_version": 1,
                "event_id": str(event_id),
                "sequence": sequence,
                "event_type": event_type,
                "run_id": str(RUN_ID),
                "org_id": "44444444-4444-4444-8444-444444444444",
                "workspace_id": str(WORKSPACE_ID),
                "chat_id": str(CHAT_ID),
                "occurred_at": "2026-07-20T00:00:00Z",
                "payload": {},
            },
            separators=(",", ":"),
        )
        + "\n\n"
    )


def api_transport() -> tuple[httpx.MockTransport, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []
    accept_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal accept_calls
        calls.append((request.method, request.url.path))
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/api/v1/auth/login":
            email = json.loads(request.content)["email"]
            return httpx.Response(
                200,
                json={
                    "access_token": (
                        PRIMARY_TOKEN if email == "admin@example.com" else SECOND_TOKEN
                    )
                },
            )
        if request.url.path == "/api/v1/workspaces":
            return httpx.Response(200, json=[{"id": str(WORKSPACE_ID)}])
        if request.url.path == "/api/v1/chats":
            return httpx.Response(201, json={"id": str(CHAT_ID)})
        if request.url.path == f"/api/v1/chats/{CHAT_ID}/runs":
            accept_calls += 1
            return httpx.Response(
                202,
                json={
                    "run_id": str(RUN_ID),
                    "created": accept_calls == 1,
                    "events_url": f"/api/v1/runs/{RUN_ID}/events",
                },
            )
        if request.url.path == f"/api/v1/runs/{RUN_ID}/cancel":
            return httpx.Response(202, json={"status": "accepted"})
        if request.url.path == f"/api/v1/runs/{RUN_ID}/events":
            body = "".join(
                (
                    frame(UUID("55555555-5555-4555-8555-555555555555"), 1, "run.accepted"),
                    frame(
                        UUID("66666666-6666-4666-8666-666666666666"),
                        2,
                        "run.cancel.requested",
                    ),
                    frame(UUID("77777777-7777-4777-8777-777777777777"), 3, "run.cancelled"),
                )
            )
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        if request.url.path == f"/api/v1/runs/{RUN_ID}":
            assert request.headers["authorization"] == f"Bearer {SECOND_TOKEN}"
            return httpx.Response(404, json={"detail": "not found"})
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    return httpx.MockTransport(handler), calls


async def test_smoke_exercises_idempotency_cancellation_replay_and_denial() -> None:
    transport, calls = api_transport()

    await smoke.run_smoke(config(), transport=transport)

    assert calls.count(("POST", f"/api/v1/chats/{CHAT_ID}/runs")) == 2
    assert ("POST", f"/api/v1/runs/{RUN_ID}/cancel") in calls
    assert ("GET", f"/api/v1/runs/{RUN_ID}/events") in calls
    assert ("GET", f"/api/v1/runs/{RUN_ID}") in calls


def test_config_requires_credentials_without_disclosing_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENRAG_SMOKE_EMAIL", raising=False)
    monkeypatch.setenv("OPENRAG_SMOKE_PASSWORD", PASSWORD)

    with pytest.raises(smoke.SmokeFailure, match="config:OPENRAG_SMOKE_EMAIL") as captured:
        smoke.config_from_environment()

    assert PASSWORD not in str(captured.value)


def test_remote_smoke_url_requires_https_and_credentials_are_repr_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENRAG_SMOKE_EMAIL", "admin@example.com")
    monkeypatch.setenv("OPENRAG_SMOKE_PASSWORD", PASSWORD)
    monkeypatch.setenv("OPENRAG_SMOKE_API_URL", "http://api.example.com")

    with pytest.raises(smoke.SmokeFailure, match="config:api-url"):
        smoke.config_from_environment()

    assert PASSWORD not in repr(config())
