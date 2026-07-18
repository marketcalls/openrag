# Durable Run and Event Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the durable, tenant-safe, replayable and cancellable run/event foundation that decouples browser streams from database and model execution resources.

**Architecture:** A run command atomically persists a user message, `AgentRun`, and transactional outbox record. A relay publishes commands to Redis Streams; async runners publish ordered per-run events; authenticated SSE subscribers replay those events without retaining a SQLAlchemy transaction. PostgreSQL remains authoritative and Redis supplies durable command transport plus bounded stream replay.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2/asyncpg, PostgreSQL 16, Redis Streams, structlog, pytest/pytest-asyncio, HTTPX, Docker Compose.

## Global Constraints

- Product name is `OpenRAG`; never introduce `RAGHub` or `raghub`.
- Use fully asynchronous I/O on API and runner hot paths.
- PostgreSQL is authoritative; Redis delivery is at-least-once and domain effects are idempotent.
- Do not hold a database connection or transaction during SSE waits or external model/tool work.
- Every accepted run reaches exactly one terminal state: `completed`, `failed`, or `cancelled`.
- All run/event access must enforce immutable organization, workspace, and user scope.
- Do not expose prompts, document text, secrets, tool payloads, provider errors, or chain-of-thought in public events or logs.
- Keep existing `/chats/{chat_id}/messages` behavior green while the new run API is introduced; cutover occurs in the Agno/LiteLLM plan.
- Use TDD for every behavior change and commit only after focused plus regression tests pass.
- Comprehensive verification includes unit, API/integration, isolation, retry/idempotency, failure-injection, 100-stream load, and Docker smoke coverage.
- Do not add or modify the local benchmark repositories `anything-llm/` and `openui/`.

---

## File structure

### New backend domain files

- `backend/src/openrag/modules/runs/models.py`: authoritative `AgentRun` state.
- `backend/src/openrag/modules/runs/schemas.py`: run command/status API schemas.
- `backend/src/openrag/modules/runs/events.py`: versioned event envelope and public event types.
- `backend/src/openrag/modules/runs/service.py`: tenant-safe command acceptance, lookup, and cancellation.
- `backend/src/openrag/modules/runs/lifecycle.py`: conditional run state transitions.
- `backend/src/openrag/modules/events/models.py`: transactional outbox and consumer inbox records.
- `backend/src/openrag/modules/events/bus.py`: event-bus protocol and Redis Streams adapter.
- `backend/src/openrag/modules/events/outbox.py`: outbox claim/publish/mark relay.
- `backend/src/openrag/modules/events/relay.py`: async relay process entrypoint.
- `backend/src/openrag/modules/runs/runner.py`: command consumer and injectable run executor boundary.
- `backend/src/openrag/api/routes/runs.py`: accept/status/cancel/replay endpoints.
- `backend/migrations/versions/4f2e1c9a7b30_agent_runs_and_outbox.py`: schema migration.

### Modified backend/runtime files

- `backend/migrations/env.py`: import new model metadata.
- `backend/src/openrag/api/app.py`: construct/inject shared event bus and include run routes.
- `backend/src/openrag/core/config.py`: Redis stream, retention, pool, and concurrency settings.
- `backend/src/openrag/core/db.py`: explicit configurable pool sizing.
- `backend/src/openrag/modules/chat/models.py`: run-safe message status linkage where required.
- `backend/src/openrag/modules/chat/schemas.py`: preserve old schema while referencing run commands through the new module.
- `backend/src/openrag/modules/chat/service.py`: expose transaction-safe message construction without forcing an internal commit.
- `backend/pyproject.toml`: add load-test marker configuration only; no new runtime broker dependency is needed.
- `deploy/compose.yaml`: Redis AOF volume, relay, and runner services.

### New and modified tests

- `backend/tests/modules/runs/test_models.py`
- `backend/tests/modules/runs/test_events.py`
- `backend/tests/modules/runs/test_service.py`
- `backend/tests/modules/runs/test_lifecycle.py`
- `backend/tests/modules/events/test_bus.py`
- `backend/tests/modules/events/test_outbox.py`
- `backend/tests/api/test_run_routes.py`
- `backend/tests/isolation/test_run_isolation.py`
- `backend/tests/integration/test_run_replay.py`
- `backend/tests/load/test_100_run_streams.py`
- `backend/tests/test_compose.py`
- `backend/tests/conftest.py`
- `scripts/smoke_run_events.py`

---

### Task 1: Persist authoritative runs, outbox records, and inbox deduplication

**Files:**
- Create: `backend/src/openrag/modules/runs/__init__.py`
- Create: `backend/src/openrag/modules/runs/models.py`
- Create: `backend/src/openrag/modules/events/__init__.py`
- Create: `backend/src/openrag/modules/events/models.py`
- Create: `backend/migrations/versions/4f2e1c9a7b30_agent_runs_and_outbox.py`
- Modify: `backend/migrations/env.py`
- Test: `backend/tests/modules/runs/test_models.py`

**Interfaces:**
- Produces: `AgentRun`, `OutboxEvent`, and `InboxEvent` SQLAlchemy models.
- Produces: one-run-per-`(user_id, client_request_id)` and one-inbox-effect-per-`(consumer, event_id)` database guarantees.
- Consumes: existing `Chat`, `Message`, `Model`, `Organization`, `Workspace`, and `User` tables.

- [ ] **Step 1: Write the failing model tests**

```python
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.events.models import InboxEvent, OutboxEvent
from openrag.modules.chat.models import Chat, Message
from openrag.modules.runs.models import AgentRun
from openrag.modules.auth.models import User


@pytest.fixture
async def run_env(
    session: AsyncSession,
    chat_env: dict[str, Any],
    seeded_user: User,
) -> dict[str, Any]:
    workspace = chat_env["workspace"]
    chat = Chat(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        user_id=seeded_user.id,
    )
    session.add(chat)
    await session.flush()
    user_message = Message(
        chat_id=chat.id,
        parent_message_id=None,
        sibling_index=0,
        role="user",
        content="hello",
    )
    session.add(user_message)
    await session.commit()
    return {"user": seeded_user, "workspace": workspace, "chat": chat, "user_message": user_message}


async def test_agent_run_defaults_to_accepted(
    session: AsyncSession,
    run_env: dict[str, Any],
) -> None:
    run = AgentRun(
        org_id=run_env["user"].org_id,
        workspace_id=run_env["workspace"].id,
        user_id=run_env["user"].id,
        chat_id=run_env["chat"].id,
        input_message_id=run_env["user_message"].id,
        client_request_id=uuid4(),
    )
    session.add(run)
    await session.commit()
    assert run.status == "accepted"
    assert run.cancel_requested_at is None
    assert run.finished_at is None


async def test_agent_run_idempotency_is_enforced(
    session: AsyncSession,
    run_env: dict[str, Any],
) -> None:
    request_id = uuid4()
    values = {
        "org_id": run_env["user"].org_id,
        "workspace_id": run_env["workspace"].id,
        "user_id": run_env["user"].id,
        "chat_id": run_env["chat"].id,
        "input_message_id": run_env["user_message"].id,
        "client_request_id": request_id,
    }
    session.add_all([AgentRun(**values), AgentRun(**values)])
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_outbox_and_inbox_dedupe_keys_are_unique(
    session: AsyncSession,
) -> None:
    event_id = uuid4()
    outbox = OutboxEvent(
        event_id=event_id,
        aggregate_type="agent_run",
        aggregate_id=uuid4(),
        event_type="run.requested.v1",
        payload={"run_id": str(uuid4())},
        dedupe_key=f"run.requested:{event_id}",
    )
    session.add(outbox)
    await session.commit()
    session.add_all(
        [
            InboxEvent(consumer="agent-runner", event_id=event_id),
            InboxEvent(consumer="agent-runner", event_id=event_id),
        ]
    )
    with pytest.raises(IntegrityError):
        await session.commit()
```

- [ ] **Step 2: Run the tests and confirm the missing modules fail**

Run:

```bash
cd backend && uv run pytest tests/modules/runs/test_models.py -q
```

Expected: collection fails with `ModuleNotFoundError` for `openrag.modules.runs`.

- [ ] **Step 3: Implement focused SQLAlchemy models**

Create `backend/src/openrag/modules/runs/models.py` with this public shape:

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class AgentRun(UUIDPk, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "client_request_id",
            name="uq_agent_runs_user_request",
        ),
        CheckConstraint(
            "status IN ('accepted','queued','running','completed','failed','cancelled')",
            name="ck_agent_runs_status",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    chat_id: Mapped[UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    input_message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id"), unique=True)
    assistant_message_id: Mapped[UUID | None] = mapped_column(ForeignKey("messages.id"), default=None)
    model_id: Mapped[UUID | None] = mapped_column(ForeignKey("models.id"), default=None)
    client_request_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(default="accepted", index=True)
    route: Mapped[str | None] = mapped_column(default=None)
    error_code: Mapped[str | None] = mapped_column(default=None)
    trace_id: Mapped[str | None] = mapped_column(default=None, index=True)
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    accepted_at: Mapped[datetime]
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    first_token_at: Mapped[datetime | None] = mapped_column(default=None)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
```

Set `accepted_at` with `default=naive_utc` in the actual file. Keep line lengths compatible with Ruff by splitting long `mapped_column` calls.

Create `backend/src/openrag/modules/events/models.py`:

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class OutboxEvent(UUIDPk, Base):
    __tablename__ = "outbox_events"

    event_id: Mapped[UUID] = mapped_column(unique=True)
    aggregate_type: Mapped[str] = mapped_column(index=True)
    aggregate_id: Mapped[UUID] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    dedupe_key: Mapped[str] = mapped_column(unique=True)
    attempts: Mapped[int] = mapped_column(default=0)
    published_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    last_error: Mapped[str | None] = mapped_column(default=None)


class InboxEvent(UUIDPk, Base):
    __tablename__ = "inbox_events"
    __table_args__ = (
        UniqueConstraint("consumer", "event_id", name="uq_inbox_consumer_event"),
    )

    consumer: Mapped[str] = mapped_column(index=True)
    event_id: Mapped[UUID]
```

Create empty package initializers and import both model modules from
`backend/migrations/env.py` so Alembic and test metadata discover them.

- [ ] **Step 4: Create the exact migration**

Create revision `4f2e1c9a7b30` with `down_revision = "ac802c65b29b"`. Its
`upgrade()` must create `agent_runs`, `outbox_events`, and `inbox_events`, all
foreign keys, named constraints, and indexes represented by the models. Its
`downgrade()` must drop indexes and tables in reverse dependency order.

- [ ] **Step 5: Run focused models and migration verification**

Run:

```bash
cd backend
uv run pytest tests/modules/runs/test_models.py -q
uv run alembic upgrade head
uv run alembic check
```

Expected: tests pass, upgrade succeeds, and `alembic check` reports no new
upgrade operations.

- [ ] **Step 6: Commit the persisted run foundation**

```bash
git add backend/src/openrag/modules/runs backend/src/openrag/modules/events \
  backend/migrations/env.py \
  backend/migrations/versions/4f2e1c9a7b30_agent_runs_and_outbox.py \
  backend/tests/modules/runs/test_models.py
git commit -m "feat: persist agent runs and domain events"
```

---

### Task 2: Define the versioned, sequence-numbered event contract

**Files:**
- Create: `backend/src/openrag/modules/runs/events.py`
- Test: `backend/tests/modules/runs/test_events.py`

**Interfaces:**
- Produces: `RunEventType`, `RunEventEnvelope`, `new_run_event`, and `encode_sse`.
- Consumes: UUID run/tenant/chat identifiers and an integer sequence allocated by the event bus.
- Guarantees: stable schema version, safe payload size, SSE `id`, and no arbitrary public event names.

- [ ] **Step 1: Write contract tests**

```python
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from openrag.modules.runs.events import RunEventEnvelope, encode_sse, new_run_event


def test_run_event_encodes_replayable_sse() -> None:
    event = new_run_event(
        sequence=7,
        event_type="message.delta",
        run_id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        chat_id=uuid4(),
        payload={"delta": "hello"},
    )
    encoded = encode_sse(event)
    lines = encoded.strip().splitlines()
    assert lines[0] == f"id: {event.event_id}"
    assert lines[1] == "event: message.delta"
    assert json.loads(lines[2].removeprefix("data: "))["sequence"] == 7


def test_unknown_event_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RunEventEnvelope(
            event_id=uuid4(),
            sequence=1,
            event_type="reasoning.secret",
            run_id=uuid4(),
            org_id=uuid4(),
            workspace_id=uuid4(),
            chat_id=uuid4(),
            occurred_at="2026-07-18T00:00:00Z",
            payload={},
        )


def test_payload_limit_is_enforced() -> None:
    with pytest.raises(ValueError, match="event payload exceeds"):
        new_run_event(
            sequence=1,
            event_type="message.delta",
            run_id=uuid4(),
            org_id=uuid4(),
            workspace_id=uuid4(),
            chat_id=uuid4(),
            payload={"delta": "x" * 70_000},
        )
```

- [ ] **Step 2: Confirm the event contract is absent**

Run:

```bash
cd backend && uv run pytest tests/modules/runs/test_events.py -q
```

Expected: import failure for `openrag.modules.runs.events`.

- [ ] **Step 3: Implement the closed Pydantic contract**

Use this closed event vocabulary:

```python
RunEventType = Literal[
    "run.accepted",
    "run.started",
    "run.completed",
    "run.failed",
    "run.cancel.requested",
    "run.cancelled",
    "route.selected",
    "retrieval.started",
    "retrieval.sources",
    "retrieval.completed",
    "agent.started",
    "agent.progress",
    "agent.completed",
    "tool.started",
    "tool.progress",
    "tool.completed",
    "tool.failed",
    "message.started",
    "message.delta",
    "message.completed",
    "ui.block.upsert",
    "ui.committed",
    "artifact.created",
    "artifact.versioned",
    "usage.updated",
    "approval.requested",
    "clarification.requested",
    "heartbeat",
]
```

`RunEventEnvelope` must set `schema_version: Literal[1] = 1`, require positive
`sequence`, use timezone-aware `datetime`, and contain the immutable run and
tenant identifiers. `new_run_event()` serializes the payload with compact JSON
and raises `ValueError("event payload exceeds 65536 bytes")` above 64 KiB.
`encode_sse()` emits `id`, `event`, and one compact JSON `data` line followed by
two newlines.

- [ ] **Step 4: Verify contract, lint, and type safety**

```bash
cd backend
uv run pytest tests/modules/runs/test_events.py -q
uv run ruff check src/openrag/modules/runs/events.py tests/modules/runs/test_events.py
uv run mypy src/openrag/modules/runs/events.py
```

Expected: all commands pass.

- [ ] **Step 5: Commit the event contract**

```bash
git add backend/src/openrag/modules/runs/events.py \
  backend/tests/modules/runs/test_events.py
git commit -m "feat: define replayable run event contract"
```

---

### Task 3: Accept an idempotent run command in one transaction

**Files:**
- Create: `backend/src/openrag/modules/runs/schemas.py`
- Create: `backend/src/openrag/modules/runs/service.py`
- Create: `backend/src/openrag/api/routes/runs.py`
- Modify: `backend/src/openrag/api/app.py`
- Modify: `backend/src/openrag/modules/chat/service.py`
- Test: `backend/tests/modules/runs/test_service.py`
- Test: `backend/tests/api/test_run_routes.py`
- Test: `backend/tests/isolation/test_run_isolation.py`

**Interfaces:**
- Produces: `RunCreate`, `RunAccepted`, `RunStatusOut`, `accept_run`, `get_run`, and `request_cancel`.
- Consumes: existing chat parent-resolution rules and `TenantContext`.
- Produces: `POST /api/v1/chats/{chat_id}/runs`, `GET /api/v1/runs/{run_id}`, and `POST /api/v1/runs/{run_id}/cancel`.

- [ ] **Step 1: Write service tests for atomic creation and idempotency**

```python
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.chat.models import Message
from openrag.modules.events.models import OutboxEvent
from openrag.modules.runs.schemas import RunCreate
from openrag.modules.runs.service import accept_run


async def test_accept_run_persists_message_run_and_outbox_atomically(
    session: AsyncSession,
    tenant_context,
    seeded_chat,
) -> None:
    request_id = uuid4()
    accepted = await accept_run(
        session,
        tenant_context,
        seeded_chat.id,
        RunCreate(content="hello", client_request_id=request_id),
    )
    assert accepted.created is True
    assert accepted.run.client_request_id == request_id
    assert accepted.run.status == "accepted"
    assert await session.scalar(select(func.count()).select_from(Message)) == 1
    assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1


async def test_accept_run_replay_returns_existing_run_without_duplicate_message(
    session: AsyncSession,
    tenant_context,
    seeded_chat,
) -> None:
    command = RunCreate(content="hello", client_request_id=uuid4())
    first = await accept_run(session, tenant_context, seeded_chat.id, command)
    second = await accept_run(session, tenant_context, seeded_chat.id, command)
    assert first.run.id == second.run.id
    assert first.created is True
    assert second.created is False
    assert await session.scalar(select(func.count()).select_from(Message)) == 1
    assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
```

- [ ] **Step 2: Write API and tenant-isolation tests**

The API test must POST this body twice and assert `202`, the same `run_id`, and
only the first response has `created: true`:

```json
{
  "content": "hello",
  "client_request_id": "00000000-0000-4000-8000-000000000001"
}
```

The isolation test must authenticate a user from another organization and assert
`404` for POST, GET, and cancel against the original chat/run. Query PostgreSQL
afterward and assert the foreign user created no run, message, or outbox record.

- [ ] **Step 3: Confirm the new endpoint and service fail**

```bash
cd backend
uv run pytest tests/modules/runs/test_service.py tests/api/test_run_routes.py \
  tests/isolation/test_run_isolation.py -q
```

Expected: missing schema/service/route failures or `404` for the absent route.

- [ ] **Step 4: Implement schemas and atomic acceptance**

`RunCreate` contains:

```python
class RunCreate(BaseModel):
    content: str = Field(min_length=1, max_length=32_000)
    client_request_id: UUID
    parent_message_id: UUID | None = None
    model_id: UUID | None = None
```

`RunAccepted` contains `run_id`, `input_message_id`, `status`, `created`, and
`events_url`. `RunStatusOut` contains safe run state/timestamps, route, token
counts, and safe error code.

`accept_run()` must:

1. Look up an existing run by `user_id` and `client_request_id`; if present,
   verify that chat ID matches and return it without mutation.
2. Lock the authorized chat row using SQLAlchemy's `with_for_update()`.
3. Resolve the parent from current messages using the existing pure helper.
4. Construct the user `Message` without calling a helper that commits.
5. Flush to allocate the message ID.
6. Construct `AgentRun(status="accepted")` and `OutboxEvent` with event type
   `run.requested.v1`, event/dedupe ID, and identifier-only payload.
7. Update chat activity and commit once.
8. On a unique-request race, roll back, re-read the existing scoped run, and
   return `created=False`.

Expose a transaction-safe `build_message()` in the chat service for validation
and construction; retain `add_message()` as a compatibility wrapper that calls
`build_message()`, commits, and returns the message. Existing chat tests must stay
green.

- [ ] **Step 5: Implement routes with existing authentication and rate limits**

Register `runs.router` under `/api/v1`. Accept uses the existing per-user chat
send rate limiter. Status/cancel use tenant context and return `404` across tenant
boundaries. Cancel is idempotent: terminal runs remain terminal; a non-terminal
run records `cancel_requested_at` and a `run.cancel.requested.v1` outbox event in
one transaction.

- [ ] **Step 6: Run focused and regression tests**

```bash
cd backend
uv run pytest tests/modules/runs tests/api/test_run_routes.py \
  tests/isolation/test_run_isolation.py tests/api/test_chat_stream.py \
  tests/modules/chat/test_tree_service.py -q
uv run ruff check src tests
uv run mypy src/openrag
```

Expected: all tests and static checks pass.

- [ ] **Step 7: Commit the run command boundary**

```bash
git add backend/src/openrag/modules/runs \
  backend/src/openrag/api/routes/runs.py backend/src/openrag/api/app.py \
  backend/src/openrag/modules/chat/service.py \
  backend/tests/modules/runs/test_service.py backend/tests/api/test_run_routes.py \
  backend/tests/isolation/test_run_isolation.py
git commit -m "feat: accept idempotent chat runs"
```

---

### Task 4: Publish and replay ordered Redis Stream events

**Files:**
- Create: `backend/src/openrag/modules/events/bus.py`
- Test: `backend/tests/modules/events/test_bus.py`
- Modify: `backend/src/openrag/core/config.py`
- Modify: `backend/tests/core/test_config.py`

**Interfaces:**
- Produces: `EventBus` protocol and `RedisEventBus`.
- Produces: `append(envelope_without_sequence) -> RunEventEnvelope`, `read(run_id, after_event_id, block_ms)`, `trim(run_id)`, and `close()`.
- Consumes: shared `redis.asyncio.Redis` and run-event schemas.

- [ ] **Step 1: Write Redis integration tests**

```python
from uuid import uuid4

from redis.asyncio import Redis

from openrag.modules.events.bus import RedisEventBus


async def test_append_allocates_monotonic_sequences_and_replays(
    redis_client: Redis,
) -> None:
    bus = RedisEventBus(redis_client, max_events=100)
    ids = {
        "run_id": uuid4(),
        "org_id": uuid4(),
        "workspace_id": uuid4(),
        "chat_id": uuid4(),
    }
    first = await bus.append(event_type="run.started", payload={}, **ids)
    second = await bus.append(
        event_type="message.delta",
        payload={"delta": "hello"},
        **ids,
    )
    assert [first.sequence, second.sequence] == [1, 2]
    replay = await bus.read(ids["run_id"], after_event_id=str(first.event_id))
    assert [event.event_id for event in replay] == [second.event_id]


async def test_duplicate_event_id_is_not_appended_twice(
    redis_client: Redis,
) -> None:
    bus = RedisEventBus(redis_client, max_events=100)
    event_id = uuid4()
    ids = {
        "run_id": uuid4(),
        "org_id": uuid4(),
        "workspace_id": uuid4(),
        "chat_id": uuid4(),
    }
    first = await bus.append(event_id=event_id, event_type="run.started", payload={}, **ids)
    second = await bus.append(event_id=event_id, event_type="run.started", payload={}, **ids)
    assert first == second
    assert len(await bus.read(ids["run_id"])) == 1
```

- [ ] **Step 2: Verify the bus tests fail before implementation**

```bash
cd backend && uv run pytest tests/modules/events/test_bus.py -q
```

Expected: import failure for `openrag.modules.events.bus`.

- [ ] **Step 3: Implement atomic append with a Redis Lua script**

The Lua script must:

1. Check `openrag:run:event:{event_id}` and return the cached serialized event
   when it exists.
2. Increment `openrag:run:{run_id}:seq`.
3. Insert the compact serialized envelope with `XADD MAXLEN ~ max_events` into
   `openrag:run:{run_id}:events`.
4. Store the event-ID cache with the configured retention TTL.
5. Return the serialized envelope.

`read()` resolves an optional public event UUID to its sequence using the event
cache, performs `XRANGE`, parses every record through `RunEventEnvelope`, sorts by
sequence, and returns only events with greater sequence. A blocking read method
may use `XREAD` but must return control at the configured heartbeat interval.

Add settings with these exact defaults:

```python
run_event_max_events: int = 4096
run_event_retention_seconds: int = 3600
run_event_block_ms: int = 15_000
```

- [ ] **Step 4: Verify Redis behavior, type safety, and configuration**

```bash
cd backend
uv run pytest tests/modules/events/test_bus.py tests/core/test_config.py -q
uv run ruff check src/openrag/modules/events src/openrag/core/config.py \
  tests/modules/events tests/core/test_config.py
uv run mypy src/openrag
```

Expected: all commands pass.

- [ ] **Step 5: Commit the Redis event bus**

```bash
git add backend/src/openrag/modules/events/bus.py \
  backend/src/openrag/core/config.py backend/tests/modules/events/test_bus.py \
  backend/tests/core/test_config.py
git commit -m "feat: add ordered Redis run events"
```

---

### Task 5: Relay the transactional outbox and deduplicate consumers

**Files:**
- Create: `backend/src/openrag/modules/events/outbox.py`
- Create: `backend/src/openrag/modules/events/relay.py`
- Test: `backend/tests/modules/events/test_outbox.py`

**Interfaces:**
- Produces: `claim_outbox_batch`, `mark_published`, `mark_publish_failed`, `OutboxRelay`, and `consume_once` inbox helper.
- Consumes: async session factory, `RedisEventBus`, and `OutboxEvent` rows.
- Guarantees: skip-locked parallel relay safety and idempotent consumer effects.

- [ ] **Step 1: Write relay and inbox tests**

```python
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.events.models import InboxEvent, OutboxEvent
from openrag.modules.events.outbox import consume_once


async def test_consume_once_applies_effect_only_once(session: AsyncSession) -> None:
    calls = 0

    async def effect() -> None:
        nonlocal calls
        calls += 1

    event_id = uuid4()
    assert await consume_once(session, "runner-a", event_id, effect) is True
    assert await consume_once(session, "runner-a", event_id, effect) is False
    assert calls == 1
    assert await session.scalar(select(func.count()).select_from(InboxEvent)) == 1


class FailingPublisher:
    async def publish(self, event: ClaimedOutboxEvent) -> None:
        raise RuntimeError("broker unavailable")


async def test_relay_failure_records_bounded_error(
    engine: AsyncEngine,
    session: AsyncSession,
    seeded_outbox: OutboxEvent,
) -> None:
    factory = build_session_factory(engine)
    relay = OutboxRelay(
        session_factory=factory,
        publisher=FailingPublisher(),
        batch_size=10,
    )
    await relay.publish_batch()
    await session.rollback()
    await session.refresh(seeded_outbox)
    assert seeded_outbox.attempts == 1
    assert seeded_outbox.published_at is None
    assert len(seeded_outbox.last_error or "") <= 500
```

The test module imports `AsyncEngine`, `build_session_factory`,
`ClaimedOutboxEvent`, and `OutboxRelay` alongside the imports in the first test.

- [ ] **Step 2: Confirm tests fail for the absent relay**

```bash
cd backend && uv run pytest tests/modules/events/test_outbox.py -q
```

Expected: import failure for `openrag.modules.events.outbox`.

- [ ] **Step 3: Implement claim/publish/mark with short transactions**

`claim_outbox_batch()` selects unpublished rows ordered by creation time using
`FOR UPDATE SKIP LOCKED`, increments attempts, and returns immutable event DTOs.
Do not hold the transaction while publishing. Each publish gets its own follow-up
transaction that conditionally sets `published_at`; errors are truncated to 500
characters and never include payload or secrets.

`consume_once()` inserts `InboxEvent`, flushes to acquire the unique key, invokes
the effect in the same transaction, and commits. On unique conflict, roll back
and return `False` without invoking the effect.

`relay.py` provides `python -m openrag.modules.events.relay`, handles SIGTERM,
uses a 250 ms idle wait, and closes its Redis/engine resources during shutdown.

- [ ] **Step 4: Run relay failure, parallel claim, and regression tests**

```bash
cd backend
uv run pytest tests/modules/events/test_outbox.py tests/modules/runs/test_service.py -q
uv run ruff check src tests
uv run mypy src/openrag
```

Expected: all tests and static checks pass.

- [ ] **Step 5: Commit the outbox relay**

```bash
git add backend/src/openrag/modules/events/outbox.py \
  backend/src/openrag/modules/events/relay.py \
  backend/tests/modules/events/test_outbox.py
git commit -m "feat: relay transactional run commands"
```

---

### Task 6: Add conditional lifecycle transitions and end-to-end cancellation

**Files:**
- Create: `backend/src/openrag/modules/runs/lifecycle.py`
- Create: `backend/src/openrag/modules/runs/runner.py`
- Test: `backend/tests/modules/runs/test_lifecycle.py`
- Test: `backend/tests/integration/test_run_replay.py`

**Interfaces:**
- Produces: `RunLifecycle.start`, `first_token`, `complete`, `fail`, `request_cancel`, and `acknowledge_cancel`.
- Produces: `RunExecutor` protocol and `AgentRunner.handle_command`.
- Guarantees: conditional transitions, one terminal event, cancellation polling/propagation, and consumer deduplication.

- [ ] **Step 1: Write lifecycle state-machine tests**

```python
import asyncio

from openrag.modules.runs.lifecycle import RunLifecycle


async def test_only_one_terminal_transition_wins(seed_run, lifecycle: RunLifecycle) -> None:
    await lifecycle.start(seed_run.id)
    results = await asyncio.gather(
        lifecycle.complete(seed_run.id, assistant_message_id=None, usage=(4, 2)),
        lifecycle.fail(seed_run.id, error_code="provider_transient"),
    )
    assert sorted(results) == [False, True]
    run = await lifecycle.get(seed_run.id)
    assert run.status in {"completed", "failed"}
    assert run.finished_at is not None


async def test_cancel_is_idempotent_and_terminal(seed_run, lifecycle: RunLifecycle) -> None:
    assert await lifecycle.request_cancel(seed_run.id) is True
    assert await lifecycle.request_cancel(seed_run.id) is False
    assert await lifecycle.acknowledge_cancel(seed_run.id) is True
    assert await lifecycle.acknowledge_cancel(seed_run.id) is False
    run = await lifecycle.get(seed_run.id)
    assert run.status == "cancelled"
```

- [ ] **Step 2: Verify lifecycle tests fail**

```bash
cd backend && uv run pytest tests/modules/runs/test_lifecycle.py -q
```

Expected: import failure for `openrag.modules.runs.lifecycle`.

- [ ] **Step 3: Implement conditional SQL transitions**

Each lifecycle method uses one SQLAlchemy
`update(AgentRun).where(allowed_source_state_predicate)` statement
with an allowed-source-state predicate and `returning(AgentRun.id)` in a short transaction. `complete`,
`fail`, and `acknowledge_cancel` publish exactly one corresponding terminal event
after the database transition succeeds. Losing transitions return `False` and
emit nothing.

`RunExecutor` has this boundary:

```python
class RunExecutor(Protocol):
    async def execute(
        self,
        run: AgentRun,
        emit: Callable[[RunEventType, dict[str, object]], Awaitable[None]],
        cancelled: Callable[[], Awaitable[bool]],
    ) -> RunExecutionResult:
        raise NotImplementedError
```

The initial `AgentRunner` consumes `run.requested.v1`, records its inbox key,
starts the run, delegates to the injected executor, and applies one terminal
transition. It catches `asyncio.CancelledError` separately, maps known safe
exceptions to stable codes, and maps unexpected exceptions to `internal` while
logging only identifiers plus `exc_info`.

- [ ] **Step 4: Add failure-injection integration coverage**

Test these cases with deterministic executors:

- duplicate command delivery invokes the executor once;
- cancellation before start emits `run.cancelled` without invoking the executor;
- cancellation during an executor wait causes cooperative exit and one terminal
  cancellation event;
- executor failure produces one `run.failed` event with a safe code;
- process cancellation after completion cannot overwrite `completed`;
- broker command redelivery cannot create a second assistant completion.

- [ ] **Step 5: Run lifecycle/integration/static checks**

```bash
cd backend
uv run pytest tests/modules/runs/test_lifecycle.py \
  tests/integration/test_run_replay.py -q
uv run ruff check src tests
uv run mypy src/openrag
```

Expected: all tests and static checks pass.

- [ ] **Step 6: Commit lifecycle and runner boundaries**

```bash
git add backend/src/openrag/modules/runs/lifecycle.py \
  backend/src/openrag/modules/runs/runner.py \
  backend/tests/modules/runs/test_lifecycle.py \
  backend/tests/integration/test_run_replay.py
git commit -m "feat: add cancellable run lifecycle"
```

---

### Task 7: Expose authenticated replayable SSE without retaining DB resources

**Files:**
- Modify: `backend/src/openrag/api/routes/runs.py`
- Modify: `backend/src/openrag/api/app.py`
- Test: `backend/tests/api/test_run_routes.py`
- Test: `backend/tests/isolation/test_run_isolation.py`
- Test: `backend/tests/integration/test_run_replay.py`

**Interfaces:**
- Produces: `GET /api/v1/runs/{run_id}/events` with SSE IDs, heartbeat, resume, and terminal completion.
- Consumes: tenant-safe run authorization and `EventBus` replay/blocking reads.
- Guarantees: no active SQL transaction while waiting for Redis or the browser.

- [ ] **Step 1: Add API tests for replay, heartbeat, and disconnect**

Test the following exact behaviors:

1. A subscriber receives ordered events with `id`, `event`, and validated `data`.
2. `Last-Event-ID` resumes strictly after that event.
3. A run with no new events emits `heartbeat` within the configured block period.
4. A terminal event closes the response after it is sent.
5. An invalid/expired cursor returns `409` with a safe restart instruction.
6. A foreign tenant receives `404` and no stream data.
7. The request's SQLAlchemy session has no active transaction before the first
   blocking bus read.
8. Client disconnect cancels the Redis blocking read and releases the connection.

- [ ] **Step 2: Confirm new assertions fail against the incomplete endpoint**

```bash
cd backend
uv run pytest tests/api/test_run_routes.py \
  tests/isolation/test_run_isolation.py tests/integration/test_run_replay.py -q
```

Expected: the events endpoint is missing or does not meet replay requirements.

- [ ] **Step 3: Implement the streaming endpoint**

Authorize the run in PostgreSQL, copy immutable IDs to a frozen scope object,
then explicitly end the transaction before constructing `StreamingResponse`.
The generator reads from Redis only, validates every returned envelope against
the authorized scope, emits heartbeat envelopes at the configured interval, and
stops after one terminal event. Set:

```python
RUN_SSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
```

Do not echo Redis/provider exceptions. Emit `run.failed` only when a persisted
run is already terminal; otherwise close so the client can reconnect.

- [ ] **Step 4: Verify resource release and regressions**

```bash
cd backend
uv run pytest tests/api/test_run_routes.py \
  tests/isolation/test_run_isolation.py tests/integration/test_run_replay.py \
  tests/api/test_chat_stream.py -q
uv run ruff check src tests
uv run mypy src/openrag
```

Expected: all commands pass and the resource-release assertion observes no DB
transaction during blocking stream reads.

- [ ] **Step 5: Commit the replay endpoint**

```bash
git add backend/src/openrag/api/routes/runs.py backend/src/openrag/api/app.py \
  backend/tests/api/test_run_routes.py \
  backend/tests/isolation/test_run_isolation.py \
  backend/tests/integration/test_run_replay.py
git commit -m "feat: stream replayable run events"
```

---

### Task 8: Configure connection budgets and deploy durable event services

**Files:**
- Modify: `backend/src/openrag/core/config.py`
- Modify: `backend/src/openrag/core/db.py`
- Modify: `backend/src/openrag/api/app.py`
- Modify: `deploy/compose.yaml`
- Modify: `backend/tests/core/test_config.py`
- Modify: `backend/tests/core/test_db.py`
- Modify: `backend/tests/test_compose.py`

**Interfaces:**
- Produces: explicit DB pool settings and shared lifecycle-managed async clients.
- Produces: Compose `event-relay` and `agent-runner` services plus persistent Redis AOF storage.
- Guarantees: calculated deployment connection budget remains below configured PostgreSQL capacity.

- [ ] **Step 1: Write configuration and Compose tests**

Add assertions for these defaults:

```python
assert settings.database_pool_size == 10
assert settings.database_max_overflow == 5
assert settings.database_pool_timeout_seconds == 5.0
assert settings.agent_runner_concurrency == 32
```

Add a `connection_budget(settings, api_replicas, runner_replicas)` test that
asserts the total maximum connections equals
`(pool_size + max_overflow) * (api_replicas + runner_replicas)` and rejects a
budget above `database_connection_budget`.

Compose tests must assert:

- Redis command contains `--appendonly yes` and `--appendfsync everysec`;
- Redis mounts `redisdata:/data`;
- `event-relay` runs `python -m openrag.modules.events.relay`;
- `agent-runner` runs `python -m openrag.modules.runs.runner`;
- both depend on healthy Redis and completed bootstrap;
- neither exposes a host port;
- the LiteLLM proxy remains present only until the next plan migrates model
  execution.

- [ ] **Step 2: Confirm new configuration tests fail**

```bash
cd backend
uv run pytest tests/core/test_config.py tests/core/test_db.py \
  tests/test_compose.py -q
```

Expected: missing settings/services assertions fail.

- [ ] **Step 3: Implement explicit engine configuration**

Change the engine factory to accept settings or explicit keyword values and pass:

```python
create_async_engine(
    url,
    pool_pre_ping=True,
    pool_size=pool_size,
    max_overflow=max_overflow,
    pool_timeout=pool_timeout_seconds,
)
```

Test fixtures may continue using the same defaults. Add a pure
`validate_connection_budget` helper that raises a descriptive `ValueError` at
startup if configured replicas and pools exceed the declared budget.

- [ ] **Step 4: Add Compose services and Redis persistence**

Reuse the backend image/environment. Relay and runner processes must handle
SIGTERM, close Redis/engine clients, and have `restart: unless-stopped`. Add
`redisdata` to named volumes. Preserve localhost-only ports for development.

- [ ] **Step 5: Verify configuration, Compose, backend regressions, and image build**

```bash
cd backend
uv run pytest tests/core/test_config.py tests/core/test_db.py \
  tests/test_compose.py -q
uv run ruff check src tests
uv run mypy src/openrag
cd ..
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml build api event-relay agent-runner
```

Expected: all tests/static checks pass, Compose validates, and all three images
build successfully.

- [ ] **Step 6: Commit deployment capacity controls**

```bash
git add backend/src/openrag/core/config.py backend/src/openrag/core/db.py \
  backend/src/openrag/api/app.py deploy/compose.yaml \
  backend/tests/core/test_config.py backend/tests/core/test_db.py \
  backend/tests/test_compose.py
git commit -m "feat: deploy durable event workers"
```

---

### Task 9: Add comprehensive load, fault, and Docker smoke gates

**Files:**
- Create: `backend/tests/load/__init__.py`
- Create: `backend/tests/load/test_100_run_streams.py`
- Create: `scripts/smoke_run_events.py`
- Modify: `backend/pyproject.toml`
- Modify: `README.md`

**Interfaces:**
- Produces: opt-in `load` pytest marker and a deterministic 100-stream test.
- Produces: a Compose smoke command covering login, chat creation, run acceptance, replay, cancellation, and tenant denial.
- Consumes: deployed API/Web/PostgreSQL/Redis/relay/runner services.

- [ ] **Step 1: Write the deterministic 100-stream load test**

The test must:

1. Create ten authenticated users in one organization and ten isolated
   workspaces.
2. Start ten run streams per user concurrently with unique request IDs.
3. Use a deterministic executor that emits 50 coalesced deltas over one second.
4. Reconnect half of the streams once using `Last-Event-ID`.
5. Cancel ten runs midstream.
6. Assert exactly 90 completed and 10 cancelled terminal events.
7. Assert every run has strictly increasing sequences and no duplicate logical
   event IDs.
8. Assert no event contains another user's run/workspace ID.
9. Assert checked-out DB connections return to the baseline while streams remain
   active.
10. Assert event queues and Redis stream lengths stay within configured bounds.

Mark it with `@pytest.mark.load` and skip it unless
`OPENRAG_RUN_LOAD_TESTS=1`.

- [ ] **Step 2: Write the Docker smoke script**

`scripts/smoke_run_events.py` must use async HTTPX and environment-configurable
`OPENRAG_SMOKE_API_URL`, email, and password. It must:

- wait for `/readyz` with a bounded deadline;
- log in and preserve the bearer token;
- list/create a workspace and create a chat;
- accept a run with a UUID idempotency key;
- repeat acceptance and assert the same run ID;
- subscribe to events and validate schema/order;
- issue cancel and observe `run.cancelled`;
- log in as a second seeded user when configured and verify `404` for the run;
- exit nonzero with a concise stage name on any failure;
- print `OpenRAG run-event smoke passed` only after every assertion succeeds.

- [ ] **Step 3: Confirm the new gates fail before wiring**

```bash
cd backend
OPENRAG_RUN_LOAD_TESTS=1 uv run pytest \
  tests/load/test_100_run_streams.py -q
cd ..
python scripts/smoke_run_events.py
```

Expected: load assertions or smoke connectivity fail until the full foundation
is wired and running.

- [ ] **Step 4: Implement marker configuration and README commands**

Add this pytest marker:

```toml
markers = [
    "load: deterministic concurrency, soak, and resource-bound tests",
]
```

Document exact commands:

```bash
cd backend && OPENRAG_RUN_LOAD_TESTS=1 uv run pytest tests/load -q
docker compose -f deploy/compose.yaml up -d --build
python scripts/smoke_run_events.py
```

- [ ] **Step 5: Run the complete verification matrix**

```bash
cd backend
uv run pytest -q
OPENRAG_RUN_LOAD_TESTS=1 uv run pytest tests/load/test_100_run_streams.py -q
uv run ruff check src tests
uv run mypy src/openrag
uv run lint-imports
cd ../frontend
pnpm test --run
pnpm lint
pnpm typecheck
pnpm build
cd ..
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml up -d --build
python scripts/smoke_run_events.py
docker compose -f deploy/compose.yaml ps
```

Expected:

- all backend, load, frontend, lint, type, and architecture tests pass;
- Compose services are healthy;
- smoke output ends with `OpenRAG run-event smoke passed`;
- no logs contain the configured canary secret or raw test prompt body.

- [ ] **Step 6: Inspect failures and runtime health before committing**

```bash
docker compose -f deploy/compose.yaml logs --since=10m api event-relay agent-runner
git diff --check
git status --short
```

Expected: no unhandled exceptions, retry loops, pool timeouts, cross-tenant data,
or unexpected tracked files. `anything-llm/` and `openui/` remain untracked and
untouched.

- [ ] **Step 7: Commit the comprehensive verification gates**

```bash
git add backend/tests/load backend/pyproject.toml scripts/smoke_run_events.py README.md
git commit -m "test: verify concurrent run event delivery"
git push origin main
```

---

## Plan self-review checklist

- Every requirement in Slice 1 of the enterprise design maps to a task above.
- Run persistence, event ordering, replay, idempotency, cancellation,
  backpressure bounds, DB resource release, Compose topology, tenant isolation,
  failure injection, load, and smoke verification are explicitly covered.
- Existing chat behavior stays available until the Agno/in-process LiteLLM plan
  switches the frontend and removes the proxy.
- The plan introduces no OpenUI/AnythingLLM writes and no direct provider SDK.
- All public identifiers and function signatures used by later tasks are defined
  in earlier tasks.
- No milestone is considered complete solely from unit tests; full regression,
  browser/static, load, Compose, and smoke evidence is required.
