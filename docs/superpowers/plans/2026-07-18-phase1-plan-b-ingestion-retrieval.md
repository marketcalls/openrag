# OpenRAG Phase 1 — Plan B: Ingestion & Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Async document ingestion (multipart upload → MinIO → Celery chain parse → chunk → embed → upsert into Qdrant with live per-stage job status) and the **single** hybrid-retrieval code path (`modules/retrieval/retrieve()` — dense TEI + sparse FastEmbed, RRF fusion, tenant/workspace must-filters), proven by an adversarial tenant-isolation suite. Also lands the carryover hardening from the Plan A final review (catch-all 500 handler, IntegrityError→409, Redis-backed rate limiting, two ADRs) and a real product route `POST /workspaces/{id}/search` so the stack is smokeable before chat exists.

**Architecture:** Modular monolith, `api/worker → modules → core` (import-linter). New module `documents` (upload, jobs, pipeline stages, deletion propagation) and `retrieval` (Qdrant client, collection, THE filter builder, hybrid search). `worker/` is a new thin Celery entrypoint layer. Iron rules 1 & 2 are this plan's core: **no code outside `modules/retrieval/` constructs Qdrant filters**, and `backend/tests/isolation/` proves it adversarially. This plan is 2 of 4; Plan C (chat) consumes `retrieve()`, the chunk payload shape, the Redis client on `app.state`, and the catch-all handler.

**Tech Stack additions:** Celery 5 + Redis (priority queues), qdrant-client (async), fastembed (BM25 sparse), Docling (parsing), aioboto3 (MinIO/S3), httpx (TEI client), testcontainers Redis/Qdrant/MinIO.

**Format note — this plan builds on the MERGED Plan A code**, whose interfaces differ slightly from the Plan A document: `get_session` lives in `core/db.py` (re-exported by `api/deps.py`), datetimes are written **naive UTC** (`datetime.now(UTC).replace(tzinfo=None)`), compose binds `127.0.0.1:55432` (Postgres) / `127.0.0.1:56379` (Redis), and `core/errors.py` already has `RateLimitExceeded`. All code below consumes those real interfaces.

## Global Constraints

- Carried from Plan A: `.env` is bootstrap config only; typed module exceptions → one RFC 9457 problem+json handler; routers accept/return Pydantic schemas only, no inline role checks; structlog JSON with secret redaction; Conventional Commits; integration tests on real stores via testcontainers — never mock Postgres/Redis/Qdrant/MinIO.
- **Iron rule 1:** No Qdrant filter construction outside `modules/retrieval/`. The only filter builder is `_tenant_filter()` in `modules/retrieval/service.py`; `retrieve()` and `delete_document_points()` are its only callers. Upsert constructs points/payloads (not filters) and may live in the documents pipeline.
- **Iron rule 2:** `backend/tests/isolation/` runs adversarial leak tests on every PR, including a canary test proving an unfiltered query DOES see both orgs (so a filter regression cannot pass silently).
- Pipeline stage functions in `modules/documents/pipeline.py` are pure-ish and unit-testable (no DB/session args). Celery tasks in `worker/tasks.py` are thin sync wrappers: `asyncio.run(...)` over async runner functions in `modules/documents/ingest.py` (ADR-0001 seam).
- Embeddings are stubbable: dense embedding goes through the `DenseEmbedder` protocol selected by `Settings.embedding_backend` (`tei` | `hash`). **TEI and Docling model downloads must never be required by the test suite** — tests use the deterministic hash backend; Docling parses DOCX/MD/TXT without model downloads (PDF layout models are exercised only in the real-stack smoke). FastEmbed's `Qdrant/bm25` sparse model IS used for real in tests: it is deterministic, its artifact is KB-scale and cached, and it keeps sparse-retrieval assertions meaningful.
- All new compose services have healthchecks. TEI sits behind a compose profile (`ml`) so dev/CI without it still works.
- Heavy imports (`docling`, `fastembed`) are deferred to inside the functions that use them so the API process never pays their import cost.
- Full gate before every commit: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`. All commands run from `backend/` unless stated.

---

### Task 1: Carryover — catch-all 500 handler + IntegrityError→409

**Files:**
- Modify: `backend/src/openrag/api/app.py`
- Test: `backend/tests/api/test_error_handlers.py`

**Interfaces:**
- Consumes: `OpenRAGError` hierarchy (`core/errors.py`), `create_app` (`api/app.py`), duplicate-member IntegrityError path (`tenancy/service.add_member`).
- Produces: unhandled exceptions → `500 application/problem+json` with generic detail (real exception text never leaks; logged via structlog with method/path); `sqlalchemy.exc.IntegrityError` → `409` problem+json. Plan C relies on the catch-all existing.

**Decision (IntegrityError placement):** handler-level catch in `api/app.py`, not a per-service helper. Rationale: one choke point covers every service without boilerplate; DB constraint races (duplicate member insert, concurrent duplicate upload beating the dedup pre-check) become uniform 409s; services still raise explicit `ConflictError` on pre-checked paths for readable messages. Worker code doesn't need the pattern — tasks own their transaction and record failures on the document row.

- [ ] **Step 1: Write failing tests**

`backend/tests/api/test_error_handlers.py`:

```python
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from openrag.api.app import create_app
from openrag.core.db import build_session_factory
from openrag.modules.auth.models import User


@pytest.fixture
async def crashy_client(engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(session_factory=build_session_factory(engine))

    @app.get("/probe/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom internal secret detail")

    # raise_app_exceptions=False: Starlette's ServerErrorMiddleware re-raises after
    # sending the handler's response; without this flag the transport surfaces the
    # exception instead of the 500 body.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_catch_all_returns_generic_problem_json(crashy_client: httpx.AsyncClient) -> None:
    r = await crashy_client.get("/probe/boom")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["title"] == "Internal error"
    assert "kaboom" not in r.text  # internals never leak


async def test_integrity_error_maps_to_409(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()
    r = await client.post("/api/v1/auth/login",
                          json={"email": "a@acme.com", "password": "pw123456"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    ws = await client.post("/api/v1/workspaces", json={"name": "F"}, headers=h)
    ws_id = ws.json()["id"]
    body = {"user_id": str(plain.id)}
    assert (await client.post(f"/api/v1/workspaces/{ws_id}/members", json=body,
                              headers=h)).status_code == 204
    # second insert violates the (workspace_id, user_id) primary key
    r2 = await client.post(f"/api/v1/workspaces/{ws_id}/members", json=body, headers=h)
    assert r2.status_code == 409
    assert r2.headers["content-type"].startswith("application/problem+json")
```

Run: `uv run pytest tests/api/test_error_handlers.py -v`
Expected: FAIL — first test surfaces `RuntimeError` body as 500 text/plain (no handler), second returns 500 not 409.

- [ ] **Step 2: Implement in `backend/src/openrag/api/app.py`**

Add imports and register both handlers inside `create_app` (after the existing `OpenRAGError` handler):

```python
import structlog
from sqlalchemy.exc import IntegrityError
```

```python
    logger = structlog.get_logger("openrag.api")

    def _problem(status: int, title: str, detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={"type": "about:blank", "title": title, "status": status, "detail": detail},
            media_type="application/problem+json",
        )

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("integrity_error", method=request.method, path=request.url.path)
        return _problem(409, "Conflict", "resource conflicts with existing state")

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_exception",
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else "unknown",
            exc_info=exc,
        )
        return _problem(500, "Internal error", "an unexpected error occurred")
```

- [ ] **Step 3: Run tests + gate**

Run: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS (2 new tests green; existing suite unaffected).

- [ ] **Step 4: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat: catch-all 500 and IntegrityError conflict handlers with problem+json"
```

---

### Task 2: Carryover — Redis-backed rate limiter

**Files:**
- Modify: `backend/src/openrag/core/ratelimit.py` (replace in-process limiter), `backend/src/openrag/api/app.py` (redis client on `app.state`), `backend/src/openrag/api/routes/auth.py` (guard refresh + invitation accept), `backend/pyproject.toml`, `backend/tests/conftest.py`, `backend/tests/api/test_ratelimit.py`

**Interfaces:**
- Consumes: `RateLimitExceeded` (already in `core/errors.py`), `Settings.redis_url`.
- Produces: `async check_rate_limit(redis: Redis, key: str, limit: int, window_seconds: int) -> None` (raises `RateLimitExceeded`); dependency factory `rate_limit(scope: str, limit: int = 10, window_seconds: int = 60)` — **same public interface as before**, now backed by `request.app.state.redis` (fixed window: `INCR` + `EXPIRE` on first hit); `create_app(session_factory=None, redis_client=None) -> FastAPI` with `app.state.redis: redis.asyncio.Redis` built from `settings.redis_url` when not injected. Guards: login 10/60s (kept), `/auth/refresh` 30/60s, `/auth/invitations/accept` 10/60s. Plan C reuses `app.state.redis` and `rate_limit()` for chat endpoints.
- Note: per-app-id keying from the old in-process limiter is gone (it defeated the point of a shared store); test isolation now comes from `FLUSHDB` per test. Redis outage propagates to the Task 1 catch-all (500) — acceptable fail-closed behaviour for auth endpoints.

- [ ] **Step 1: Add dependencies**

In `backend/pyproject.toml`: add `"redis>=5.0",` to `[project] dependencies`; change the dev testcontainers line to `"testcontainers[postgres,redis]>=4.5",`. Run: `uv sync`.

- [ ] **Step 2: Write failing tests**

Add to `backend/tests/conftest.py` (new imports + fixtures; **replace** the existing `client` fixture):

```python
from redis.asyncio import Redis
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as r:
        yield f"redis://{r.get_container_host_ip()}:{r.get_exposed_port(6379)}/0"


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[Redis]:
    r = Redis.from_url(redis_url)
    await r.flushdb()  # rate-limit counters must not leak across tests
    yield r
    await r.aclose()


@pytest.fixture
async def client(engine: AsyncEngine, redis_client: Redis) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(session_factory=build_session_factory(engine), redis_client=redis_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

Also update Task 1's `crashy_client` fixture in `tests/api/test_error_handlers.py` to accept `redis_client: Redis` and pass `redis_client=redis_client` to `create_app`.

Replace `backend/tests/api/test_ratelimit.py`:

```python
import asyncio

import httpx
import pytest
from redis.asyncio import Redis

from openrag.core.errors import RateLimitExceeded
from openrag.core.ratelimit import check_rate_limit
from openrag.modules.auth.models import User


async def test_check_rate_limit_blocks_then_window_resets(redis_client: Redis) -> None:
    for _ in range(2):
        await check_rate_limit(redis_client, "rl:t:1", limit=2, window_seconds=1)
    with pytest.raises(RateLimitExceeded):
        await check_rate_limit(redis_client, "rl:t:1", limit=2, window_seconds=1)
    await asyncio.sleep(1.1)  # fixed window expires via EXPIRE
    await check_rate_limit(redis_client, "rl:t:1", limit=2, window_seconds=1)


async def test_login_rate_limited(client: httpx.AsyncClient, seeded_user: User) -> None:
    for _ in range(10):
        await client.post("/api/v1/auth/login", json={"email": "a@acme.com", "password": "bad"})
    r = await client.post("/api/v1/auth/login",
                          json={"email": "a@acme.com", "password": "pw123456"})
    assert r.status_code == 429
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_refresh_rate_limited(client: httpx.AsyncClient, seeded_user: User) -> None:
    await client.post("/api/v1/auth/login",
                      json={"email": "a@acme.com", "password": "pw123456"})
    for _ in range(30):
        await client.post("/api/v1/auth/refresh")  # guard runs regardless of outcome
    assert (await client.post("/api/v1/auth/refresh")).status_code == 429


async def test_invitation_accept_rate_limited(client: httpx.AsyncClient) -> None:
    for _ in range(10):
        await client.post("/api/v1/auth/invitations/accept",
                          json={"token": "bogus", "password": "irrelevant1"})
    r = await client.post("/api/v1/auth/invitations/accept",
                          json={"token": "bogus", "password": "irrelevant1"})
    assert r.status_code == 429
```

Run: `uv run pytest tests/api/test_ratelimit.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_rate_limit'`.

- [ ] **Step 3: Implement**

Replace `backend/src/openrag/core/ratelimit.py` entirely:

```python
from collections.abc import Awaitable, Callable

from fastapi import Request
from redis.asyncio import Redis

from openrag.core.errors import RateLimitExceeded


async def check_rate_limit(redis: Redis, key: str, limit: int, window_seconds: int) -> None:
    """Fixed-window limiter: INCR the key, arm EXPIRE on the first hit in a window.

    Shared across processes/workers via Redis (replaces the Plan A in-process
    limiter behind the same `rate_limit()` public interface).
    """
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_seconds)
    if count > limit:
        raise RateLimitExceeded("rate limit exceeded, retry later")


def rate_limit(
    scope: str, limit: int = 10, window_seconds: int = 60
) -> Callable[[Request], Awaitable[None]]:
    async def guard(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        redis: Redis = request.app.state.redis
        await check_rate_limit(redis, f"rl:{scope}:{client_ip}", limit, window_seconds)

    return guard
```

In `backend/src/openrag/api/app.py`: add `from redis.asyncio import Redis` and change the factory signature/body:

```python
def create_app(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    redis_client: Redis | None = None,
) -> FastAPI:
```

and after `app.state.session_factory = session_factory`:

```python
    if redis_client is None:
        redis_client = Redis.from_url(get_settings().redis_url)
    app.state.redis = redis_client
```

In `backend/src/openrag/api/routes/auth.py`, guard the two additional routes:

```python
@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    dependencies=[Depends(rate_limit("refresh", limit=30, window_seconds=60))],
)
```

```python
@router.post(
    "/invitations/accept",
    status_code=201,
    dependencies=[Depends(rate_limit("invitation_accept", limit=10, window_seconds=60))],
)
```

(login keeps its existing `rate_limit("login", limit=10, window_seconds=60)` dependency.)

- [ ] **Step 4: Run tests + gate**

Run: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS. First run pulls the `redis:7-alpine` container.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests backend/pyproject.toml backend/uv.lock
git commit -m "feat: redis-backed fixed-window rate limiting on auth endpoints"
```

---

### Task 3: Carryover — ADR-0003 and ADR-0004

**Files:**
- Create: `docs/adr/ADR-0003-naive-utc-datetimes.md`, `docs/adr/ADR-0004-tenancy-auth-model-imports.md`

- [ ] **Step 1: Write `docs/adr/ADR-0003-naive-utc-datetimes.md`**

```markdown
# ADR-0003: Naive-UTC Datetimes in Postgres

**Date:** 2026-07-18
**Status:** Accepted

## Context

SQLAlchemy maps `Mapped[datetime]` to `TIMESTAMP WITHOUT TIME ZONE` by default, and
asyncpg rejects timezone-aware Python datetimes for such columns. Plan A shipped all
timestamp columns as naive; mid-phase migration of every column to
`DateTime(timezone=True)` would churn every table and risk a mixed-column state where
some comparisons silently misbehave. Alternatives considered: (A) keep naive columns
and standardize on naive-UTC values; (B) migrate all columns to timestamptz now;
(C) per-column choice.

## Decision

Option A. All persisted datetimes are naive UTC:

- Writes go through `openrag.core.db.naive_utc()` —
  `datetime.now(UTC).replace(tzinfo=None)` — the single write-path idiom (the
  `UUIDPk.created_at` default uses the same expression).
- Reads that must be compared against aware datetimes re-attach UTC:
  `value.replace(tzinfo=UTC)` (as `auth/service.py` already does for
  refresh-token expiry).

## Rationale

- Zero migration churn mid-phase; one convention everywhere beats a mixed state.
- UTC-only storage keeps ordering and arithmetic correct; the tz suffix carries no
  information when every value is UTC by construction.

## Consequences

- Comparing a DB datetime with `datetime.now(UTC)` without normalizing raises
  `TypeError` — a loud failure, not silent corruption; tests catch it immediately.
- API responses serialize naive values; the OpenAPI contract documents all
  timestamps as UTC. Revisit (single Alembic migration to timestamptz) if
  cross-timezone deployments ever read the DB directly.
```

- [ ] **Step 2: Write `docs/adr/ADR-0004-tenancy-auth-model-imports.md`**

```markdown
# ADR-0004: Tenancy ↔ Auth Sanctioned Model Sharing

**Date:** 2026-07-18
**Status:** Accepted

## Context

The Foundation says modules import other modules' public services only — never ORM
models. The merged Plan A code crosses that line in one place, in both directions:
`tenancy/context.py` imports `auth.models.User` (resolving the request user is the
heart of `TenantContext`), and `auth/service.py` imports `tenancy.context.TenantContext`
plus checks org scoping via `User.org_id`. A strict service-only indirection would
require an `auth.get_user_by_id()` service, a duplicated user DTO, and a dependency
cycle workaround — for two modules that are conceptually one identity boundary.

## Decision

`auth` and `tenancy` are a **sanctioned model-sharing pair**: `tenancy` may import
`auth` ORM models read-only, and `auth` may import `tenancy`'s public context types.
Every other module pair remains service-only (e.g. `documents` and `retrieval` call
`tenancy.service.get_workspace_checked()`, never touch `Workspace` columns directly
outside their own queries via services). Layer direction (`api/worker → modules →
core`) stays enforced by import-linter.

## Rationale

- Identity (users) and tenancy (orgs/workspaces) are inherently coupled: `User.org_id`
  is the tenancy join key. Splitting them behind service facades adds indirection
  without isolation.
- An explicit, documented exception is safer than a fiction that review can't enforce.

## Consequences

- Schema changes to `User` are reviewed against both modules.
- Revisit when Phase 2 adds groups/custom roles: if a `groups` module lands, fold the
  pair boundary decision into that design.
```

- [ ] **Step 3: Commit**

```bash
git add docs/adr
git commit -m "docs: ADRs for naive-UTC datetimes and tenancy/auth model sharing"
```

---

### Task 4: Compose additions (Qdrant, MinIO, TEI), settings, dependencies

**Files:**
- Modify: `deploy/compose.yaml`, `.env.example`, `backend/src/openrag/core/config.py`, `backend/pyproject.toml`
- Test: `backend/tests/core/test_config.py` (extend)

**Interfaces:**
- Produces: `Settings` fields `qdrant_url`, `minio_endpoint`, `minio_access_key`, `minio_secret_key`, `minio_bucket`, `tei_url`, `embedding_backend` (`"tei"` | `"hash"`), `embedding_dim` (1024), `max_upload_mb` (100), `interactive_upload_mb` (10). Compose services `qdrant` (127.0.0.1:56333), `minio` (127.0.0.1:59000 API / 59001 console, dev-only creds openrag/openrag123), `tei` (127.0.0.1:58080, profile `ml`, serves BAAI/bge-m3).

- [ ] **Step 1: Extend failing test**

Append to `backend/tests/core/test_config.py`:

```python
def test_ingestion_settings_defaults(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    s = Settings(_env_file=None)
    assert s.qdrant_url == "http://localhost:56333"
    assert s.minio_endpoint == "http://localhost:59000"
    assert s.minio_bucket == "openrag-documents"
    assert s.tei_url == "http://localhost:58080"
    assert s.embedding_backend == "tei"
    assert s.embedding_dim == 1024
    assert s.interactive_upload_mb == 10
```

Run: `uv run pytest tests/core/test_config.py -v` — Expected: FAIL (`AttributeError: qdrant_url`).

- [ ] **Step 2: Add settings fields**

Append to the `Settings` class in `backend/src/openrag/core/config.py`:

```python
    # Plan B: ingestion & retrieval
    qdrant_url: str = "http://localhost:56333"
    minio_endpoint: str = "http://localhost:59000"
    minio_access_key: str = "openrag"
    minio_secret_key: str = "openrag123"  # dev-only default; prod overrides via env
    minio_bucket: str = "openrag-documents"
    tei_url: str = "http://localhost:58080"
    embedding_backend: str = "tei"  # "tei" | "hash" (hash = deterministic, test/dev only)
    embedding_dim: int = 1024  # bge-m3
    max_upload_mb: int = 100
    interactive_upload_mb: int = 10  # uploads below this jump to the interactive queue
```

- [ ] **Step 3: Extend `deploy/compose.yaml`**

Add these services (keep postgres/redis untouched) and the new volumes:

```yaml
  qdrant:
    image: qdrant/qdrant:v1.10.1
    ports: ["127.0.0.1:56333:6333"]
    volumes: [qdrantdata:/qdrant/storage]
    healthcheck:
      # qdrant image ships no curl/wget; bash /dev/tcp probes the HTTP port
      test: ["CMD-SHELL", "bash -c ':> /dev/tcp/127.0.0.1/6333' || exit 1"]
      interval: 5s
      timeout: 3s
      retries: 10

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: openrag       # dev-only credentials — override in prod env
      MINIO_ROOT_PASSWORD: openrag123
    ports: ["127.0.0.1:59000:9000", "127.0.0.1:59001:9001"]
    volumes: [miniodata:/data]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 5s
      timeout: 3s
      retries: 10

  tei:
    # First start downloads the BAAI/bge-m3 model (~2.3 GB) into the teidata volume;
    # start_period below allows for it. CPU image; GPU variants are a prod concern.
    image: ghcr.io/huggingface/text-embeddings-inference:cpu-1.5
    profiles: ["ml"]  # not required for CI/dev without ML; tests use the hash backend
    command: --model-id BAAI/bge-m3
    ports: ["127.0.0.1:58080:80"]
    volumes: [teidata:/data]
    healthcheck:
      test: ["CMD-SHELL", "bash -c ':> /dev/tcp/127.0.0.1/80' || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 180s
```

```yaml
volumes:
  pgdata:
  qdrantdata:
  miniodata:
  teidata:
```

- [ ] **Step 4: Extend `.env.example`** (repo root; append)

```bash
OPENRAG_QDRANT_URL=http://localhost:56333
OPENRAG_MINIO_ENDPOINT=http://localhost:59000
OPENRAG_MINIO_ACCESS_KEY=openrag
OPENRAG_MINIO_SECRET_KEY=openrag123
OPENRAG_MINIO_BUCKET=openrag-documents
OPENRAG_TEI_URL=http://localhost:58080
OPENRAG_EMBEDDING_BACKEND=tei
```

(MinIO dev creds are bootstrap infra config like the Postgres password — not app secrets; iron rule 3 still holds.)

- [ ] **Step 5: Add remaining Plan B dependencies**

In `backend/pyproject.toml`, `[project] dependencies` — add:

```toml
    "httpx>=0.27",
    "celery[redis]>=5.4",
    "qdrant-client>=1.12",
    "fastembed>=0.6",
    "docling>=2.15",
    "aioboto3>=13.0",
    "python-multipart>=0.0.9",
```

(`httpx` moves into main deps — the TEI client uses it at runtime; it may stay in dev too.)

`[dependency-groups] dev` — change/add:

```toml
    "testcontainers[postgres,redis,qdrant,minio]>=4.5",
    "python-docx>=1.1",
```

Append mypy overrides (untyped third-party libs; celery's decorator is untyped):

```toml
[[tool.mypy.overrides]]
module = [
    "celery.*", "kombu.*", "fastembed.*", "aioboto3.*", "aiobotocore.*",
    "botocore.*", "docling.*", "docling_core.*",
]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "openrag.worker.tasks"
disallow_untyped_decorators = false
```

Run: `uv sync`
Expected: resolves and installs cleanly (docling pulls a sizeable dep tree; that is pip-level only — no model downloads).

- [ ] **Step 6: Verify stack + gate**

Run (repo root): `docker compose -f deploy/compose.yaml up -d qdrant minio && docker compose -f deploy/compose.yaml ps`
Expected: `qdrant` and `minio` `Up (healthy)` within ~20s.

Run (repo root): `docker compose -f deploy/compose.yaml --profile ml config --services`
Expected: lists `tei` alongside the others (do not start it yet).

Run (backend/): `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add deploy/compose.yaml .env.example backend/src backend/tests backend/pyproject.toml backend/uv.lock
git commit -m "feat: qdrant, minio, tei compose services; ingestion settings and deps"
```

---

### Task 5: Object storage wrapper (`core/storage.py`)

**Files:**
- Create: `backend/src/openrag/core/storage.py`
- Modify: `backend/tests/conftest.py` (MinIO fixtures)
- Test: `backend/tests/core/test_storage.py`

**Interfaces:**
- Consumes: `Settings` (Task 4), `NotFoundError`.
- Produces: `ObjectStorage(endpoint_url, access_key, secret_key, bucket)` with `async ensure_bucket() -> None`, `async put(key, data: bytes, content_type: str = ...) -> None`, `async get(key) -> bytes` (raises `NotFoundError`), `async delete(key) -> None` (idempotent); `build_storage(settings: Settings) -> ObjectStorage`. Test fixtures `minio_config` (session container) and `storage`.

**Decisions:** (a) **Location `core/`** — object storage is provider glue like `core/db.py`, sits below all modules (documents uses it now, chat exports will later); placing it inside `documents` would force a cross-module internal import later. (b) **aioboto3 over minio-py**: natively async (iron rule: no blocking I/O in request handlers) with no thread-executor plumbing, and S3-standard, so swapping MinIO for S3/R2 is config-only. Cost: heavier dependency — accepted. A client is opened per call (aioboto3 clients are async context managers); fine at Phase 1 rates.

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/conftest.py`:

```python
from testcontainers.minio import MinioContainer

from openrag.core.storage import ObjectStorage


@pytest.fixture(scope="session")
def minio_config() -> Iterator[dict[str, str]]:
    with MinioContainer() as m:
        cfg = m.get_config()
        yield {
            "endpoint": f"http://{cfg['endpoint']}",
            "access_key": cfg["access_key"],
            "secret_key": cfg["secret_key"],
        }


@pytest.fixture
async def storage(minio_config: dict[str, str]) -> ObjectStorage:
    s = ObjectStorage(
        endpoint_url=minio_config["endpoint"],
        access_key=minio_config["access_key"],
        secret_key=minio_config["secret_key"],
        bucket="openrag-test",
    )
    await s.ensure_bucket()
    return s
```

`backend/tests/core/test_storage.py`:

```python
import pytest

from openrag.core.errors import NotFoundError
from openrag.core.storage import ObjectStorage


async def test_put_get_delete_roundtrip(storage: ObjectStorage) -> None:
    await storage.put("org/ws/doc/file.txt", b"hello openrag", content_type="text/plain")
    assert await storage.get("org/ws/doc/file.txt") == b"hello openrag"
    await storage.delete("org/ws/doc/file.txt")
    with pytest.raises(NotFoundError):
        await storage.get("org/ws/doc/file.txt")


async def test_delete_missing_is_idempotent(storage: ObjectStorage) -> None:
    await storage.delete("does/not/exist")  # no raise


async def test_ensure_bucket_idempotent(storage: ObjectStorage) -> None:
    await storage.ensure_bucket()
    await storage.ensure_bucket()
```

Run: `uv run pytest tests/core/test_storage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'openrag.core.storage'`.

- [ ] **Step 2: Implement `backend/src/openrag/core/storage.py`**

```python
from typing import Any

import aioboto3
from botocore.exceptions import ClientError

from openrag.core.config import Settings
from openrag.core.errors import NotFoundError


class ObjectStorage:
    """Thin async S3 wrapper for MinIO (aioboto3). One bucket per deployment."""

    def __init__(self, *, endpoint_url: str, access_key: str, secret_key: str, bucket: str) -> None:
        self._session = aioboto3.Session()
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self.bucket = bucket

    def _client(self) -> Any:
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )

    async def ensure_bucket(self) -> None:
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self.bucket)
            except ClientError:
                await s3.create_bucket(Bucket=self.bucket)

    async def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        async with self._client() as s3:
            await s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    async def get(self, key: str) -> bytes:
        async with self._client() as s3:
            try:
                obj = await s3.get_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                raise NotFoundError(f"object not found: {key}") from exc
            body: bytes = await obj["Body"].read()
            return body

    async def delete(self, key: str) -> None:
        async with self._client() as s3:
            await s3.delete_object(Bucket=self.bucket, Key=key)  # S3 delete is idempotent


def build_storage(settings: Settings) -> ObjectStorage:
    return ObjectStorage(
        endpoint_url=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        bucket=settings.minio_bucket,
    )
```

- [ ] **Step 3: Run tests + gate**

Run: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS (first run pulls the MinIO container).

- [ ] **Step 4: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat: async S3 object storage wrapper for MinIO"
```

---

### Task 6: Documents module — models, migration, schemas

**Files:**
- Create: `backend/src/openrag/modules/documents/__init__.py`, `modules/documents/models.py`, `modules/documents/schemas.py`
- Modify: `backend/src/openrag/core/db.py` (add `naive_utc()`), `backend/migrations/env.py` (import documents models)
- Test: `backend/tests/modules/documents/test_models.py` (+ `tests/modules/documents/__init__.py`)

**Interfaces:**
- Produces: `naive_utc() -> datetime` in `core/db.py` (ADR-0003 single write idiom); `Document(id, org_id, workspace_id, filename, mime, size_bytes, content_hash, status: str in {"queued","processing","indexed","failed"}, error, storage_key, page_count, created_by, created_at, updated_at)` with unique `(workspace_id, content_hash)`; `IngestJob(id, document_id, stage: str in {"parse","chunk","embed","upsert"}, progress: float, error, started_at, finished_at, created_at)`; schemas `DocumentOut(id, filename, mime, size_bytes, status, page_count, error, created_at)`.

- [ ] **Step 1: Write failing test**

`backend/tests/modules/documents/test_models.py`:

```python
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.documents.models import Document, IngestJob
from openrag.modules.tenancy.models import Organization, Workspace


async def _seed(session: AsyncSession) -> tuple[Organization, Workspace, User]:
    org = Organization(name="Acme")
    session.add(org)
    await session.flush()
    ws = Workspace(org_id=org.id, name="Fin")
    user = User(org_id=org.id, email="a@a.com", password_hash="x", role="admin")
    session.add_all([ws, user])
    await session.flush()
    return org, ws, user


async def test_document_and_job_roundtrip(session: AsyncSession) -> None:
    org, ws, user = await _seed(session)
    doc = Document(org_id=org.id, workspace_id=ws.id, filename="a.pdf",
                   mime="application/pdf", size_bytes=10, content_hash="h1",
                   storage_key=f"{org.id}/{ws.id}/x/a.pdf", created_by=user.id)
    session.add(doc)
    await session.flush()
    session.add(IngestJob(document_id=doc.id, stage="parse"))
    await session.commit()

    found = (await session.execute(select(Document))).scalar_one()
    assert found.status == "queued" and found.page_count is None
    job = (await session.execute(select(IngestJob))).scalar_one()
    assert job.progress == 0.0 and job.finished_at is None


async def test_content_hash_unique_per_workspace(session: AsyncSession) -> None:
    org, ws, user = await _seed(session)
    common = dict(org_id=org.id, workspace_id=ws.id, filename="a.pdf",
                  mime="application/pdf", size_bytes=10, content_hash="dup",
                  storage_key="k", created_by=user.id)
    session.add(Document(**common))
    await session.commit()
    session.add(Document(**common))
    with pytest.raises(IntegrityError):
        await session.commit()
```

Run: `uv run pytest tests/modules/documents -v` — Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 2: Implement**

Add to `backend/src/openrag/core/db.py`:

```python
def naive_utc() -> datetime:
    """Naive-UTC now — the single datetime write idiom (ADR-0003)."""
    return datetime.now(UTC).replace(tzinfo=None)
```

`backend/src/openrag/modules/documents/models.py`:

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk, naive_utc


class Document(UUIDPk, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "content_hash", name="uq_documents_workspace_hash"),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    filename: Mapped[str]
    mime: Mapped[str]
    size_bytes: Mapped[int]
    content_hash: Mapped[str]
    status: Mapped[str] = mapped_column(default="queued")  # queued|processing|indexed|failed
    error: Mapped[str | None] = mapped_column(default=None)
    storage_key: Mapped[str]
    page_count: Mapped[int | None] = mapped_column(default=None)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)


class IngestJob(UUIDPk, Base):
    __tablename__ = "ingest_jobs"

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str]  # parse|chunk|embed|upsert
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
```

`backend/src/openrag/modules/documents/schemas.py`:

```python
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: UUID
    filename: str
    mime: str
    size_bytes: int
    status: str
    page_count: int | None
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
```

Add to `backend/migrations/env.py`: `import openrag.modules.documents.models  # noqa: F401`

- [ ] **Step 3: Run test, generate + apply migration**

Run: `uv run pytest tests/modules/documents -v` — Expected: PASS.

Run (compose stack up): `uv run alembic revision --autogenerate -m "documents and ingest jobs" && uv run alembic upgrade head`
Expected: generated file contains `create_table("documents"...)` with `uq_documents_workspace_hash` and `create_table("ingest_jobs"...)`; upgrade succeeds.

- [ ] **Step 4: Gate + commit**

Run: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS.

```bash
git add backend/src backend/tests backend/migrations
git commit -m "feat: document and ingest job models with dedup constraint and migration"
```

---

### Task 7: Embedders — protocol, TEI dense, deterministic hash dense, BM25 sparse

**Files:**
- Create: `backend/src/openrag/modules/retrieval/__init__.py`, `modules/retrieval/embeddings.py`
- Test: `backend/tests/modules/retrieval/test_embeddings.py` (+ `tests/modules/retrieval/__init__.py`)

**Interfaces:**
- Consumes: `Settings.embedding_backend/embedding_dim/tei_url`.
- Produces: `DenseEmbedder` protocol — `async def embed(self, texts: list[str]) -> list[list[float]]`; `TeiDenseEmbedder(base_url, batch_size=32, transport=None)`; `HashDenseEmbedder(dim=1024)` (deterministic bag-of-hashed-words, L2-normalized — texts sharing words get high cosine, disjoint texts ~0; the test-suite dense backend); `get_dense_embedder() -> DenseEmbedder` (lru_cached, reads `get_settings()`); `embed_sparse(texts: list[str]) -> list[SparseVector]` (fastembed `Qdrant/bm25`, sync/CPU — call via `asyncio.to_thread` from async code).

**Decision (sparse is real in tests):** fastembed BM25 is deterministic for a given text, its model artifact is KB-scale (cached in `~/.cache` after first fetch), and using it for real keeps sparse/keyword retrieval assertions meaningful. Dense stays stubbed because TEI needs a served 2.3 GB model.

- [ ] **Step 1: Write failing tests**

`backend/tests/modules/retrieval/test_embeddings.py`:

```python
import json
import math

import httpx

from openrag.modules.retrieval.embeddings import (
    HashDenseEmbedder,
    TeiDenseEmbedder,
    embed_sparse,
)


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_hash_embedder_deterministic_and_normalized() -> None:
    emb = HashDenseEmbedder(dim=64)
    [v1] = await emb.embed(["the flux capacitor hums"])
    [v2] = await emb.embed(["the flux capacitor hums"])
    assert v1 == v2 and len(v1) == 64
    assert math.isclose(sum(x * x for x in v1), 1.0, rel_tol=1e-6)


async def test_hash_embedder_overlap_beats_disjoint() -> None:
    emb = HashDenseEmbedder(dim=256)
    [q, hit, miss] = await emb.embed(
        ["flux capacitor invoice", "invoice 0231 for the flux capacitor", "quarterly kumquat report"]
    )
    assert _cos(q, hit) > _cos(q, miss)


async def test_tei_embedder_batches_and_parses() -> None:
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["inputs"]
        calls.append(inputs)
        return httpx.Response(200, json=[[0.1, 0.2]] * len(inputs))

    emb = TeiDenseEmbedder("http://tei", batch_size=2, transport=httpx.MockTransport(handler))
    vecs = await emb.embed(["a", "b", "c"])
    assert vecs == [[0.1, 0.2]] * 3
    assert [len(c) for c in calls] == [2, 1]  # batched


def test_sparse_bm25_hits_shared_terms() -> None:
    [doc, query] = embed_sparse(["invoice 0231 total due", "invoice 0231"])
    assert set(query.indices) & set(doc.indices)  # shared term indices
    assert all(v > 0 for v in doc.values)
```

Run: `uv run pytest tests/modules/retrieval/test_embeddings.py -v`
Expected: FAIL — module missing.

- [ ] **Step 2: Implement `backend/src/openrag/modules/retrieval/embeddings.py`**

```python
import hashlib
import math
import re
from functools import lru_cache
from typing import Any, Protocol

import httpx
from qdrant_client import models

from openrag.core.config import get_settings

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class DenseEmbedder(Protocol):
    """Seam that makes dense embeddings stubbable (tests use HashDenseEmbedder)."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class TeiDenseEmbedder:
    """Dense embeddings via a TEI server (bge-m3). Batched HTTP POST /embed."""

    def __init__(
        self,
        base_url: str,
        batch_size: int = 32,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._batch_size = batch_size
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=60.0, transport=self._transport
        ) as client:
            for i in range(0, len(texts), self._batch_size):
                batch = texts[i : i + self._batch_size]
                r = await client.post("/embed", json={"inputs": batch, "truncate": True})
                r.raise_for_status()
                out.extend(r.json())
        return out


class HashDenseEmbedder:
    """Deterministic stand-in for TEI (test/dev only): L2-normalized bag of hashed
    unigrams. Texts sharing words get high cosine; disjoint texts get ~0. With this
    backend, "semantic" similarity reduces to lexical overlap — tests are scoped
    accordingly; true semantic quality is validated in the real-stack smoke."""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode()).hexdigest()
            vec[int(digest, 16) % self._dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


@lru_cache
def get_dense_embedder() -> DenseEmbedder:
    settings = get_settings()
    if settings.embedding_backend == "hash":
        return HashDenseEmbedder(dim=settings.embedding_dim)
    return TeiDenseEmbedder(settings.tei_url)


@lru_cache
def _bm25_model() -> Any:
    from fastembed import SparseTextEmbedding  # deferred: heavy import

    return SparseTextEmbedding("Qdrant/bm25")


def embed_sparse(texts: list[str]) -> list[models.SparseVector]:
    """BM25-family sparse vectors (ADR-0002). Sync/CPU — wrap in asyncio.to_thread
    from async code."""
    return [
        models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())
        for e in _bm25_model().embed(texts)
    ]
```

- [ ] **Step 3: Run tests + gate**

Run: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS (first sparse test fetches the KB-scale `Qdrant/bm25` artifact once, then cached).

- [ ] **Step 4: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat: dense embedder protocol with TEI and deterministic hash backends, bm25 sparse"
```

---

### Task 8: Retrieval module — THE single Qdrant code path (iron rule 1)

**Files:**
- Create: `backend/src/openrag/modules/retrieval/client.py`, `modules/retrieval/service.py`
- Modify: `backend/src/openrag/core/errors.py` (add `WorkspaceAccessDenied`), `backend/src/openrag/modules/tenancy/service.py` (add `get_workspace_checked`), `backend/tests/conftest.py` (Qdrant fixtures + env/caches)
- Test: `backend/tests/modules/retrieval/test_retrieve.py`

**Interfaces (Plan C consumes these exactly):**
- Produces: `WorkspaceAccessDenied(AuthorizationError)` (403, in `core/errors.py` so tenancy and retrieval share it without cycles); `get_workspace_checked(session, ctx, workspace_id) -> Workspace` in `tenancy/service.py` (org-scoped; role `"user"` additionally requires membership; raises `WorkspaceAccessDenied` with a non-leaky detail); `get_qdrant() -> AsyncQdrantClient` (lru_cached, `settings.qdrant_url`); `COLLECTION = "chunks_bge_m3"`; `async ensure_collection(embedding_model: str = "bge-m3") -> str` (1024-d cosine `dense` + IDF-modified `sparse` named vectors, keyword payload indexes on `tenant_id`/`workspace_id`/`document_id`, created with the collection; raises `ValueError` for any other model — the Phase 1 embedding-model lock); `RetrievedChunk(document_id: UUID, page: int, chunk_index: int, text: str, score: float)`; `RetrievalResult(chunks: list[RetrievedChunk], no_answer: bool)` (chunks stay populated when `no_answer` — the "nearest sources" for CHAT-9); **`async def retrieve(session: AsyncSession, ctx: TenantContext, workspace_id: UUID, query: str, top_k: int = 8) -> RetrievalResult`**; `async delete_document_points(org_id: UUID, document_id: UUID) -> None`.
- **`_tenant_filter()` is module-private and is the ONLY place in the codebase that constructs a Qdrant filter.** `delete_document_points` also lives here so filter/collection knowledge stays in one place (it takes `org_id` in addition to the spec's `document_id` for defense in depth — callers already hold the row).

**Decision (min_score vs RRF):** RRF fused scores are rank-based and unitless (~`Σ 1/(60+rank)`, max ≈0.03), so the workspace `min_score` (cosine-calibrated, default 0.35) cannot be applied to them. `retrieve()` therefore runs the hybrid query for ordering, plus one cheap dense top-1 query to get the best cosine; `no_answer = best_cosine < workspace.min_score`. This keeps `min_score` semantics stable ("how close is the closest chunk, in cosine terms") regardless of fusion internals.

- [ ] **Step 1: Add error + tenancy helper**

Append to `backend/src/openrag/core/errors.py`:

```python
class WorkspaceAccessDenied(AuthorizationError):
    title = "Workspace access denied"
```

Append to `backend/src/openrag/modules/tenancy/service.py`:

```python
from openrag.core.errors import WorkspaceAccessDenied  # add to imports


async def get_workspace_checked(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> Workspace:
    """The one workspace-access gate used by documents and retrieval (iron rule 1's
    Postgres-side counterpart). Same 403 for cross-org and non-member so existence
    never leaks."""
    ws = (
        await session.execute(
            select(Workspace).where(Workspace.id == workspace_id, Workspace.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if ws is None:
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    if ctx.role == "user" and workspace_id not in ctx.workspace_ids:
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    return ws
```

- [ ] **Step 2: Add Qdrant fixtures and env plumbing to `backend/tests/conftest.py`**

```python
from testcontainers.qdrant import QdrantContainer

from openrag.core.config import get_settings
from openrag.modules.retrieval.client import get_qdrant
from openrag.modules.retrieval.embeddings import get_dense_embedder


@pytest.fixture(scope="session")
def qdrant_url() -> Iterator[str]:
    with QdrantContainer("qdrant/qdrant:v1.10.1") as q:
        yield f"http://{q.get_container_host_ip()}:{q.get_exposed_port(6333)}"


def _clear_caches() -> None:
    get_settings.cache_clear()
    get_qdrant.cache_clear()  # also drops the client's httpx pool between event loops
    get_dense_embedder.cache_clear()


@pytest.fixture
def stack_env(
    pg_url: str,
    qdrant_url: str,
    minio_config: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Point ambient settings at the test containers; dense backend = deterministic
    hash (no TEI, no model downloads)."""
    monkeypatch.setenv("OPENRAG_DATABASE_URL", pg_url)
    monkeypatch.setenv("OPENRAG_QDRANT_URL", qdrant_url)
    monkeypatch.setenv("OPENRAG_MINIO_ENDPOINT", minio_config["endpoint"])
    monkeypatch.setenv("OPENRAG_MINIO_ACCESS_KEY", minio_config["access_key"])
    monkeypatch.setenv("OPENRAG_MINIO_SECRET_KEY", minio_config["secret_key"])
    monkeypatch.setenv("OPENRAG_MINIO_BUCKET", "openrag-test")
    monkeypatch.setenv("OPENRAG_EMBEDDING_BACKEND", "hash")
    _clear_caches()
    yield
    _clear_caches()


@pytest.fixture
async def qdrant_collection(stack_env: None) -> None:
    """Fresh collection per test (the Qdrant container is session-scoped)."""
    from openrag.modules.retrieval.client import COLLECTION
    from openrag.modules.retrieval.service import ensure_collection

    client = get_qdrant()
    if await client.collection_exists(COLLECTION):
        await client.delete_collection(COLLECTION)
    await ensure_collection()
```

- [ ] **Step 3: Write failing tests**

`backend/tests/modules/retrieval/test_retrieve.py`:

```python
import asyncio
from uuid import uuid4

import pytest
from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import WorkspaceAccessDenied
from openrag.modules.auth.models import User
from openrag.modules.retrieval.client import COLLECTION, get_qdrant
from openrag.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
from openrag.modules.retrieval.service import delete_document_points, retrieve
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Organization, Workspace, WorkspaceMember


async def seed_workspace(
    session: AsyncSession, org_name: str, *, role: str = "user", member: bool = True,
    min_score: float = 0.0,
) -> tuple[TenantContext, Workspace]:
    org = Organization(name=org_name)
    session.add(org)
    await session.flush()
    ws = Workspace(org_id=org.id, name="ws", min_score=min_score)
    user = User(org_id=org.id, email=f"u@{org_name}.com", password_hash="x", role=role)
    session.add_all([ws, user])
    await session.flush()
    if member:
        session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id))
    await session.commit()
    ctx = TenantContext(
        user_id=user.id, org_id=org.id, role=role,
        workspace_ids=frozenset({ws.id}) if member else frozenset(),
    )
    return ctx, ws


async def upsert_texts(ctx: TenantContext, ws: Workspace, texts: list[str]) -> str:
    """Test seeding via raw points; production code goes through the pipeline."""
    document_id = str(uuid4())
    dense = await get_dense_embedder().embed(texts)
    sparse = await asyncio.to_thread(embed_sparse, texts)
    points = [
        models.PointStruct(
            id=str(uuid4()),
            vector={"dense": d, "sparse": s},
            payload={"tenant_id": str(ctx.org_id), "workspace_id": str(ws.id),
                     "document_id": document_id, "page": i + 1, "chunk_index": i,
                     "text": t, "doc_type": "text/plain", "date": "2026-07-18",
                     "acl_groups": []},
        )
        for i, (t, d, s) in enumerate(zip(texts, dense, sparse, strict=True))
    ]
    await get_qdrant().upsert(COLLECTION, points=points, wait=True)
    return document_id


async def test_retrieve_returns_matching_chunk(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orga")
    await upsert_texts(ctx, ws, ["the flux capacitor requires 1.21 gigawatts",
                                 "unrelated kumquat farming notes"])
    result = await retrieve(session, ctx, ws.id, "flux capacitor gigawatts", top_k=2)
    assert not result.no_answer
    assert result.chunks[0].text.startswith("the flux capacitor")
    assert result.chunks[0].page == 1 and result.chunks[0].chunk_index == 0


async def test_min_score_triggers_no_answer_with_nearest(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orgb", min_score=0.99)
    await upsert_texts(ctx, ws, ["some vaguely related text about invoices"])
    result = await retrieve(session, ctx, ws.id, "completely different query terms")
    assert result.no_answer
    assert result.chunks  # nearest sources still surfaced (CHAT-9)


async def test_empty_workspace_is_no_answer(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orgc")
    result = await retrieve(session, ctx, ws.id, "anything")
    assert result.no_answer and result.chunks == []


async def test_non_member_denied(session: AsyncSession, qdrant_collection: None) -> None:
    ctx, ws = await seed_workspace(session, "orgd", member=False)
    with pytest.raises(WorkspaceAccessDenied):
        await retrieve(session, ctx, ws.id, "anything")


async def test_admin_without_membership_allowed(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orge", role="admin", member=False)
    result = await retrieve(session, ctx, ws.id, "anything")
    assert result.chunks == []


async def test_delete_document_points(session: AsyncSession, qdrant_collection: None) -> None:
    ctx, ws = await seed_workspace(session, "orgf")
    doc_id = await upsert_texts(ctx, ws, ["target text to delete"])
    from uuid import UUID
    await delete_document_points(ctx.org_id, UUID(doc_id))
    result = await retrieve(session, ctx, ws.id, "target text to delete")
    assert result.chunks == []
```

Run: `uv run pytest tests/modules/retrieval/test_retrieve.py -v`
Expected: FAIL — `openrag.modules.retrieval.client`/`service` missing.

- [ ] **Step 4: Implement**

`backend/src/openrag/modules/retrieval/client.py`:

```python
from functools import lru_cache

from qdrant_client import AsyncQdrantClient

from openrag.core.config import get_settings

COLLECTION = "chunks_bge_m3"  # one collection per embedding model (foundation)


@lru_cache
def get_qdrant() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=get_settings().qdrant_url)
```

`backend/src/openrag/modules/retrieval/service.py`:

```python
"""THE single Qdrant search code path (iron rule 1).

`_tenant_filter` below is the only function in the codebase allowed to construct a
Qdrant filter. `retrieve()` and `delete_document_points()` are its only callers.
The adversarial suite in tests/isolation/ exists to catch any regression here.
"""

import asyncio
from dataclasses import dataclass
from uuid import UUID

from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.modules.retrieval.client import COLLECTION, get_qdrant
from openrag.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.service import get_workspace_checked


@dataclass(frozen=True)
class RetrievedChunk:
    document_id: UUID
    page: int
    chunk_index: int
    text: str
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    no_answer: bool


def _tenant_filter(
    *, org_id: UUID, workspace_id: UUID | None = None, document_id: UUID | None = None
) -> models.Filter:
    """The ONE Qdrant filter builder. tenant_id is always a must-condition;
    acl_groups intersection lands here in Phase 2 without touching callers."""
    must: list[models.Condition] = [
        models.FieldCondition(key="tenant_id", match=models.MatchValue(value=str(org_id)))
    ]
    if workspace_id is not None:
        must.append(
            models.FieldCondition(key="workspace_id", match=models.MatchValue(value=str(workspace_id)))
        )
    if document_id is not None:
        must.append(
            models.FieldCondition(key="document_id", match=models.MatchValue(value=str(document_id)))
        )
    return models.Filter(must=must)


async def ensure_collection(embedding_model: str = "bge-m3") -> str:
    """Idempotent collection setup. Any model other than bge-m3 is rejected —
    the Phase 1 embedding-model lock (workspaces default to bge-m3)."""
    if embedding_model != "bge-m3":
        raise ValueError(f"unsupported embedding model: {embedding_model}")
    client = get_qdrant()
    if not await client.collection_exists(COLLECTION):
        await client.create_collection(
            COLLECTION,
            vectors_config={
                "dense": models.VectorParams(
                    size=get_settings().embedding_dim, distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        for field in ("tenant_id", "workspace_id", "document_id"):
            await client.create_payload_index(
                COLLECTION, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD
            )
    return COLLECTION


async def retrieve(
    session: AsyncSession,
    ctx: TenantContext,
    workspace_id: UUID,
    query: str,
    top_k: int = 8,
) -> RetrievalResult:
    """Hybrid retrieval — the one code path (spec §3.3).

    1. Workspace access gate (typed WorkspaceAccessDenied).
    2. Embed query dense (backend per settings) + sparse (BM25).
    3. Qdrant prefetch dense + sparse under the tenant filter → RRF fusion.
    4. no_answer when the best DENSE COSINE is below workspace.min_score (RRF
       scores are rank-based/unitless, so the threshold is checked in cosine
       space via a dense top-1 query); nearest chunks are still returned.
    """
    ws = await get_workspace_checked(session, ctx, workspace_id)
    await ensure_collection(ws.embedding_model)
    dense_vec = (await get_dense_embedder().embed([query]))[0]
    sparse_vec = (await asyncio.to_thread(embed_sparse, [query]))[0]
    flt = _tenant_filter(org_id=ctx.org_id, workspace_id=workspace_id)
    client = get_qdrant()
    fused = await client.query_points(
        COLLECTION,
        prefetch=[
            models.Prefetch(query=dense_vec, using="dense", filter=flt, limit=top_k * 4),
            models.Prefetch(query=sparse_vec, using="sparse", filter=flt, limit=top_k * 4),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=flt,  # belt and braces on top of the filtered prefetches
        limit=top_k,
        with_payload=True,
    )
    chunks = []
    for p in fused.points:
        payload = p.payload or {}
        chunks.append(
            RetrievedChunk(
                document_id=UUID(str(payload["document_id"])),
                page=int(payload["page"]),
                chunk_index=int(payload["chunk_index"]),
                text=str(payload["text"]),
                score=float(p.score),
            )
        )
    if not chunks:
        return RetrievalResult(chunks=[], no_answer=True)
    top_dense = await client.query_points(
        COLLECTION, query=dense_vec, using="dense", query_filter=flt,
        limit=1, with_payload=False,
    )
    best_cosine = float(top_dense.points[0].score) if top_dense.points else 0.0
    return RetrievalResult(chunks=chunks, no_answer=best_cosine < ws.min_score)


async def delete_document_points(org_id: UUID, document_id: UUID) -> None:
    """Deletion propagation entry point — lives here so filter knowledge never
    leaves this module. org_id scoping is defense in depth beyond the spec's
    document_id filter."""
    await get_qdrant().delete(
        COLLECTION,
        points_selector=models.FilterSelector(
            filter=_tenant_filter(org_id=org_id, document_id=document_id)
        ),
        wait=True,
    )
```

- [ ] **Step 5: Run tests + gate**

Run: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS (first run pulls the Qdrant container).

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat: single-code-path hybrid retrieval with tenant must-filters and RRF"
```

---

### Task 9: Documents service — upload with dedup, listing, delete authorization

**Files:**
- Create: `backend/src/openrag/modules/documents/service.py`
- Test: `backend/tests/modules/documents/test_service.py`

**Interfaces:**
- Consumes: `get_workspace_checked` (Task 8), `build_storage` (Task 5), `record_audit(session, *, org_id, actor_id, action, target_type, target_id)` (audit module), `ConflictError`, `NotFoundError`, `WorkspaceAccessDenied`.
- Produces: `async create_from_upload(session, ctx, workspace_id, *, filename, mime, data: bytes) -> Document` (access gate → sha256 dedup per workspace (`ConflictError`, the DB unique constraint backstops races via the Task 1 handler) → MinIO put at `{org_id}/{workspace_id}/{document_id}/{filename}` → row `queued` → audit `document.uploaded`; enqueueing is the caller's job — see Task 12 decision); `async list_documents(session, ctx, workspace_id) -> list[Document]` (newest first); `async get_document_checked(session, ctx, document_id) -> Document` (org-scoped, role `"user"` needs workspace membership; `NotFoundError` cross-org so existence never leaks).
- Note: the upload path reads the file into memory (the Task 13 route caps at `max_upload_mb`, default 100 MB) — the hash must be complete before the dedup check, so true streaming buys nothing at Phase 1 sizes.

- [ ] **Step 1: Write failing tests**

`backend/tests/modules/documents/test_service.py`:

```python
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from openrag.core.storage import build_storage
from openrag.modules.audit.models import AuditEvent
from openrag.modules.documents.service import (
    create_from_upload,
    get_document_checked,
    list_documents,
)
from tests.modules.retrieval.test_retrieve import seed_workspace


async def test_upload_stores_row_object_and_audit(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "up1")
    doc = await create_from_upload(session, ctx, ws.id, filename="a.txt",
                                   mime="text/plain", data=b"hello world")
    assert doc.status == "queued" and doc.size_bytes == 11
    assert doc.storage_key == f"{ctx.org_id}/{ws.id}/{doc.id}/a.txt"
    storage = build_storage(get_settings())
    assert await storage.get(doc.storage_key) == b"hello world"
    actions = [e.action for e in (await session.execute(select(AuditEvent))).scalars()]
    assert "document.uploaded" in actions


async def test_duplicate_content_conflicts(session: AsyncSession, stack_env: None) -> None:
    ctx, ws = await seed_workspace(session, "up2")
    await create_from_upload(session, ctx, ws.id, filename="a.txt",
                             mime="text/plain", data=b"same bytes")
    with pytest.raises(ConflictError):
        await create_from_upload(session, ctx, ws.id, filename="b.txt",
                                 mime="text/plain", data=b"same bytes")


async def test_non_member_cannot_upload_or_list(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "up3", member=False)
    with pytest.raises(WorkspaceAccessDenied):
        await create_from_upload(session, ctx, ws.id, filename="a.txt",
                                 mime="text/plain", data=b"x")
    with pytest.raises(WorkspaceAccessDenied):
        await list_documents(session, ctx, ws.id)


async def test_list_and_get_checked(session: AsyncSession, stack_env: None) -> None:
    ctx, ws = await seed_workspace(session, "up4")
    doc = await create_from_upload(session, ctx, ws.id, filename="a.txt",
                                   mime="text/plain", data=b"abc")
    docs = await list_documents(session, ctx, ws.id)
    assert [d.id for d in docs] == [doc.id]
    assert (await get_document_checked(session, ctx, doc.id)).id == doc.id

    other_ctx, _ = await seed_workspace(session, "up5")
    with pytest.raises(NotFoundError):  # cross-org: existence never leaks
        await get_document_checked(session, other_ctx, doc.id)
```

Run: `uv run pytest tests/modules/documents/test_service.py -v`
Expected: FAIL — `service` missing.

- [ ] **Step 2: Implement `backend/src/openrag/modules/documents/service.py`**

```python
import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from openrag.core.storage import build_storage
from openrag.modules.audit.service import record_audit
from openrag.modules.documents.models import Document
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.service import get_workspace_checked


async def create_from_upload(
    session: AsyncSession,
    ctx: TenantContext,
    workspace_id: UUID,
    *,
    filename: str,
    mime: str,
    data: bytes,
) -> Document:
    ws = await get_workspace_checked(session, ctx, workspace_id)
    content_hash = hashlib.sha256(data).hexdigest()
    dup = (
        await session.execute(
            select(Document).where(
                Document.workspace_id == ws.id, Document.content_hash == content_hash
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise ConflictError(f"identical content already uploaded as document {dup.id}")
    doc = Document(
        org_id=ctx.org_id, workspace_id=ws.id, filename=filename, mime=mime,
        size_bytes=len(data), content_hash=content_hash, storage_key="",
        created_by=ctx.user_id,
    )
    session.add(doc)
    await session.flush()  # assigns doc.id for the storage key
    doc.storage_key = f"{ctx.org_id}/{ws.id}/{doc.id}/{filename}"
    storage = build_storage(get_settings())
    await storage.ensure_bucket()
    await storage.put(doc.storage_key, data, content_type=mime)
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="document.uploaded", target_type="document",
                       target_id=str(doc.id))
    await session.commit()
    return doc


async def list_documents(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> list[Document]:
    ws = await get_workspace_checked(session, ctx, workspace_id)
    stmt = (
        select(Document)
        .where(Document.workspace_id == ws.id)
        .order_by(Document.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def get_document_checked(
    session: AsyncSession, ctx: TenantContext, document_id: UUID
) -> Document:
    doc = (
        await session.execute(
            select(Document).where(Document.id == document_id, Document.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if doc is None:
        raise NotFoundError("document not found")
    if ctx.role == "user" and doc.workspace_id not in ctx.workspace_ids:
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    return doc
```

- [ ] **Step 3: Run tests + gate**

Run: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat: document upload with content-hash dedup, listing, access-checked lookup"
```

---

### Task 10: Pipeline stages — parse (Docling) and chunk (pure functions)

**Files:**
- Create: `backend/src/openrag/modules/documents/pipeline.py`
- Test: `backend/tests/modules/documents/test_pipeline_parse.py`, `test_pipeline_chunk.py`

**Interfaces:**
- Produces: `IngestFailure(Exception)` (terminal, non-retryable: bad input, not infrastructure); `PageBlock(page: int, text: str, kind: str)` with kind in `{"text","heading","table"}`; `Chunk(text: str, page: int, chunk_index: int)` (both frozen dataclasses, JSON-serializable via `asdict`); `parse_bytes(data: bytes, filename: str) -> list[PageBlock]` (sync/CPU — callers use `asyncio.to_thread`; Docling for PDF/DOCX/XLSX/CSV/MD, plain-text fast path for `.txt`; empty/unsupported → `IngestFailure` with reason); `chunk_blocks(blocks, *, target_chars=2000, overlap_ratio=0.15) -> list[Chunk]` (heading-aware, ~512 tokens ≈ 2000 chars, 15% overlap, tables kept whole, pure function).
- Note: constructing Docling's `DocumentConverter` and converting DOCX/MD/CSV/XLSX needs **no model downloads** (SimplePipeline); only PDF conversion pulls layout models — exercised in the real-stack smoke, not the test suite. The docling import is deferred to inside `parse_bytes` so API workers never pay it.

- [ ] **Step 1: Write failing chunker tests**

`backend/tests/modules/documents/test_pipeline_chunk.py`:

```python
from openrag.modules.documents.pipeline import Chunk, PageBlock, chunk_blocks


def blk(text: str, page: int = 1, kind: str = "text") -> PageBlock:
    return PageBlock(page=page, text=text, kind=kind)


def test_empty_input_returns_empty() -> None:
    assert chunk_blocks([]) == []


def test_single_short_block_single_chunk() -> None:
    chunks = chunk_blocks([blk("hello world", page=3)])
    assert chunks == [Chunk(text="hello world", page=3, chunk_index=0)]


def test_long_text_splits_with_overlap() -> None:
    words = " ".join(f"word{i}" for i in range(1200))  # ~8000 chars
    chunks = chunk_blocks([blk(words)], target_chars=2000, overlap_ratio=0.15)
    assert len(chunks) >= 3
    assert all(len(c.text) <= 2600 for c in chunks)  # target + slack, never unbounded
    # 15% overlap: the head of chunk N+1 repeats the tail of chunk N
    tail_words = chunks[0].text.split()[-10:]
    assert " ".join(tail_words) in chunks[1].text


def test_table_kept_whole_even_when_oversized() -> None:
    table = ("| a | b |\n" * 400).strip()  # ~3600 chars, beyond target
    chunks = chunk_blocks([blk("intro text"), blk(table, kind="table"), blk("outro")])
    table_chunks = [c for c in chunks if c.text == table]
    assert len(table_chunks) == 1  # never split, never merged


def test_heading_starts_new_chunk_when_buffer_substantial() -> None:
    body = "x" * 1200  # above target/2
    chunks = chunk_blocks([blk(body), blk("Chapter Two", kind="heading"), blk("more text")])
    assert len(chunks) == 2
    assert chunks[1].text.startswith("Chapter Two")


def test_indices_sequential_and_pages_tracked() -> None:
    chunks = chunk_blocks([blk("a", page=1), blk("b" * 3000, page=2, kind="table"),
                           blk("c", page=3)])
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].page == 1
```

Run: `uv run pytest tests/modules/documents/test_pipeline_chunk.py -v` — Expected: FAIL.

- [ ] **Step 2: Write failing parse tests**

`backend/tests/modules/documents/test_pipeline_parse.py`:

```python
import io

import pytest
from docx import Document as DocxBuilder

from openrag.modules.documents.pipeline import IngestFailure, parse_bytes


def build_docx() -> bytes:
    d = DocxBuilder()
    d.add_heading("Flux Capacitor Manual", level=1)
    d.add_paragraph("The flux capacitor requires 1.21 gigawatts of power.")
    d.add_heading("Billing", level=1)
    d.add_paragraph("Invoice 0231 covers the plutonium delivery.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_parse_docx_extracts_blocks_with_kinds() -> None:
    blocks = parse_bytes(build_docx(), "manual.docx")
    texts = " ".join(b.text for b in blocks)
    assert "1.21 gigawatts" in texts and "Invoice 0231" in texts
    assert any(b.kind == "heading" for b in blocks)
    assert all(b.page >= 1 for b in blocks)


def test_parse_txt_fast_path() -> None:
    blocks = parse_bytes(b"para one\n\npara two", "notes.txt")
    assert [b.text for b in blocks] == ["para one", "para two"]
    assert all(b.kind == "text" and b.page == 1 for b in blocks)


def test_empty_file_fails_with_reason() -> None:
    with pytest.raises(IngestFailure, match="empty"):
        parse_bytes(b"", "empty.txt")


def test_unsupported_format_fails_with_reason() -> None:
    with pytest.raises(IngestFailure):
        parse_bytes(b"\x00\x01garbage", "weird.xyz")
```

Run: `uv run pytest tests/modules/documents/test_pipeline_parse.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement `backend/src/openrag/modules/documents/pipeline.py`**

```python
"""Ingestion stage functions: parse → chunk → embed → upsert (spec §3.2).

Pure-ish and unit-testable: no sessions, no Celery. Orchestration and job-status
writes live in modules/documents/ingest.py; Celery wrappers in worker/tasks.py.
"""

import asyncio
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

from qdrant_client import models

from openrag.modules.retrieval.client import COLLECTION, get_qdrant
from openrag.modules.retrieval.embeddings import DenseEmbedder, embed_sparse

# Deterministic point ids: retried upserts overwrite instead of duplicating.
_CHUNK_NAMESPACE = UUID("6c7d9a52-3e1f-4b8a-9c0d-2f5e8b1a7d43")


class IngestFailure(Exception):
    """Terminal, non-retryable ingestion failure (bad input, not infrastructure)."""


@dataclass(frozen=True)
class PageBlock:
    page: int
    text: str
    kind: str  # "text" | "heading" | "table"


@dataclass(frozen=True)
class Chunk:
    text: str
    page: int
    chunk_index: int


def parse_bytes(data: bytes, filename: str) -> list[PageBlock]:
    """Docling parse to page-aware blocks. Sync/CPU — call via asyncio.to_thread."""
    if not data:
        raise IngestFailure("file is empty")
    suffix = Path(filename).suffix.lower()
    if suffix == ".txt":  # docling has no plain-text input format
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IngestFailure("text file is not valid UTF-8") from exc
        blocks = [
            PageBlock(page=1, text=p.strip(), kind="text")
            for p in text.split("\n\n") if p.strip()
        ]
        if not blocks:
            raise IngestFailure("document contains no extractable text")
        return blocks

    # Deferred heavy imports: keep docling out of the API process entirely.
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc import DocItemLabel, TableItem, TextItem

    heading_labels = {DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER}
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        try:
            result = DocumentConverter().convert(tmp_path, raises_on_error=True)
        except Exception as exc:  # docling raises assorted types for bad input
            raise IngestFailure(f"unsupported or unparsable document: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    blocks: list[PageBlock] = []
    for item, _level in result.document.iterate_items():
        prov = getattr(item, "prov", None)
        page = prov[0].page_no if prov else 1
        if isinstance(item, TableItem):
            text, kind = item.export_to_markdown(result.document), "table"
        elif isinstance(item, TextItem):
            text = item.text
            kind = "heading" if item.label in heading_labels else "text"
        else:
            continue
        if text.strip():
            blocks.append(PageBlock(page=page, text=text.strip(), kind=kind))
    if not blocks:
        raise IngestFailure("document contains no extractable text")
    return blocks


def _split_text(text: str, limit: int) -> list[str]:
    """Split into pieces of at most `limit` chars on whitespace boundaries."""
    words = text.split()
    pieces: list[str] = []
    buf: list[str] = []
    size = 0
    for w in words:
        if size + len(w) + 1 > limit and buf:
            pieces.append(" ".join(buf))
            buf, size = [], 0
        buf.append(w)
        size += len(w) + 1
    if buf:
        pieces.append(" ".join(buf))
    return pieces


def chunk_blocks(
    blocks: list[PageBlock], *, target_chars: int = 2000, overlap_ratio: float = 0.15
) -> list[Chunk]:
    """Heading-aware chunking: ~512 tokens ≈ 2000 chars, 15% overlap between
    consecutive chunks, tables emitted whole as their own chunk."""
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_page: int | None = None
    overlap_chars = int(target_chars * overlap_ratio)

    def flush(carry_overlap: bool) -> None:
        nonlocal buf, buf_page
        if not buf:
            return
        text = "\n\n".join(buf)
        chunks.append(Chunk(text=text, page=buf_page or 1, chunk_index=len(chunks)))
        if carry_overlap and overlap_chars > 0:
            tail = text[-overlap_chars:]
            cut = tail.find(" ")  # start the overlap on a word boundary
            buf = [tail[cut + 1 :] if 0 <= cut < len(tail) - 1 else tail]
            # buf_page intentionally kept: the overlap belongs to the same region
        else:
            buf, buf_page = [], None

    for block in blocks:
        if block.kind == "table":
            flush(carry_overlap=False)
            chunks.append(Chunk(text=block.text, page=block.page, chunk_index=len(chunks)))
            continue
        if block.kind == "heading" and buf and len("\n\n".join(buf)) >= target_chars // 2:
            flush(carry_overlap=False)
        for piece in _split_text(block.text, target_chars):
            # Flush BEFORE appending a piece that would overflow the target, so a
            # chunk never exceeds ~target + overlap chars (pieces are <= target).
            if buf and len("\n\n".join(buf)) + len(piece) > target_chars:
                flush(carry_overlap=True)
            if buf_page is None:
                buf_page = block.page
            buf.append(piece)
    flush(carry_overlap=False)
    return chunks


async def embed_batch(
    texts: list[str], dense_embedder: DenseEmbedder
) -> tuple[list[list[float]], list[models.SparseVector]]:
    """Embed one batch dense + sparse (spec §3.2 stage 3)."""
    dense = await dense_embedder.embed(texts)
    sparse = await asyncio.to_thread(embed_sparse, texts)
    return dense, sparse


async def upsert_points(
    *,
    org_id: UUID,
    workspace_id: UUID,
    document_id: UUID,
    mime: str,
    created_at: datetime,
    chunks: list[Chunk],
    dense: list[list[float]],
    sparse: list[models.SparseVector],
) -> None:
    """Upsert one batch of chunk points with the spec §2.2 payload. Constructs
    points, never filters (iron rule 1 — filters live in retrieval only)."""
    points = [
        models.PointStruct(
            id=str(uuid5(_CHUNK_NAMESPACE, f"{document_id}:{c.chunk_index}")),
            vector={"dense": d, "sparse": s},
            payload={
                "tenant_id": str(org_id),
                "workspace_id": str(workspace_id),
                "document_id": str(document_id),
                "page": c.page,
                "chunk_index": c.chunk_index,
                "text": c.text,
                "doc_type": mime,
                "date": created_at.isoformat(),
                "acl_groups": [],  # reserved: Phase 2 ACLs need no schema migration
            },
        )
        for c, d, s in zip(chunks, dense, sparse, strict=True)
    ]
    await get_qdrant().upsert(COLLECTION, points=points, wait=True)
```

- [ ] **Step 4: Run tests + gate**

Run: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS (docling's first import in the parse tests takes a few seconds; no downloads for DOCX).

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat: docling parse and heading-aware chunking pipeline stages"
```

---

### Task 11: Ingest runners — job tracking, embed+upsert, delete propagation

**Files:**
- Create: `backend/src/openrag/modules/documents/ingest.py`
- Test: `backend/tests/modules/documents/test_ingest.py`

**Interfaces:**
- Consumes: pipeline stages (Task 10), `ensure_collection`/`delete_document_points` (Task 8), `build_storage`, `record_audit`, `naive_utc`.
- Produces (all async, each opens/disposes its own engine from ambient settings — required because each Celery task invocation runs in a fresh `asyncio.run` loop, ADR-0001): `run_parse(document_id)` (MinIO get → `parse_bytes` → blocks JSON artifact at `{storage_key}.blocks.json` → `status=processing`, `page_count`); `run_chunk(document_id)` (blocks → `chunk_blocks` → `{storage_key}.chunks.json`); `run_embed_upsert(document_id)` (chunks → batches of 32 → `embed_batch` + `upsert_points`, per-batch progress on BOTH embed and upsert jobs → `status=indexed`); `run_delete(document_id, actor_id)` (Qdrant points → MinIO objects incl. artifacts → ingest_jobs + document row → audit `document.deleted`; idempotent when the row is already gone); `mark_failed(document_id, reason)` (used by the Celery `on_failure` hook after retry exhaustion). Every stage writes an `IngestJob` row (`started_at`/`progress`/`finished_at`/`error`); `IngestFailure` marks the document `failed` with the reason and re-raises (chain stops, no retry).

- [ ] **Step 1: Write failing tests**

`backend/tests/modules/documents/test_ingest.py`:

```python
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.core.storage import build_storage
from openrag.modules.audit.models import AuditEvent
from openrag.modules.documents.ingest import (
    mark_failed,
    run_chunk,
    run_delete,
    run_embed_upsert,
    run_parse,
)
from openrag.modules.documents.models import Document, IngestJob
from openrag.modules.documents.pipeline import IngestFailure
from openrag.modules.documents.service import create_from_upload
from openrag.modules.retrieval.service import retrieve
from tests.modules.retrieval.test_retrieve import seed_workspace

TEXT = b"The flux capacitor requires 1.21 gigawatts.\n\nInvoice 0231 covers plutonium."


async def _upload(session: AsyncSession, name: str) -> tuple:  # type: ignore[type-arg]
    ctx, ws = await seed_workspace(session, name)
    doc = await create_from_upload(session, ctx, ws.id, filename="n.txt",
                                   mime="text/plain", data=TEXT)
    return ctx, ws, doc


async def test_full_runner_sequence_indexes_document(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws, doc = await _upload(session, "ing1")
    await run_parse(doc.id)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)

    await session.refresh(doc)
    assert doc.status == "indexed" and doc.page_count == 1
    jobs = {j.stage: j for j in (await session.execute(
        select(IngestJob).where(IngestJob.document_id == doc.id))).scalars()}
    assert set(jobs) == {"parse", "chunk", "embed", "upsert"}
    assert all(j.finished_at is not None and j.progress == 1.0 for j in jobs.values())

    result = await retrieve(session, ctx, ws.id, "invoice 0231")
    assert result.chunks and result.chunks[0].document_id == doc.id

    raw = await build_storage(get_settings()).get(doc.storage_key + ".chunks.json")
    assert json.loads(raw)  # chunk artifact persisted between stages


async def test_parse_failure_marks_document_failed(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "ing2")
    doc = await create_from_upload(session, ctx, ws.id, filename="bad.xyz",
                                   mime="application/octet-stream", data=b"\x00junk")
    with pytest.raises(IngestFailure):
        await run_parse(doc.id)
    await session.refresh(doc)
    assert doc.status == "failed" and doc.error


async def test_mark_failed_records_reason(session: AsyncSession, stack_env: None) -> None:
    ctx, ws, doc = await _upload(session, "ing3")
    await mark_failed(doc.id, "boom after retries")
    await session.refresh(doc)
    assert doc.status == "failed" and doc.error == "boom after retries"


async def test_delete_propagates_everywhere(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws, doc = await _upload(session, "ing4")
    await run_parse(doc.id)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)

    await run_delete(doc.id, ctx.user_id)
    assert (await session.execute(
        select(Document).where(Document.id == doc.id))).scalar_one_or_none() is None
    result = await retrieve(session, ctx, ws.id, "invoice 0231")
    assert result.chunks == []
    actions = [e.action for e in (await session.execute(select(AuditEvent))).scalars()]
    assert "document.deleted" in actions
    await run_delete(doc.id, ctx.user_id)  # idempotent
```

Run: `uv run pytest tests/modules/documents/test_ingest.py -v` — Expected: FAIL (`ingest` missing).

- [ ] **Step 2: Implement `backend/src/openrag/modules/documents/ingest.py`**

```python
"""Async ingestion runners: orchestration + job status around the pure pipeline
stages. Called from Celery via asyncio.run (ADR-0001), so each runner owns its
engine lifecycle instead of sharing a loop-bound pool."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.core.db import build_engine, build_session_factory, naive_utc
from openrag.core.storage import ObjectStorage, build_storage
from openrag.modules.audit.service import record_audit
from openrag.modules.documents.models import Document, IngestJob
from openrag.modules.documents.pipeline import (
    Chunk,
    IngestFailure,
    PageBlock,
    chunk_blocks,
    embed_batch,
    parse_bytes,
    upsert_points,
)
from openrag.modules.retrieval.embeddings import get_dense_embedder
from openrag.modules.retrieval.service import delete_document_points, ensure_collection

_BATCH_SIZE = 32


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    engine = build_engine(get_settings().database_url)
    try:
        async with build_session_factory(engine)() as session:
            yield session
    finally:
        await engine.dispose()


async def _get_document(session: AsyncSession, document_id: UUID) -> Document:
    doc = (
        await session.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if doc is None:
        raise IngestFailure(f"document {document_id} no longer exists")
    return doc


async def _start_stage(session: AsyncSession, document_id: UUID, stage: str) -> IngestJob:
    job = IngestJob(document_id=document_id, stage=stage, started_at=naive_utc())
    session.add(job)
    await session.commit()  # visible immediately for live UI progress
    return job


async def _finish_stage(session: AsyncSession, job: IngestJob, error: str | None = None) -> None:
    job.finished_at = naive_utc()
    job.error = error
    if error is None:
        job.progress = 1.0
    await session.commit()


async def _fail(session: AsyncSession, doc: Document, job: IngestJob, reason: str) -> None:
    doc.status = "failed"
    doc.error = reason[:1000]
    await _finish_stage(session, job, error=reason[:1000])


def _storage() -> ObjectStorage:
    return build_storage(get_settings())


async def run_parse(document_id: UUID) -> None:
    async with _session() as session:
        doc = await _get_document(session, document_id)
        job = await _start_stage(session, document_id, "parse")
        storage = _storage()
        try:
            data = await storage.get(doc.storage_key)
            blocks = await asyncio.to_thread(parse_bytes, data, doc.filename)
        except IngestFailure as exc:
            await _fail(session, doc, job, str(exc))
            raise
        await storage.put(
            doc.storage_key + ".blocks.json",
            json.dumps([asdict(b) for b in blocks]).encode(),
            content_type="application/json",
        )
        doc.status = "processing"
        doc.page_count = max(b.page for b in blocks)
        await _finish_stage(session, job)


async def run_chunk(document_id: UUID) -> None:
    async with _session() as session:
        doc = await _get_document(session, document_id)
        job = await _start_stage(session, document_id, "chunk")
        storage = _storage()
        raw = await storage.get(doc.storage_key + ".blocks.json")
        blocks = [PageBlock(**b) for b in json.loads(raw)]
        chunks = chunk_blocks(blocks)
        if not chunks:
            await _fail(session, doc, job, "chunking produced no chunks")
            raise IngestFailure("chunking produced no chunks")
        await storage.put(
            doc.storage_key + ".chunks.json",
            json.dumps([asdict(c) for c in chunks]).encode(),
            content_type="application/json",
        )
        await _finish_stage(session, job)


async def run_embed_upsert(document_id: UUID) -> None:
    """Stages 3+4 in one runner: embedding a batch and upserting it immediately
    avoids persisting vectors between tasks; ingest_jobs still shows both stages."""
    async with _session() as session:
        doc = await _get_document(session, document_id)
        embed_job = await _start_stage(session, document_id, "embed")
        upsert_job = await _start_stage(session, document_id, "upsert")
        raw = await _storage().get(doc.storage_key + ".chunks.json")
        chunks = [Chunk(**c) for c in json.loads(raw)]
        await ensure_collection()  # workspaces are bge-m3-locked in Phase 1
        dense_embedder = get_dense_embedder()
        done = 0
        for i in range(0, len(chunks), _BATCH_SIZE):
            batch = chunks[i : i + _BATCH_SIZE]
            dense, sparse = await embed_batch([c.text for c in batch], dense_embedder)
            await upsert_points(
                org_id=doc.org_id, workspace_id=doc.workspace_id, document_id=doc.id,
                mime=doc.mime, created_at=doc.created_at,
                chunks=batch, dense=dense, sparse=sparse,
            )
            done += len(batch)
            embed_job.progress = upsert_job.progress = done / len(chunks)
            await session.commit()
        doc.status = "indexed"
        await _finish_stage(session, embed_job)
        await _finish_stage(session, upsert_job)


async def run_delete(document_id: UUID, actor_id: UUID | None) -> None:
    """One task propagating deletion: Qdrant points → MinIO objects → Postgres
    rows, with an audit entry (spec §2.3, DOC-8). Idempotent."""
    async with _session() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if doc is None:
            return
        await delete_document_points(doc.org_id, document_id)
        storage = _storage()
        for key in (doc.storage_key, doc.storage_key + ".blocks.json",
                    doc.storage_key + ".chunks.json"):
            await storage.delete(key)
        await session.execute(sa_delete(IngestJob).where(IngestJob.document_id == document_id))
        await record_audit(session, org_id=doc.org_id, actor_id=actor_id,
                           action="document.deleted", target_type="document",
                           target_id=str(document_id))
        await session.delete(doc)
        await session.commit()


async def mark_failed(document_id: UUID, reason: str) -> None:
    """Terminal-failure hook for the Celery on_failure callback."""
    async with _session() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if doc is not None:
            doc.status = "failed"
            doc.error = reason[:1000]
            await session.commit()
```

- [ ] **Step 3: Run tests + gate**

Run: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat: ingest runners with per-stage job tracking and delete propagation"
```

---

### Task 12: Celery app, priority queues, thin task wrappers

**Files:**
- Create: `backend/src/openrag/worker/__init__.py`, `worker/celery_app.py`, `worker/tasks.py`
- Modify: `backend/pyproject.toml` (import-linter layers gain `openrag.worker`)
- Test: `backend/tests/worker/test_celery.py` (+ `tests/worker/__init__.py`)

**Interfaces:**
- Consumes: ingest runners (Task 11), `Settings.redis_url/interactive_upload_mb`.
- Produces: `celery_app` (Redis broker+backend, `task_acks_late=True`, `worker_prefetch_multiplier=1`, queues `default` + `interactive`); tasks `documents.parse`, `documents.chunk`, `documents.embed_upsert`, `documents.delete` (bind=True, max_retries=3, exponential backoff `2**retries`; `IngestFailure` short-circuits without retry; `on_failure` calls `mark_failed`); `select_queue(size_bytes) -> str` (`"interactive"` when `< interactive_upload_mb`); `build_ingest_chain(document_id: str, queue: str)` (parse → chunk → embed_upsert signatures pinned to the queue); `enqueue_ingest(document_id: UUID, size_bytes: int) -> None`; `enqueue_delete(document_id: UUID, actor_id: UUID) -> None` (interactive queue). Worker start: `uv run celery -A openrag.worker.celery_app:celery_app worker -Q interactive,default -l info`.

**Decision (chain wiring location):** the chain is wired in `worker/tasks.py` (`enqueue_ingest`), called by the API route after `service.create_from_upload()` — NOT inside the documents service. Modules must not import `worker` (import direction `api/worker → modules`), so wiring it "in the service" would invert the layering. The import-linter layers become `["openrag.api", "openrag.worker", "openrag.modules", "openrag.core"]`: api may import worker's enqueue functions; worker may import modules; never the reverse. **Controller: flag if you'd rather use `send_task`-by-name from the service instead.**

- [ ] **Step 1: Update import-linter contract**

In `backend/pyproject.toml`:

```toml
[[tool.importlinter.contracts]]
name = "Layered architecture"
type = "layers"
layers = ["openrag.api", "openrag.worker", "openrag.modules", "openrag.core"]
```

- [ ] **Step 2: Write failing tests**

`backend/tests/worker/test_celery.py`:

```python
from openrag.worker.celery_app import celery_app
from openrag.worker.tasks import build_ingest_chain, select_queue


def test_celery_config() -> None:
    assert celery_app.conf.task_acks_late is True
    assert {q.name for q in celery_app.conf.task_queues} == {"default", "interactive"}


def test_queue_selection_by_size() -> None:
    assert select_queue(5 * 1024 * 1024) == "interactive"  # < 10 MB jumps the queue
    assert select_queue(10 * 1024 * 1024) == "default"
    assert select_queue(50 * 1024 * 1024) == "default"


def test_ingest_chain_structure() -> None:
    sig = build_ingest_chain("doc-id-123", "interactive")
    names = [t.task for t in sig.tasks]
    assert names == ["documents.parse", "documents.chunk", "documents.embed_upsert"]
    assert all(t.options.get("queue") == "interactive" for t in sig.tasks)
    assert all(t.args == ("doc-id-123",) for t in sig.tasks)
```

Run: `uv run pytest tests/worker -v` — Expected: FAIL (`openrag.worker` missing).

- [ ] **Step 3: Implement**

`backend/src/openrag/worker/celery_app.py`:

```python
from celery import Celery
from kombu import Queue

from openrag.core.config import get_settings


def build_celery() -> Celery:
    settings = get_settings()
    app = Celery("openrag", broker=settings.redis_url, backend=settings.redis_url)
    app.conf.update(
        task_acks_late=True,  # a killed worker re-delivers, pairs with idempotent upserts
        worker_prefetch_multiplier=1,  # long tasks: no hoarding
        task_default_queue="default",
        task_queues=(Queue("default"), Queue("interactive")),
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = build_celery()
```

`backend/src/openrag/worker/tasks.py`:

```python
"""Thin sync wrappers over async runners (ADR-0001: asyncio.run per task).

No business logic lives here — only retry/queue/failure plumbing.
"""

import asyncio
from typing import Any
from uuid import UUID

from celery import Task, chain

from openrag.core.config import get_settings
from openrag.modules.documents import ingest
from openrag.modules.documents.pipeline import IngestFailure
from openrag.worker.celery_app import celery_app

_MAX_RETRIES = 3


class IngestTask(Task):
    """Marks the document failed once retries are exhausted (or on IngestFailure)."""

    def on_failure(self, exc: Exception, task_id: str, args: tuple[Any, ...],
                   kwargs: dict[str, Any], einfo: Any) -> None:
        asyncio.run(ingest.mark_failed(UUID(str(args[0])), str(exc)))


def _run(self: Task, coro_factory: Any) -> None:
    try:
        asyncio.run(coro_factory())
    except IngestFailure:
        raise  # terminal: already recorded on the document; stops the chain, no retry
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries) from exc


@celery_app.task(base=IngestTask, bind=True, max_retries=_MAX_RETRIES, name="documents.parse")
def parse_task(self: Task, document_id: str) -> str:
    _run(self, lambda: ingest.run_parse(UUID(document_id)))
    return document_id


@celery_app.task(base=IngestTask, bind=True, max_retries=_MAX_RETRIES, name="documents.chunk")
def chunk_task(self: Task, document_id: str) -> str:
    _run(self, lambda: ingest.run_chunk(UUID(document_id)))
    return document_id


@celery_app.task(base=IngestTask, bind=True, max_retries=_MAX_RETRIES,
                 name="documents.embed_upsert")
def embed_upsert_task(self: Task, document_id: str) -> str:
    _run(self, lambda: ingest.run_embed_upsert(UUID(document_id)))
    return document_id


@celery_app.task(bind=True, max_retries=_MAX_RETRIES, name="documents.delete")
def delete_task(self: Task, document_id: str, actor_id: str | None = None) -> None:
    try:
        asyncio.run(ingest.run_delete(UUID(document_id),
                                      UUID(actor_id) if actor_id else None))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries) from exc


def select_queue(size_bytes: int) -> str:
    """Uploads under the interactive threshold jump the bulk queue (spec §3.2)."""
    limit = get_settings().interactive_upload_mb * 1024 * 1024
    return "interactive" if size_bytes < limit else "default"


def build_ingest_chain(document_id: str, queue: str) -> Any:
    return chain(
        parse_task.si(document_id).set(queue=queue),
        chunk_task.si(document_id).set(queue=queue),
        embed_upsert_task.si(document_id).set(queue=queue),
    )


def enqueue_ingest(document_id: UUID, size_bytes: int) -> None:
    build_ingest_chain(str(document_id), select_queue(size_bytes)).apply_async()


def enqueue_delete(document_id: UUID, actor_id: UUID) -> None:
    delete_task.si(str(document_id), str(actor_id)).apply_async(queue="interactive")
```

Create empty `backend/src/openrag/worker/__init__.py` and `backend/tests/worker/__init__.py`.

- [ ] **Step 4: Run tests + gate**

Run: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS; import-linter reports the 4-layer contract kept.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests backend/pyproject.toml
git commit -m "feat: celery app with priority queues and thin ingest task wrappers"
```

---

### Task 13: API routes — documents (upload/list/delete) and workspace search

**Files:**
- Create: `backend/src/openrag/api/routes/documents.py`, `api/routes/search.py`, `backend/src/openrag/modules/retrieval/schemas.py`
- Modify: `backend/src/openrag/core/errors.py` (add `PayloadTooLarge`), `backend/src/openrag/api/app.py` (register routers)
- Test: `backend/tests/api/test_documents_routes.py`, `backend/tests/api/test_search_route.py`

**Interfaces:**
- Consumes: documents service (Task 9), `retrieve()` (Task 8), `enqueue_ingest`/`enqueue_delete` (Task 12), `DocumentOut` (Task 6).
- Produces: `PayloadTooLarge(OpenRAGError)` 413; routes `POST /api/v1/workspaces/{workspace_id}/documents` (multipart, 201 `DocumentOut`, size-capped, then enqueues the chain), `GET /api/v1/workspaces/{workspace_id}/documents` (200 `list[DocumentOut]` with status), `DELETE /api/v1/documents/{document_id}` (202 `{"status": "deletion scheduled"}`), **`POST /api/v1/workspaces/{workspace_id}/search`** `{query, top_k?}` → `SearchResponse` — a real Plan B product surface (documents-page search + smoke path before chat exists); schemas `SearchRequest(query: str, top_k: int = 8, 1 ≤ top_k ≤ 50)`, `ChunkOut(document_id, page, chunk_index, text, score)`, `SearchResponse(no_answer: bool, chunks: list[ChunkOut])`. All membership checks flow through `get_workspace_checked`/`get_document_checked` inside services — no inline checks in handlers.
- Tests monkeypatch `enqueue_ingest`/`enqueue_delete` at the route module (`openrag.api.routes.documents.*`) so API tests never need a broker; the real chain is exercised in Task 15 and the smoke.

- [ ] **Step 1: Write failing tests**

`backend/tests/api/test_documents_routes.py`:

```python
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User


@pytest.fixture
def captured_enqueues(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:  # type: ignore[type-arg]
    calls: dict[str, list] = {"ingest": [], "delete": []}  # type: ignore[type-arg]
    monkeypatch.setattr("openrag.api.routes.documents.enqueue_ingest",
                        lambda doc_id, size: calls["ingest"].append((doc_id, size)))
    monkeypatch.setattr("openrag.api.routes.documents.enqueue_delete",
                        lambda doc_id, actor_id: calls["delete"].append((doc_id, actor_id)))
    return calls


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def make_workspace(client: httpx.AsyncClient, h: dict[str, str]) -> str:
    r = await client.post("/api/v1/workspaces", json={"name": "Docs"}, headers=h)
    return str(r.json()["id"])


async def test_upload_list_delete_flow(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
    captured_enqueues: dict,  # type: ignore[type-arg]
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": ("notes.txt", b"the flux capacitor hums", "text/plain")},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "queued" and body["filename"] == "notes.txt"
    assert len(captured_enqueues["ingest"]) == 1

    listing = await client.get(f"/api/v1/workspaces/{ws_id}/documents", headers=h)
    assert [d["id"] for d in listing.json()] == [body["id"]]

    # duplicate content in the same workspace -> 409
    r2 = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": ("copy.txt", b"the flux capacitor hums", "text/plain")},
    )
    assert r2.status_code == 409

    r3 = await client.delete(f"/api/v1/documents/{body['id']}", headers=h)
    assert r3.status_code == 202
    assert len(captured_enqueues["delete"]) == 1


async def test_non_member_user_gets_403(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, stack_env: None,
    captured_enqueues: dict,  # type: ignore[type-arg]
) -> None:
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    h_user = await auth(client, "p@acme.com")
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h_user,
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert r.status_code == 403
    assert (await client.get(f"/api/v1/workspaces/{ws_id}/documents",
                             headers=h_user)).status_code == 403


async def test_delete_unknown_document_404(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
    captured_enqueues: dict,  # type: ignore[type-arg]
) -> None:
    h = await auth(client, "a@acme.com")
    r = await client.delete("/api/v1/documents/00000000-0000-0000-0000-000000000000",
                            headers=h)
    assert r.status_code == 404
    assert captured_enqueues["delete"] == []


async def test_oversized_upload_413(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
    monkeypatch: pytest.MonkeyPatch,
    captured_enqueues: dict,  # type: ignore[type-arg]
) -> None:
    from openrag.core.config import get_settings
    monkeypatch.setenv("OPENRAG_MAX_UPLOAD_MB", "0")
    get_settings.cache_clear()
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": ("big.txt", b"too big for zero", "text/plain")},
    )
    assert r.status_code == 413
    get_settings.cache_clear()
```

`backend/tests/api/test_search_route.py`:

```python
import httpx

from openrag.modules.auth.models import User
from tests.api.test_documents_routes import auth, make_workspace
from tests.modules.retrieval.test_retrieve import upsert_texts


async def test_search_empty_workspace_no_answer(
    client: httpx.AsyncClient, seeded_user: User, qdrant_collection: None
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.post(f"/api/v1/workspaces/{ws_id}/search",
                          json={"query": "anything"}, headers=h)
    assert r.status_code == 200
    assert r.json() == {"no_answer": True, "chunks": []}


async def test_search_returns_seeded_chunk(
    client: httpx.AsyncClient, seeded_user: User, session, qdrant_collection: None  # type: ignore[no-untyped-def]
) -> None:
    from openrag.modules.tenancy.context import TenantContext
    from openrag.modules.tenancy.models import Workspace
    from sqlalchemy import select

    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    ws = (await session.execute(select(Workspace))).scalar_one()
    ws.min_score = 0.0
    await session.commit()
    ctx = TenantContext(user_id=seeded_user.id, org_id=seeded_user.org_id,
                        role="admin", workspace_ids=frozenset())
    await upsert_texts(ctx, ws, ["invoice 0231 covers the plutonium delivery"])

    r = await client.post(f"/api/v1/workspaces/{ws_id}/search",
                          json={"query": "invoice 0231"}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["no_answer"] is False
    assert "invoice 0231" in body["chunks"][0]["text"]
    assert {"document_id", "page", "chunk_index", "text", "score"} <= set(body["chunks"][0])


async def test_search_requires_auth(client: httpx.AsyncClient, seeded_user: User) -> None:
    r = await client.post("/api/v1/workspaces/00000000-0000-0000-0000-000000000000/search",
                          json={"query": "x"})
    assert r.status_code == 401
```

Run: `uv run pytest tests/api/test_documents_routes.py tests/api/test_search_route.py -v`
Expected: FAIL — 404 (routers missing).

- [ ] **Step 2: Implement**

Append to `backend/src/openrag/core/errors.py`:

```python
class PayloadTooLarge(OpenRAGError):
    status_code = 413
    title = "Payload too large"
```

`backend/src/openrag/modules/retrieval/schemas.py`:

```python
from uuid import UUID

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=50)


class ChunkOut(BaseModel):
    document_id: UUID
    page: int
    chunk_index: int
    text: str
    score: float


class SearchResponse(BaseModel):
    no_answer: bool
    chunks: list[ChunkOut]
```

`backend/src/openrag/api/routes/documents.py`:

```python
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.config import get_settings
from openrag.core.errors import PayloadTooLarge
from openrag.modules.documents import service
from openrag.modules.documents.schemas import DocumentOut
from openrag.modules.tenancy.context import TenantContext, get_tenant_context
from openrag.worker.tasks import enqueue_delete, enqueue_ingest

router = APIRouter(tags=["documents"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]


@router.post("/workspaces/{workspace_id}/documents", status_code=201,
             response_model=DocumentOut)
async def upload_document(
    workspace_id: UUID, session: SessionDep, ctx: CtxDep,
    file: Annotated[UploadFile, File()],
) -> DocumentOut:
    data = await file.read()
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise PayloadTooLarge(f"file exceeds {get_settings().max_upload_mb} MB limit")
    doc = await service.create_from_upload(
        session, ctx, workspace_id,
        filename=file.filename or "upload.bin",
        mime=file.content_type or "application/octet-stream",
        data=data,
    )
    enqueue_ingest(doc.id, doc.size_bytes)
    return DocumentOut.model_validate(doc)


@router.get("/workspaces/{workspace_id}/documents", response_model=list[DocumentOut])
async def list_workspace_documents(
    workspace_id: UUID, session: SessionDep, ctx: CtxDep
) -> list[DocumentOut]:
    docs = await service.list_documents(session, ctx, workspace_id)
    return [DocumentOut.model_validate(d) for d in docs]


@router.delete("/documents/{document_id}", status_code=202)
async def delete_document(
    document_id: UUID, session: SessionDep, ctx: CtxDep
) -> dict[str, str]:
    doc = await service.get_document_checked(session, ctx, document_id)
    enqueue_delete(doc.id, ctx.user_id)
    return {"status": "deletion scheduled"}
```

`backend/src/openrag/api/routes/search.py`:

```python
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.modules.retrieval.schemas import ChunkOut, SearchRequest, SearchResponse
from openrag.modules.retrieval.service import retrieve
from openrag.modules.tenancy.context import TenantContext, get_tenant_context

router = APIRouter(tags=["search"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]


@router.post("/workspaces/{workspace_id}/search", response_model=SearchResponse)
async def search_workspace(
    workspace_id: UUID, body: SearchRequest, session: SessionDep, ctx: CtxDep
) -> SearchResponse:
    result = await retrieve(session, ctx, workspace_id, body.query, top_k=body.top_k)
    return SearchResponse(
        no_answer=result.no_answer,
        chunks=[
            ChunkOut(document_id=c.document_id, page=c.page, chunk_index=c.chunk_index,
                     text=c.text, score=c.score)
            for c in result.chunks
        ],
    )
```

Register in `backend/src/openrag/api/app.py`:

```python
from openrag.api.routes.documents import router as documents_router
from openrag.api.routes.search import router as search_router
```

```python
    app.include_router(documents_router, prefix="/api/v1")
    app.include_router(search_router, prefix="/api/v1")
```

- [ ] **Step 3: Run tests + gate**

Run: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat: document upload/list/delete and workspace search endpoints"
```

---

### Task 14: Adversarial tenant-isolation suite (iron rule 2)

**Files:**
- Create: `backend/tests/isolation/__init__.py`, `backend/tests/isolation/conftest.py`, `backend/tests/isolation/test_tenant_isolation.py`

**Interfaces:**
- Consumes: `retrieve`, `delete_document_points` via `run_delete`, `upsert_points` (the REAL production upsert path — not a test-only shortcut), seeding helpers, `stack_env`/`qdrant_collection` fixtures.
- Produces: the every-PR isolation suite. Seeding runs documents through the real service + embed/upsert stage functions with the deterministic hash dense backend + real BM25 sparse (TEI never required). Includes the **canary**: a direct unfiltered Qdrant query DOES see both orgs — so if `_tenant_filter` ever regresses, the leak tests fail loudly rather than passing vacuously.

- [ ] **Step 1: Write the suite (these tests must pass immediately — the code exists; any failure here is a real leak)**

`backend/tests/isolation/conftest.py`:

```python
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.documents.ingest import run_chunk, run_embed_upsert, run_parse
from openrag.modules.documents.models import Document
from openrag.modules.documents.service import create_from_upload
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace
from tests.modules.retrieval.test_retrieve import seed_workspace


async def ingest_text(
    session: AsyncSession, ctx: TenantContext, ws: Workspace, filename: str, text: str
) -> Document:
    """Seed via the REAL pipeline: upload service -> parse -> chunk -> embed+upsert."""
    doc = await create_from_upload(session, ctx, ws.id, filename=filename,
                                   mime="text/plain", data=text.encode())
    await run_parse(doc.id)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)
    await session.refresh(doc)
    assert doc.status == "indexed"
    return doc


@pytest.fixture
async def two_orgs(
    session: AsyncSession, qdrant_collection: None
) -> dict[str, tuple[TenantContext, Workspace, Document]]:
    ctx_a, ws_a = await seed_workspace(session, "isoA")
    ctx_b, ws_b = await seed_workspace(session, "isoB")
    doc_a = await ingest_text(session, ctx_a, ws_a, "a.txt",
                              "org alpha secret: the vault code is 7431")
    doc_b = await ingest_text(session, ctx_b, ws_b, "b.txt",
                              "org bravo secret: the vault code is 9962")
    return {"a": (ctx_a, ws_a, doc_a), "b": (ctx_b, ws_b, doc_b)}
```

`backend/tests/isolation/test_tenant_isolation.py`:

```python
"""Adversarial leak tests (iron rule 2). Run on every PR.

If any test here fails, treat it as a security incident, not a flake.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import WorkspaceAccessDenied
from openrag.modules.documents.ingest import run_delete
from openrag.modules.retrieval.client import COLLECTION, get_qdrant
from openrag.modules.retrieval.embeddings import get_dense_embedder
from openrag.modules.retrieval.service import retrieve


async def test_org_a_never_sees_org_b_chunks(
    session: AsyncSession, two_orgs: dict  # type: ignore[type-arg]
) -> None:
    ctx_a, ws_a, doc_a = two_orgs["a"]
    _, _, doc_b = two_orgs["b"]
    # Query A's workspace with B's exact secret text — the strongest lure possible.
    result = await retrieve(session, ctx_a, ws_a.id,
                            "org bravo secret: the vault code is 9962", top_k=10)
    returned_docs = {c.document_id for c in result.chunks}
    assert doc_b.id not in returned_docs
    assert all(d == doc_a.id for d in returned_docs)
    assert all("9962" not in c.text for c in result.chunks)


async def test_non_member_workspace_retrieval_denied(
    session: AsyncSession, two_orgs: dict  # type: ignore[type-arg]
) -> None:
    ctx_a, _, _ = two_orgs["a"]
    _, ws_b, _ = two_orgs["b"]
    with pytest.raises(WorkspaceAccessDenied):  # cross-org workspace id
        await retrieve(session, ctx_a, ws_b.id, "anything")


async def test_non_member_same_org_denied(
    session: AsyncSession, two_orgs: dict  # type: ignore[type-arg]
) -> None:
    from dataclasses import replace

    ctx_a, ws_a, _ = two_orgs["a"]
    stranger = replace(ctx_a, workspace_ids=frozenset())  # role "user", no membership
    with pytest.raises(WorkspaceAccessDenied):
        await retrieve(session, stranger, ws_a.id, "anything")


async def test_deleted_document_unretrievable(
    session: AsyncSession, two_orgs: dict  # type: ignore[type-arg]
) -> None:
    ctx_a, ws_a, doc_a = two_orgs["a"]
    before = await retrieve(session, ctx_a, ws_a.id, "vault code 7431")
    assert any(c.document_id == doc_a.id for c in before.chunks)
    await run_delete(doc_a.id, ctx_a.user_id)
    after = await retrieve(session, ctx_a, ws_a.id, "vault code 7431")
    assert all(c.document_id != doc_a.id for c in after.chunks)
    assert all("7431" not in c.text for c in after.chunks)


async def test_canary_unfiltered_query_sees_both_orgs(
    two_orgs: dict,  # type: ignore[type-arg]
) -> None:
    """Prove the data COULD leak without the filter — so the tests above are
    meaningful. This is the only sanctioned unfiltered query in the repo, and it
    lives in tests: production code must never do this (iron rule 1)."""
    ctx_a, *_ = two_orgs["a"]
    lure = (await get_dense_embedder().embed(["secret: the vault code is"]))[0]
    raw = await get_qdrant().query_points(COLLECTION, query=lure, using="dense",
                                          limit=10, with_payload=True)
    tenants = {str((p.payload or {})["tenant_id"]) for p in raw.points}
    assert len(tenants) == 2  # both orgs visible when the must-filter is absent
```

Run: `uv run pytest tests/isolation -v`
Expected: 5 PASSED. If any leak test fails, stop and fix `modules/retrieval/` — do not adjust the test.

- [ ] **Step 2: Full gate + commit**

Run: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS.

```bash
git add backend/tests/isolation
git commit -m "test: adversarial tenant isolation suite with unfiltered-query canary"
```

---

### Task 15: End-to-end ingestion integration test (real Docling, stubbed dense)

**Files:**
- Create: `backend/tests/integration/__init__.py`, `backend/tests/integration/test_ingestion_e2e.py`

**Interfaces:**
- Consumes: everything above. Real components: Docling (DOCX — no model downloads), BM25 sparse, Qdrant + MinIO + Postgres containers. Stubbed: dense embedder (`hash` backend).
- Scope note (per plan constraints): with the hash backend, dense cosine reduces to lexical overlap, so a pure "semantic paraphrase" assertion would be testing the stub, not the system. The retrieval assertions therefore cover (a) an exact-keyword query — dominated by real BM25 sparse — and (b) a partial-overlap query hitting the right chunk in top-5 through hybrid fusion. True semantic retrieval (TEI bge-m3) is validated by the real-stack smoke in the completion criteria.

- [ ] **Step 1: Write the test**

`backend/tests/integration/test_ingestion_e2e.py`:

```python
import io

import pytest
from docx import Document as DocxBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.documents.ingest import run_chunk, run_embed_upsert, run_parse
from openrag.modules.documents.models import IngestJob
from openrag.modules.documents.pipeline import IngestFailure
from openrag.modules.documents.service import create_from_upload
from openrag.modules.retrieval.service import retrieve
from tests.modules.retrieval.test_retrieve import seed_workspace


def fixture_docx() -> bytes:
    d = DocxBuilder()
    d.add_heading("Operations Manual", level=1)
    for i in range(30):
        d.add_paragraph(f"Routine operational paragraph number {i} about daily procedures.")
    d.add_heading("Power Requirements", level=1)
    d.add_paragraph("The flux capacitor requires exactly 1.21 gigawatts of power "
                    "supplied by the plutonium reactor.")
    d.add_heading("Billing", level=1)
    d.add_paragraph("Invoice 0231 was issued for the October plutonium delivery.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


async def test_docx_through_real_pipeline_then_hybrid_retrieval(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "e2e1")
    doc = await create_from_upload(session, ctx, ws.id, filename="manual.docx",
                                   mime="application/vnd.openxmlformats-officedocument"
                                        ".wordprocessingml.document",
                                   data=fixture_docx())
    await run_parse(doc.id)   # real Docling (DOCX: no model downloads)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)
    await session.refresh(doc)
    assert doc.status == "indexed" and (doc.page_count or 0) >= 1
    stages = {j.stage for j in (await session.execute(
        select(IngestJob).where(IngestJob.document_id == doc.id))).scalars()}
    assert stages == {"parse", "chunk", "embed", "upsert"}

    # (a) exact keyword query — real BM25 sparse must surface the billing chunk
    kw = await retrieve(session, ctx, ws.id, "invoice 0231", top_k=5)
    assert any("Invoice 0231" in c.text for c in kw.chunks[:5])

    # (b) partial-overlap query — hybrid fusion puts the power chunk in top-5
    ov = await retrieve(session, ctx, ws.id, "gigawatts flux capacitor power", top_k=5)
    hit = next(c for c in ov.chunks[:5] if "1.21 gigawatts" in c.text)
    assert hit.document_id == doc.id and hit.page >= 1 and hit.chunk_index >= 0


async def test_empty_and_unsupported_fail_cleanly(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "e2e2")
    empty = await create_from_upload(session, ctx, ws.id, filename="empty.txt",
                                     mime="text/plain", data=b"")
    with pytest.raises(IngestFailure, match="empty"):
        await run_parse(empty.id)
    await session.refresh(empty)
    assert empty.status == "failed" and "empty" in (empty.error or "")

    weird = await create_from_upload(session, ctx, ws.id, filename="blob.xyz",
                                     mime="application/octet-stream", data=b"\x00\x01")
    with pytest.raises(IngestFailure):
        await run_parse(weird.id)
    await session.refresh(weird)
    assert weird.status == "failed" and weird.error
```

Run: `uv run pytest tests/integration -v`
Expected: 2 PASSED (docling import makes this the slowest test file; still no model downloads).

- [ ] **Step 2: Full gate + commit**

Run: `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS.

```bash
git add backend/tests/integration
git commit -m "test: end-to-end ingestion and hybrid retrieval integration"
```

---

## Plan B Completion Criteria

1. **Full gate green:** `uv run lint-imports && uv run pytest tests -v && uv run ruff check . && uv run mypy src` — all tests pass (unit + integration + `tests/isolation/`) against real Postgres/Redis/Qdrant/MinIO containers, with **no TEI and no Docling/PDF model downloads**.
2. **Iron rule 1 audit:** `grep -rn "models.Filter\|FieldCondition" backend/src/openrag --include="*.py" | grep -v "modules/retrieval/service.py"` returns nothing.
3. **Real-stack smoke** (repo root; validates TEI dense + PDF parsing, the two paths deliberately excluded from the test suite):

```bash
docker compose -f deploy/compose.yaml --profile ml up -d          # first TEI start downloads bge-m3 (~2.3 GB)
cd backend && uv run alembic upgrade head
OPENRAG_BOOTSTRAP_EMAIL=root@openrag.internal OPENRAG_BOOTSTRAP_PASSWORD=changeme123 \
  uv run python -m openrag.bootstrap
uv run uvicorn --factory openrag.api.app:create_app --port 8000 &
uv run celery -A openrag.worker.celery_app:celery_app worker -Q interactive,default -l info &

TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"root@openrag.internal","password":"changeme123"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
WS=$(curl -s -X POST localhost:8000/api/v1/workspaces -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"name":"Smoke"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -s -X POST "localhost:8000/api/v1/workspaces/$WS/documents" \
  -H "authorization: Bearer $TOKEN" -F "file=@/path/to/any/real.pdf"
# poll until "status":"indexed" (first PDF also downloads Docling layout models in the worker):
curl -s "localhost:8000/api/v1/workspaces/$WS/documents" -H "authorization: Bearer $TOKEN"
# retrieval through the real product route — expect chunks[] with text/page/chunk_index and no_answer=false
curl -s -X POST "localhost:8000/api/v1/workspaces/$WS/search" -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"query":"<a phrase that appears in the pdf>"}'
```

   Then delete the document via `curl -X DELETE .../api/v1/documents/<id>` → 202; re-run the search → the deleted document's chunks are gone. Stop uvicorn/celery afterward.
4. **Interfaces frozen for Plan C:** `retrieve(session, ctx, workspace_id, query, top_k=8) -> RetrievalResult` with `RetrievedChunk(document_id, page, chunk_index, text, score)` (citation-ready); `app.state.redis` + `rate_limit()`; the catch-all handler; `POST /workspaces/{id}/search` remains a product route.

---

## Self-review (performed while authoring)

- **Spec §2.2:** collection `chunks_bge_m3`, named vectors `dense` (1024-d cosine) + `sparse` (BM25/IDF), payload incl. `acl_groups: []` reserved, keyword indexes on tenant/workspace/document — Task 8/10. **§2.3:** storage key `{org_id}/{workspace_id}/{document_id}/{filename}`, one-task deletion propagation with audit — Tasks 9/11. **§3.2:** 4 stages with per-stage `ingest_jobs`, 3× backoff, terminal failure reason, priority queue for <10 MB, dedup short-circuit — Tasks 6/11/12. **§3.3:** the one code path with membership assert, hybrid RRF, must-filters, min_score no-answer — Task 8. **§6:** isolation + integration suites — Tasks 14/15.
- **Consistency with merged code verified:** `get_session` in `core/db.py`, `TenantContext(user_id, org_id, role, workspace_ids)`, `record_audit(session, *, org_id, actor_id, action, target_type, target_id)`, `OpenRAGError` subclasses incl. existing `RateLimitExceeded`, `UUIDPk` naive-UTC default, compose 127.0.0.1 port style, `uv run lint-imports` gate.
- **Placeholder scan:** no TBDs; every code block is complete and importable as written.
