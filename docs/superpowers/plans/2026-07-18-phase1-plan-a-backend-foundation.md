# OpenRAG Phase 1 — Plan A: Backend Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tested FastAPI backend with auth (login/JWT/refresh rotation/invitations), tenancy (orgs/workspaces/roles/TenantContext), audit write-path, and a Docker Compose dev stack.

**Architecture:** Modular monolith per the foundation spec — `api → modules → core`, boundaries enforced by import-linter. All org-owned queries flow through `TenantContext`. This plan is 1 of 4 (B: ingestion+retrieval, C: models+secrets+chat, D: frontend); it delivers a working, testable API on its own.

**Tech Stack:** Python 3.12, uv, FastAPI (async), SQLAlchemy 2.0 async + asyncpg, Alembic, Pydantic v2, argon2-cffi, PyJWT, structlog, pytest + pytest-asyncio + testcontainers, ruff, mypy --strict, import-linter.

## Global Constraints

- Specs: `docs/superpowers/specs/2026-07-18-openrag-phase1-design.md` (scope), `...-openrag-engineering-foundation-design.md` (iron rules). Re-read the Five Iron Rules before starting.
- `.env` holds bootstrap config only (DB/Redis URLs, environment). No secrets beyond that — the JWT signing key is generated at first startup and stored in the `app_settings` DB table, never in `.env`.
- Access JWT lifetime 900s; refresh tokens rotate, reuse of a rotated token revokes the whole family.
- Errors: typed module exceptions → one global RFC 9457 `application/problem+json` handler. No bare `except:`. Error responses never leak internals.
- Routers accept/return Pydantic schemas only — never ORM objects. No inline `if user.role ==` in handlers; role checks are dependencies.
- Logging: structlog JSON; keys matching `password|secret|token|key` are redacted.
- All commands below run from `backend/` unless stated. Conventional Commits. `uv run ruff check . && uv run mypy src` must pass before every commit.
- Integration tests use testcontainers (real Postgres) — never mock the DB.

---

### Task 1: Backend scaffold and tooling

**Files:**
- Create: `backend/pyproject.toml`, `backend/src/openrag/__init__.py`, `backend/src/openrag/{core,modules,api}/__init__.py`, `backend/src/openrag/modules/{auth,tenancy,audit}/__init__.py`, `backend/tests/__init__.py`, `backend/tests/test_scaffold.py`

- [ ] **Step 1: Create `backend/pyproject.toml`**

```toml
[project]
name = "openrag"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.30",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "argon2-cffi>=23.1",
    "pyjwt>=2.8",
    "structlog>=24.1",
]

[dependency-groups]
dev = [
    "pytest>=8.2",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
    "testcontainers[postgres]>=4.5",
    "ruff>=0.4",
    "mypy>=1.10",
    "import-linter>=2.0",
    "psycopg2-binary>=2.9",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/openrag"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "S"]
ignore = ["S101"]  # allow assert in tests

[tool.mypy]
strict = true
mypy_path = "src"
packages = ["openrag"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.importlinter]
root_package = "openrag"

[[tool.importlinter.contracts]]
name = "Layered architecture"
type = "layers"
layers = ["openrag.api", "openrag.modules", "openrag.core"]
```

- [ ] **Step 2: Create package skeleton and a scaffold test**

Create empty `__init__.py` files at every path listed above. Then `backend/tests/test_scaffold.py`:

```python
import openrag


def test_package_importable() -> None:
    assert openrag is not None
```

- [ ] **Step 3: Install and run**

Run: `cd backend && uv sync && uv run pytest -v`
Expected: `test_package_importable PASSED`

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add backend/
git commit -m "feat: scaffold backend package with uv, ruff, mypy, pytest"
```

---

### Task 2: Docker Compose dev stack

**Files:**
- Create: `deploy/compose.yaml`, `.env.example`

- [ ] **Step 1: Create `deploy/compose.yaml`**

```yaml
name: openrag
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: openrag
      POSTGRES_PASSWORD: openrag
      POSTGRES_DB: openrag
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U openrag"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  pgdata:
```

(Qdrant, MinIO, TEI, LiteLLM are added by Plans B and C.)

- [ ] **Step 2: Create `.env.example`** (repo root)

```bash
# Bootstrap config only — never put secrets here (foundation iron rule 3)
OPENRAG_DATABASE_URL=postgresql+asyncpg://openrag:openrag@localhost:5432/openrag
OPENRAG_REDIS_URL=redis://localhost:6379/0
OPENRAG_ENVIRONMENT=dev
```

- [ ] **Step 3: Verify the stack**

Run: `docker compose -f deploy/compose.yaml up -d && docker compose -f deploy/compose.yaml ps`
Expected: `postgres` and `redis` both `Up (healthy)` within ~15s.

- [ ] **Step 4: Commit**

```bash
git add deploy/compose.yaml .env.example
git commit -m "feat: add docker compose dev stack (postgres, redis)"
```

---

### Task 3: core — config, logging, errors

**Files:**
- Create: `backend/src/openrag/core/config.py`, `core/logging.py`, `core/errors.py`
- Test: `backend/tests/core/test_config.py`, `tests/core/test_errors.py`, `tests/core/test_logging.py` (+ `tests/core/__init__.py`)

**Interfaces:**
- Produces: `get_settings() -> Settings` (fields: `database_url: str`, `redis_url: str`, `environment: str`, `access_token_ttl_seconds: int = 900`, `refresh_token_ttl_seconds: int = 1209600`); `configure_logging() -> None`; exception hierarchy `OpenRAGError(detail)` with subclasses `AuthenticationError(401)`, `AuthorizationError(403)`, `NotFoundError(404)`, `ConflictError(409)`; `problem_json_handler(request, exc) -> JSONResponse`.

- [ ] **Step 1: Write failing tests**

`backend/tests/core/test_config.py`:

```python
from openrag.core.config import Settings


def test_settings_reads_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENRAG_DATABASE_URL", "postgresql+asyncpg://x:y@h:5432/db")
    s = Settings(_env_file=None)
    assert s.database_url.endswith("/db")
    assert s.access_token_ttl_seconds == 900
```

`backend/tests/core/test_errors.py`:

```python
from openrag.core.errors import AuthenticationError, NotFoundError, OpenRAGError


def test_error_hierarchy() -> None:
    err = NotFoundError("document missing")
    assert isinstance(err, OpenRAGError)
    assert err.status_code == 404
    assert err.detail == "document missing"
    assert AuthenticationError("").status_code == 401
```

`backend/tests/core/test_logging.py`:

```python
import structlog

from openrag.core.logging import configure_logging, redact_sensitive


def test_redaction_processor() -> None:
    event = redact_sensitive(None, "", {"password": "hunter2", "api_key": "sk-123", "msg": "ok"})
    assert event["password"] == "[REDACTED]"
    assert event["api_key"] == "[REDACTED]"
    assert event["msg"] == "ok"


def test_configure_logging_idempotent() -> None:
    configure_logging()
    configure_logging()
    assert structlog.is_configured()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'openrag.core.config'`

- [ ] **Step 3: Implement**

`backend/src/openrag/core/config.py`:

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENRAG_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://openrag:openrag@localhost:5432/openrag"
    redis_url: str = "redis://localhost:6379/0"
    environment: str = "dev"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1209600  # 14 days


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`backend/src/openrag/core/errors.py`:

```python
class OpenRAGError(Exception):
    """Base for all typed application errors. Mapped to RFC 9457 responses."""

    status_code: int = 500
    title: str = "Internal error"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail)


class AuthenticationError(OpenRAGError):
    status_code = 401
    title = "Authentication failed"


class AuthorizationError(OpenRAGError):
    status_code = 403
    title = "Not permitted"


class NotFoundError(OpenRAGError):
    status_code = 404
    title = "Not found"


class ConflictError(OpenRAGError):
    status_code = 409
    title = "Conflict"
```

`backend/src/openrag/core/logging.py`:

```python
import logging
import re
from typing import Any

import structlog

_SENSITIVE = re.compile(r"password|secret|token|api_key|key$", re.IGNORECASE)


def redact_sensitive(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    for k in list(event_dict):
        if _SENSITIVE.search(k):
            event_dict[k] = "[REDACTED]"
    return event_dict


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_sensitive,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core -v && uv run ruff check . && uv run mypy src`
Expected: 4 PASSED, lint/type clean.

- [ ] **Step 5: Commit**

```bash
git add backend/src/openrag/core backend/tests/core
git commit -m "feat: core config, structlog with redaction, typed error hierarchy"
```

---

### Task 4: core — async database and Alembic

**Files:**
- Create: `backend/src/openrag/core/db.py`, `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako` (alembic init), `backend/tests/conftest.py`
- Test: `backend/tests/core/test_db.py`

**Interfaces:**
- Produces: `Base` (DeclarativeBase), `build_engine(url: str) -> AsyncEngine`, `build_session_factory(engine) -> async_sessionmaker[AsyncSession]`, FastAPI dependency `get_session() -> AsyncIterator[AsyncSession]` (created in Task 8's app wiring). Test fixtures: `pg_url` (session-scoped container), `engine`, `session` (function-scoped, rolled back).

- [ ] **Step 1: Implement `backend/src/openrag/core/db.py`**

```python
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UUIDPk:
    """Mixin: uuid4 primary key + created_at."""

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC)
    )


def build_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 2: Initialize Alembic (async template)**

Run: `uv run alembic init -t async migrations`
Then edit `backend/alembic.ini`: set `sqlalchemy.url =` (leave empty). Edit `backend/migrations/env.py` — replace the config/url/metadata sections with:

```python
from openrag.core.config import get_settings
from openrag.core.db import Base
import openrag.modules.auth.models  # noqa: F401  (registered as tasks add them)
import openrag.modules.tenancy.models  # noqa: F401

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata
```

(The two model imports will fail until Task 5 creates them — add each import in the task that creates the module's models. For now comment both out.)

- [ ] **Step 3: Write test fixtures and failing test**

`backend/tests/conftest.py`:

```python
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from testcontainers.postgres import PostgresContainer

from openrag.core.db import Base, build_engine, build_session_factory


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = build_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = build_session_factory(engine)
    async with factory() as s:
        yield s
```

`backend/tests/core/test_db.py`:

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def test_roundtrip(session: AsyncSession) -> None:
    result = await session.execute(text("SELECT 1"))
    assert result.scalar() == 1
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/core/test_db.py -v`
Expected: PASS (container pulls on first run — allow a minute).

- [ ] **Step 5: Commit**

```bash
git add backend/src/openrag/core/db.py backend/alembic.ini backend/migrations backend/tests
git commit -m "feat: async SQLAlchemy base, engine factory, alembic init, pg test fixtures"
```

---

### Task 5: Organization and User models + first migration

**Files:**
- Create: `backend/src/openrag/modules/tenancy/models.py`, `backend/src/openrag/modules/auth/models.py`
- Modify: `backend/migrations/env.py` (uncomment the two model imports)
- Test: `backend/tests/modules/tenancy/test_models.py` (+ `__init__.py` chain)

**Interfaces:**
- Produces: `Organization(id, name, created_at)`; `User(id, org_id, email [globally unique], password_hash, role: str in {"superadmin","admin","user"}, active: bool, created_at)`.

- [ ] **Step 1: Write failing test**

`backend/tests/modules/tenancy/test_models.py`:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.tenancy.models import Organization


async def test_create_org_and_user(session: AsyncSession) -> None:
    org = Organization(name="Acme")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email="a@acme.com", password_hash="x", role="admin")
    session.add(user)
    await session.commit()

    found = (await session.execute(select(User).where(User.email == "a@acme.com"))).scalar_one()
    assert found.org_id == org.id
    assert found.active is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/modules -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement models**

`backend/src/openrag/modules/tenancy/models.py`:

```python
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class Organization(UUIDPk, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(unique=True)
```

`backend/src/openrag/modules/auth/models.py`:

```python
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class User(UUIDPk, Base):
    __tablename__ = "users"

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    email: Mapped[str] = mapped_column(unique=True, index=True)
    password_hash: Mapped[str]
    role: Mapped[str]  # superadmin | admin | user
    active: Mapped[bool] = mapped_column(default=True)
```

Uncomment both model imports in `backend/migrations/env.py`.

- [ ] **Step 4: Run test, generate + apply migration**

Run: `uv run pytest tests/modules -v`
Expected: PASS.

Run (compose stack up): `uv run alembic revision --autogenerate -m "orgs and users" && uv run alembic upgrade head`
Expected: generated file contains `create_table("organizations"...)` and `create_table("users"...)`; upgrade succeeds.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests backend/migrations
git commit -m "feat: organization and user models with initial migration"
```

---

### Task 6: Password hashing, DB-stored signing key, JWT service

**Files:**
- Create: `backend/src/openrag/modules/auth/passwords.py`, `auth/tokens.py`, `backend/src/openrag/core/app_settings.py`
- Test: `backend/tests/modules/auth/test_passwords.py`, `test_tokens.py`

**Interfaces:**
- Produces: `hash_password(raw: str) -> str`; `verify_password(hashed: str, raw: str) -> bool`; `AppSetting` model (`app_settings(key: str pk, value: str)`); `get_or_create_signing_key(session: AsyncSession) -> str`; `issue_access_token(*, user_id: UUID, org_id: UUID, role: str, signing_key: str, ttl_seconds: int) -> str`; `AccessClaims(user_id: UUID, org_id: UUID, role: str)`; `decode_access_token(token: str, signing_key: str) -> AccessClaims` (raises `AuthenticationError`).

- [ ] **Step 1: Write failing tests**

`backend/tests/modules/auth/test_passwords.py`:

```python
from openrag.modules.auth.passwords import hash_password, verify_password


def test_hash_and_verify() -> None:
    h = hash_password("s3cret!")
    assert h != "s3cret!"
    assert h.startswith("$argon2id$")
    assert verify_password(h, "s3cret!")
    assert not verify_password(h, "wrong")
```

`backend/tests/modules/auth/test_tokens.py`:

```python
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.app_settings import get_or_create_signing_key
from openrag.core.errors import AuthenticationError
from openrag.modules.auth.tokens import decode_access_token, issue_access_token


async def test_signing_key_persisted(session: AsyncSession) -> None:
    k1 = await get_or_create_signing_key(session)
    k2 = await get_or_create_signing_key(session)
    assert k1 == k2 and len(k1) >= 43  # 32 bytes urlsafe


def test_token_roundtrip() -> None:
    uid, oid = uuid4(), uuid4()
    tok = issue_access_token(user_id=uid, org_id=oid, role="admin", signing_key="k" * 43, ttl_seconds=900)
    claims = decode_access_token(tok, "k" * 43)
    assert claims.user_id == uid and claims.org_id == oid and claims.role == "admin"


def test_bad_signature_rejected() -> None:
    tok = issue_access_token(user_id=uuid4(), org_id=uuid4(), role="user", signing_key="k" * 43, ttl_seconds=900)
    with pytest.raises(AuthenticationError):
        decode_access_token(tok, "x" * 43)


def test_expired_rejected() -> None:
    tok = issue_access_token(user_id=uuid4(), org_id=uuid4(), role="user", signing_key="k" * 43, ttl_seconds=-1)
    with pytest.raises(AuthenticationError):
        decode_access_token(tok, "k" * 43)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/modules/auth -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement**

`backend/src/openrag/modules/auth/passwords.py`:

```python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()  # argon2id defaults


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(hashed: str, raw: str) -> bool:
    try:
        return _hasher.verify(hashed, raw)
    except VerifyMismatchError:
        return False
```

`backend/src/openrag/core/app_settings.py`:

```python
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base

SIGNING_KEY_NAME = "jwt_signing_key"


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]


async def get_or_create_signing_key(session: AsyncSession) -> str:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == SIGNING_KEY_NAME))
    ).scalar_one_or_none()
    if row is None:
        row = AppSetting(key=SIGNING_KEY_NAME, value=secrets.token_urlsafe(32))
        session.add(row)
        await session.commit()
    return row.value
```

`backend/src/openrag/modules/auth/tokens.py`:

```python
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from openrag.core.errors import AuthenticationError

_ALG = "HS256"


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    org_id: UUID
    role: str


def issue_access_token(
    *, user_id: UUID, org_id: UUID, role: str, signing_key: str, ttl_seconds: int
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, signing_key, algorithm=_ALG)


def decode_access_token(token: str, signing_key: str) -> AccessClaims:
    try:
        payload = jwt.decode(token, signing_key, algorithms=[_ALG])
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("invalid or expired token") from exc
    return AccessClaims(
        user_id=UUID(payload["sub"]), org_id=UUID(payload["org"]), role=payload["role"]
    )
```

Add `import openrag.core.app_settings  # noqa: F401` to `migrations/env.py`, then:
Run: `uv run alembic revision --autogenerate -m "app settings" && uv run alembic upgrade head`

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/modules/auth -v && uv run mypy src`
Expected: 5 PASSED, types clean.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests backend/migrations
git commit -m "feat: argon2id hashing, DB-stored signing key, JWT access tokens"
```

---

### Task 7: Refresh tokens with rotation + auth service

**Files:**
- Create: `backend/src/openrag/modules/auth/service.py`
- Modify: `backend/src/openrag/modules/auth/models.py` (add `RefreshToken`), `migrations/env.py` already imports auth models
- Test: `backend/tests/modules/auth/test_service.py`

**Interfaces:**
- Produces: `RefreshToken(id, user_id, family_id, token_hash, expires_at, revoked_at)`; `TokenPair(access_token: str, refresh_token: str)`; `async login(session, *, email, password, settings) -> TokenPair` (raises `AuthenticationError` for unknown email, wrong password, or inactive user); `async rotate_refresh(session, *, raw_refresh, settings) -> TokenPair` (reuse of rotated token revokes family); `async logout(session, *, raw_refresh) -> None`.

- [ ] **Step 1: Add `RefreshToken` to `auth/models.py`**

```python
from datetime import datetime  # add to imports


class RefreshToken(UUIDPk, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    family_id: Mapped[UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
```

Run: `uv run alembic revision --autogenerate -m "refresh tokens" && uv run alembic upgrade head`

- [ ] **Step 2: Write failing tests**

`backend/tests/modules/auth/test_service.py`:

```python
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.core.errors import AuthenticationError
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.auth.service import login, logout, rotate_refresh
from openrag.modules.tenancy.models import Organization

SETTINGS = Settings(_env_file=None)


async def make_user(session: AsyncSession, email: str = "u@acme.com") -> User:
    org = Organization(name=f"org-{email}")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email=email, password_hash=hash_password("pw123456"), role="user")
    session.add(user)
    await session.commit()
    return user


async def test_login_returns_pair(session: AsyncSession) -> None:
    await make_user(session)
    pair = await login(session, email="u@acme.com", password="pw123456", settings=SETTINGS)
    assert pair.access_token and pair.refresh_token


async def test_login_wrong_password(session: AsyncSession) -> None:
    await make_user(session)
    with pytest.raises(AuthenticationError):
        await login(session, email="u@acme.com", password="nope", settings=SETTINGS)


async def test_rotation_and_reuse_revokes_family(session: AsyncSession) -> None:
    await make_user(session)
    pair1 = await login(session, email="u@acme.com", password="pw123456", settings=SETTINGS)
    pair2 = await rotate_refresh(session, raw_refresh=pair1.refresh_token, settings=SETTINGS)
    assert pair2.refresh_token != pair1.refresh_token
    # reusing the rotated (old) token is an attack signal -> whole family dies
    with pytest.raises(AuthenticationError):
        await rotate_refresh(session, raw_refresh=pair1.refresh_token, settings=SETTINGS)
    with pytest.raises(AuthenticationError):
        await rotate_refresh(session, raw_refresh=pair2.refresh_token, settings=SETTINGS)


async def test_logout_revokes(session: AsyncSession) -> None:
    await make_user(session)
    pair = await login(session, email="u@acme.com", password="pw123456", settings=SETTINGS)
    await logout(session, raw_refresh=pair.refresh_token)
    with pytest.raises(AuthenticationError):
        await rotate_refresh(session, raw_refresh=pair.refresh_token, settings=SETTINGS)
```

Run: `uv run pytest tests/modules/auth/test_service.py -v`
Expected: FAIL — `service` missing.

- [ ] **Step 3: Implement `backend/src/openrag/modules/auth/service.py`**

```python
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.app_settings import get_or_create_signing_key
from openrag.core.config import Settings
from openrag.core.errors import AuthenticationError
from openrag.modules.auth.models import RefreshToken, User
from openrag.modules.auth.passwords import verify_password
from openrag.modules.auth.tokens import issue_access_token


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _issue_pair(session: AsyncSession, user: User, family_id: object, settings: Settings) -> TokenPair:
    signing_key = await get_or_create_signing_key(session)
    raw_refresh = secrets.token_urlsafe(48)
    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id,
            token_hash=_hash(raw_refresh),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
    )
    await session.commit()
    access = issue_access_token(
        user_id=user.id, org_id=user.org_id, role=user.role,
        signing_key=signing_key, ttl_seconds=settings.access_token_ttl_seconds,
    )
    return TokenPair(access_token=access, refresh_token=raw_refresh)


async def login(session: AsyncSession, *, email: str, password: str, settings: Settings) -> TokenPair:
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not user.active or not verify_password(user.password_hash, password):
        raise AuthenticationError("invalid credentials")
    return await _issue_pair(session, user, uuid4(), settings)


async def rotate_refresh(session: AsyncSession, *, raw_refresh: str, settings: Settings) -> TokenPair:
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == _hash(raw_refresh)))
    ).scalar_one_or_none()
    if row is None:
        raise AuthenticationError("unknown refresh token")
    now = datetime.now(UTC)
    if row.revoked_at is not None:
        # Reuse of a rotated token: revoke the entire family.
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id)
            .values(revoked_at=now)
        )
        await session.commit()
        raise AuthenticationError("refresh token reuse detected")
    if row.expires_at.replace(tzinfo=UTC) < now:
        raise AuthenticationError("refresh token expired")
    row.revoked_at = now
    user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one()
    if not user.active:
        raise AuthenticationError("user inactive")
    return await _issue_pair(session, user, row.family_id, settings)


async def logout(session: AsyncSession, *, raw_refresh: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == _hash(raw_refresh))
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/modules/auth -v && uv run ruff check . && uv run mypy src`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests backend/migrations
git commit -m "feat: login, refresh rotation with family revocation, logout"
```

---

### Task 8: FastAPI app factory + auth endpoints

**Files:**
- Create: `backend/src/openrag/api/app.py`, `api/deps.py`, `api/routes/auth.py` (+ `api/routes/__init__.py`), `backend/src/openrag/modules/auth/schemas.py`
- Test: `backend/tests/api/test_auth_routes.py` (+ `tests/api/__init__.py`)

**Interfaces:**
- Consumes: `login/rotate_refresh/logout` (Task 7), errors (Task 3).
- Produces: `create_app(session_factory=None) -> FastAPI` (wires problem+json handler, routers, `configure_logging`); dependency `get_session()` in `api/deps.py`; routes `POST /api/v1/auth/login {email,password} -> {access_token}` + httpOnly `refresh_token` cookie; `POST /api/v1/auth/refresh` (cookie) -> new pair; `POST /api/v1/auth/logout`. Test fixture `client(engine) -> httpx.AsyncClient`.

- [ ] **Step 1: Write failing tests**

`backend/tests/api/test_auth_routes.py`:

```python
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from openrag.api.app import create_app
from openrag.core.db import build_session_factory
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.tenancy.models import Organization


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(session_factory=build_session_factory(engine))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def seeded_user(session: AsyncSession) -> User:
    org = Organization(name="Acme")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email="a@acme.com",
                password_hash=hash_password("pw123456"), role="admin")
    session.add(user)
    await session.commit()
    return user


async def test_login_ok(client: httpx.AsyncClient, seeded_user: User) -> None:
    r = await client.post("/api/v1/auth/login", json={"email": "a@acme.com", "password": "pw123456"})
    assert r.status_code == 200
    assert r.json()["access_token"]
    assert "refresh_token" in r.cookies


async def test_login_bad_password_problem_json(client: httpx.AsyncClient, seeded_user: User) -> None:
    r = await client.post("/api/v1/auth/login", json={"email": "a@acme.com", "password": "bad"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["title"] == "Authentication failed"


async def test_refresh_and_logout(client: httpx.AsyncClient, seeded_user: User) -> None:
    r = await client.post("/api/v1/auth/login", json={"email": "a@acme.com", "password": "pw123456"})
    r2 = await client.post("/api/v1/auth/refresh")
    assert r2.status_code == 200
    assert r2.cookies["refresh_token"] != r.cookies["refresh_token"]
    r3 = await client.post("/api/v1/auth/logout")
    assert r3.status_code == 204
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401
```

Run: `uv run pytest tests/api -v` — Expected: FAIL.

- [ ] **Step 2: Implement schemas and deps**

`backend/src/openrag/modules/auth/schemas.py`:

```python
from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
```

(Add `email-validator>=2.1` to `pyproject.toml` dependencies; run `uv sync`.)

`backend/src/openrag/api/deps.py`:

```python
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session
```

- [ ] **Step 3: Implement app factory and auth routes**

`backend/src/openrag/api/app.py`:

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.api.routes.auth import router as auth_router
from openrag.core.config import get_settings
from openrag.core.db import build_engine, build_session_factory
from openrag.core.errors import OpenRAGError
from openrag.core.logging import configure_logging


def create_app(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    configure_logging()
    app = FastAPI(title="OpenRAG", docs_url="/api/docs", openapi_url="/api/openapi.json")
    if session_factory is None:
        session_factory = build_session_factory(build_engine(get_settings().database_url))
    app.state.session_factory = session_factory

    @app.exception_handler(OpenRAGError)
    async def handle_openrag_error(request: Request, exc: OpenRAGError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": "about:blank",
                "title": exc.title,
                "status": exc.status_code,
                "detail": exc.detail,
            },
            media_type="application/problem+json",
        )

    app.include_router(auth_router, prefix="/api/v1")
    return app
```

`backend/src/openrag/api/routes/auth.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.config import Settings, get_settings
from openrag.core.errors import AuthenticationError
from openrag.modules.auth import service
from openrag.modules.auth.schemas import AccessTokenResponse, LoginRequest

router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
RefreshCookie = Annotated[str | None, Cookie(alias="refresh_token")]


def _set_refresh(response: Response, raw: str, settings: Settings) -> None:
    response.set_cookie(
        "refresh_token", raw, httponly=True, samesite="strict",
        secure=settings.environment != "dev",
        max_age=settings.refresh_token_ttl_seconds, path="/api/v1/auth",
    )


@router.post("/login", response_model=AccessTokenResponse)
async def login(
    body: LoginRequest, response: Response, session: SessionDep, settings: SettingsDep
) -> AccessTokenResponse:
    pair = await service.login(session, email=body.email, password=body.password, settings=settings)
    _set_refresh(response, pair.refresh_token, settings)
    return AccessTokenResponse(access_token=pair.access_token)


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(
    response: Response, session: SessionDep, settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> AccessTokenResponse:
    if not refresh_token:
        raise AuthenticationError("missing refresh token")
    pair = await service.rotate_refresh(session, raw_refresh=refresh_token, settings=settings)
    _set_refresh(response, pair.refresh_token, settings)
    return AccessTokenResponse(access_token=pair.access_token)


@router.post("/logout", status_code=204)
async def logout(
    response: Response, session: SessionDep, refresh_token: RefreshCookie = None
) -> None:
    if refresh_token:
        await service.logout(session, raw_refresh=refresh_token)
    response.delete_cookie("refresh_token", path="/api/v1/auth")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/api -v && uv run ruff check . && uv run mypy src`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests backend/pyproject.toml backend/uv.lock
git commit -m "feat: FastAPI app factory, problem+json handler, auth endpoints"
```

---

### Task 9: TenantContext dependency and role guards

**Files:**
- Create: `backend/src/openrag/modules/tenancy/context.py`
- Modify: `backend/src/openrag/api/deps.py`
- Test: `backend/tests/api/test_tenant_context.py`

**Interfaces:**
- Produces: `TenantContext(user_id: UUID, org_id: UUID, role: str, workspace_ids: frozenset[UUID])`; FastAPI dependency `get_tenant_context` (validates bearer JWT, loads active user + workspace memberships; 401 on bad/missing token or inactive user); `require_role(*roles: str)` returning a dependency that raises `AuthorizationError` (superadmin always passes).

- [ ] **Step 1: Write failing tests**

`backend/tests/api/test_tenant_context.py`:

```python
import httpx
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.tenancy.context import TenantContext, get_tenant_context, require_role


def wire_probe(app: FastAPI) -> None:
    @app.get("/probe/me")
    async def me(ctx: TenantContext = Depends(get_tenant_context)) -> dict[str, str]:
        return {"role": ctx.role, "org_id": str(ctx.org_id)}

    @app.get("/probe/admin", dependencies=[Depends(require_role("admin"))])
    async def admin_only() -> dict[str, bool]:
        return {"ok": True}


async def login_token(client: httpx.AsyncClient, email: str, pw: str = "pw123456") -> str:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    return str(r.json()["access_token"])


async def test_me_requires_token(client: httpx.AsyncClient, seeded_user: User) -> None:
    wire_probe(client._transport.app)  # type: ignore[attr-defined]
    assert (await client.get("/probe/me")).status_code == 401
    tok = await login_token(client, "a@acme.com")
    r = await client.get("/probe/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["role"] == "admin"


async def test_role_guard(client: httpx.AsyncClient, seeded_user: User, session: AsyncSession) -> None:
    wire_probe(client._transport.app)  # type: ignore[attr-defined]
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()
    admin_tok = await login_token(client, "a@acme.com")
    user_tok = await login_token(client, "p@acme.com")
    assert (await client.get("/probe/admin", headers={"Authorization": f"Bearer {admin_tok}"})).status_code == 200
    assert (await client.get("/probe/admin", headers={"Authorization": f"Bearer {user_tok}"})).status_code == 403
```

Move the `client` and `seeded_user` fixtures from `tests/api/test_auth_routes.py` into `tests/conftest.py` so both test files share them (delete the originals).

Run: `uv run pytest tests/api -v` — Expected: new tests FAIL (`context` missing).

- [ ] **Step 2: Implement `backend/src/openrag/modules/tenancy/context.py`**

```python
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.app_settings import get_or_create_signing_key
from openrag.core.errors import AuthenticationError, AuthorizationError
from openrag.modules.auth.models import User
from openrag.modules.auth.tokens import decode_access_token
from openrag.modules.tenancy.models import WorkspaceMember


@dataclass(frozen=True)
class TenantContext:
    user_id: UUID
    org_id: UUID
    role: str
    workspace_ids: frozenset[UUID]


_bearer = HTTPBearer(auto_error=False)


async def get_tenant_context(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantContext:
    if creds is None:
        raise AuthenticationError("missing bearer token")
    signing_key = await get_or_create_signing_key(session)
    claims = decode_access_token(creds.credentials, signing_key)
    user = (await session.execute(select(User).where(User.id == claims.user_id))).scalar_one_or_none()
    if user is None or not user.active:
        raise AuthenticationError("unknown or inactive user")
    ws_ids = (
        await session.execute(
            select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
        )
    ).scalars().all()
    return TenantContext(
        user_id=user.id, org_id=user.org_id, role=user.role, workspace_ids=frozenset(ws_ids)
    )


def require_role(*roles: str):  # type: ignore[no-untyped-def]
    async def guard(ctx: Annotated[TenantContext, Depends(get_tenant_context)]) -> TenantContext:
        if ctx.role != "superadmin" and ctx.role not in roles:
            raise AuthorizationError(f"requires role in {sorted(roles)}")
        return ctx

    return guard
```

Note: `WorkspaceMember` doesn't exist until Task 10 — create a forward stub now in `tenancy/models.py`:

```python
class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(default="member")
```

(Task 10 adds the `Workspace` table and the FK on `workspace_id` in its migration.)
Run: `uv run alembic revision --autogenerate -m "workspace members" && uv run alembic upgrade head`

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/api -v && uv run mypy src`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src backend/tests backend/migrations
git commit -m "feat: TenantContext dependency and declarative role guards"
```

---

### Task 10: Workspaces and members

**Files:**
- Modify: `backend/src/openrag/modules/tenancy/models.py` (add `Workspace`, FK on `WorkspaceMember.workspace_id`)
- Create: `backend/src/openrag/modules/tenancy/{schemas.py,service.py}`, `backend/src/openrag/api/routes/workspaces.py`
- Test: `backend/tests/api/test_workspaces.py`

**Interfaces:**
- Produces: `Workspace(id, org_id, name, embedding_model="bge-m3", min_score=0.35, default_model_id=None, created_at)`; schemas `WorkspaceCreate(name)`, `WorkspaceOut(id, name, embedding_model, min_score)`, `MemberAdd(user_id, role="member")`; service `create_workspace(session, ctx, name) -> Workspace` (admin), `list_workspaces(session, ctx) -> list[Workspace]` (membership-filtered; admins see all org workspaces), `add_member(session, ctx, workspace_id, user_id, role)` (admin, target user must be same org — `NotFoundError` otherwise); routes `GET/POST /api/v1/workspaces`, `POST /api/v1/workspaces/{id}/members`.

- [ ] **Step 1: Add `Workspace` model**

In `tenancy/models.py` (and change `WorkspaceMember.workspace_id` to `ForeignKey("workspaces.id")`):

```python
class Workspace(UUIDPk, Base):
    __tablename__ = "workspaces"

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str]
    embedding_model: Mapped[str] = mapped_column(default="bge-m3")
    min_score: Mapped[float] = mapped_column(default=0.35)
    default_model_id: Mapped[UUID | None] = mapped_column(default=None)
```

Run: `uv run alembic revision --autogenerate -m "workspaces" && uv run alembic upgrade head`

- [ ] **Step 2: Write failing tests**

`backend/tests/api/test_workspaces.py`:

```python
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_admin_creates_workspace_and_adds_member(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()

    h_admin = await auth(client, "a@acme.com")
    r = await client.post("/api/v1/workspaces", json={"name": "Finance"}, headers=h_admin)
    assert r.status_code == 201
    ws_id = r.json()["id"]

    h_user = await auth(client, "p@acme.com")
    # not a member yet -> sees nothing
    assert (await client.get("/api/v1/workspaces", headers=h_user)).json() == []
    # plain user cannot create
    assert (await client.post("/api/v1/workspaces", json={"name": "X"}, headers=h_user)).status_code == 403

    r = await client.post(f"/api/v1/workspaces/{ws_id}/members",
                          json={"user_id": str(plain.id)}, headers=h_admin)
    assert r.status_code == 204
    names = [w["name"] for w in (await client.get("/api/v1/workspaces", headers=h_user)).json()]
    assert names == ["Finance"]
```

Run: `uv run pytest tests/api/test_workspaces.py -v` — Expected: FAIL (404, router missing).

- [ ] **Step 3: Implement schemas, service, routes**

`backend/src/openrag/modules/tenancy/schemas.py`:

```python
from uuid import UUID

from pydantic import BaseModel


class WorkspaceCreate(BaseModel):
    name: str


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    embedding_model: str
    min_score: float

    model_config = {"from_attributes": True}


class MemberAdd(BaseModel):
    user_id: UUID
    role: str = "member"
```

`backend/src/openrag/modules/tenancy/service.py`:

```python
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import NotFoundError
from openrag.modules.auth.models import User
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace, WorkspaceMember


async def create_workspace(session: AsyncSession, ctx: TenantContext, name: str) -> Workspace:
    ws = Workspace(org_id=ctx.org_id, name=name)
    session.add(ws)
    await session.commit()
    return ws


async def list_workspaces(session: AsyncSession, ctx: TenantContext) -> list[Workspace]:
    stmt = select(Workspace).where(Workspace.org_id == ctx.org_id)
    if ctx.role == "user":
        stmt = stmt.where(Workspace.id.in_(ctx.workspace_ids))
    return list((await session.execute(stmt.order_by(Workspace.name))).scalars())


async def add_member(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, user_id: UUID, role: str
) -> None:
    ws = (
        await session.execute(
            select(Workspace).where(Workspace.id == workspace_id, Workspace.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if ws is None:
        raise NotFoundError("workspace not found")
    user = (
        await session.execute(select(User).where(User.id == user_id, User.org_id == ctx.org_id))
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("user not found")
    session.add(WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role))
    await session.commit()
```

`backend/src/openrag/api/routes/workspaces.py`:

```python
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.modules.tenancy import service
from openrag.modules.tenancy.context import TenantContext, get_tenant_context, require_role
from openrag.modules.tenancy.schemas import MemberAdd, WorkspaceCreate, WorkspaceOut

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]


@router.post("", status_code=201, response_model=WorkspaceOut)
async def create(body: WorkspaceCreate, session: SessionDep, ctx: AdminDep) -> WorkspaceOut:
    ws = await service.create_workspace(session, ctx, body.name)
    return WorkspaceOut.model_validate(ws)


@router.get("", response_model=list[WorkspaceOut])
async def list_(session: SessionDep, ctx: CtxDep) -> list[WorkspaceOut]:
    return [WorkspaceOut.model_validate(w) for w in await service.list_workspaces(session, ctx)]


@router.post("/{workspace_id}/members", status_code=204)
async def add_member(
    workspace_id: UUID, body: MemberAdd, session: SessionDep, ctx: AdminDep
) -> None:
    await service.add_member(session, ctx, workspace_id, body.user_id, body.role)
```

Register in `api/app.py`: `from openrag.api.routes.workspaces import router as workspaces_router` and `app.include_router(workspaces_router, prefix="/api/v1")`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests backend/migrations
git commit -m "feat: workspaces with membership-scoped listing and member management"
```

---

### Task 11: Invitations

**Files:**
- Modify: `backend/src/openrag/modules/auth/models.py` (add `Invitation`), `auth/schemas.py`, `auth/service.py`, `api/routes/auth.py`
- Test: `backend/tests/api/test_invitations.py`

**Interfaces:**
- Produces: `Invitation(id, org_id, email, role, token_hash, expires_at, accepted_at)`; service `create_invitation(session, ctx, *, email, role, ttl_hours=72) -> str` (returns RAW token once; admin-only enforced at route; `ConflictError` if email already registered); `accept_invitation(session, *, raw_token, password) -> User` (`AuthenticationError` if unknown/expired/used); routes `POST /api/v1/auth/invitations {email, role} -> 201 {invite_token}` (admin), `POST /api/v1/auth/invitations/accept {token, password} -> 201`.

- [ ] **Step 1: Add model + migration**

In `auth/models.py`:

```python
class Invitation(UUIDPk, Base):
    __tablename__ = "invitations"

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    email: Mapped[str] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(default="user")
    token_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None] = mapped_column(default=None)
```

Run: `uv run alembic revision --autogenerate -m "invitations" && uv run alembic upgrade head`

- [ ] **Step 2: Write failing tests**

`backend/tests/api/test_invitations.py`:

```python
import httpx

from openrag.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_invite_flow(client: httpx.AsyncClient, seeded_user: User) -> None:
    h = await auth(client, "a@acme.com")
    r = await client.post("/api/v1/auth/invitations",
                          json={"email": "new@acme.com", "role": "user"}, headers=h)
    assert r.status_code == 201
    token = r.json()["invite_token"]

    r2 = await client.post("/api/v1/auth/invitations/accept",
                           json={"token": token, "password": "newpw12345"})
    assert r2.status_code == 201

    r3 = await client.post("/api/v1/auth/login",
                           json={"email": "new@acme.com", "password": "newpw12345"})
    assert r3.status_code == 200
    # token is single-use
    r4 = await client.post("/api/v1/auth/invitations/accept",
                           json={"token": token, "password": "other12345"})
    assert r4.status_code == 401


async def test_invite_requires_admin(client: httpx.AsyncClient, seeded_user: User) -> None:
    assert (await client.post("/api/v1/auth/invitations", json={"email": "x@x.com", "role": "user"})).status_code == 401
```

Run: `uv run pytest tests/api/test_invitations.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `auth/schemas.py`:

```python
class InvitationCreate(BaseModel):
    email: EmailStr
    role: str = "user"


class InvitationOut(BaseModel):
    invite_token: str


class InvitationAccept(BaseModel):
    token: str
    password: str
```

Append to `auth/service.py`:

```python
from openrag.core.errors import ConflictError  # add import
from openrag.modules.auth.models import Invitation  # add import
from openrag.modules.auth.passwords import hash_password  # add import
from openrag.modules.tenancy.context import TenantContext  # add import


async def create_invitation(
    session: AsyncSession, ctx: TenantContext, *, email: str, role: str, ttl_hours: int = 72
) -> str:
    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("email already registered")
    raw = secrets.token_urlsafe(32)
    session.add(
        Invitation(
            org_id=ctx.org_id, email=email, role=role, token_hash=_hash(raw),
            expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours),
        )
    )
    await session.commit()
    return raw


async def accept_invitation(session: AsyncSession, *, raw_token: str, password: str) -> User:
    inv = (
        await session.execute(select(Invitation).where(Invitation.token_hash == _hash(raw_token)))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if inv is None or inv.accepted_at is not None or inv.expires_at.replace(tzinfo=UTC) < now:
        raise AuthenticationError("invalid or expired invitation")
    inv.accepted_at = now
    user = User(org_id=inv.org_id, email=inv.email,
                password_hash=hash_password(password), role=inv.role)
    session.add(user)
    await session.commit()
    return user
```

Append routes to `api/routes/auth.py`:

```python
from openrag.modules.auth.schemas import InvitationAccept, InvitationCreate, InvitationOut  # add
from openrag.modules.tenancy.context import TenantContext, require_role  # add

AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]


@router.post("/invitations", status_code=201, response_model=InvitationOut)
async def create_invitation(
    body: InvitationCreate, session: SessionDep, ctx: AdminDep
) -> InvitationOut:
    raw = await service.create_invitation(session, ctx, email=body.email, role=body.role)
    return InvitationOut(invite_token=raw)


@router.post("/invitations/accept", status_code=201)
async def accept_invitation(body: InvitationAccept, session: SessionDep) -> dict[str, str]:
    user = await service.accept_invitation(session, raw_token=body.token, password=body.password)
    return {"email": user.email}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests backend/migrations
git commit -m "feat: invite-based onboarding with expiring single-use tokens"
```

---

### Task 12: Org user management endpoints

**Files:**
- Create: `backend/src/openrag/api/routes/users.py`
- Modify: `backend/src/openrag/modules/auth/schemas.py`, `auth/service.py`, `api/app.py` (register router)
- Test: `backend/tests/api/test_users.py`

**Interfaces:**
- Produces: `UserOut(id, email, role, active)`; service `list_users(session, ctx) -> list[User]` (org-scoped), `set_user_active(session, ctx, user_id, active: bool) -> User`, `set_user_role(session, ctx, user_id, role: str) -> User` (both: `NotFoundError` if user not in `ctx.org_id`; admins cannot modify superadmins); routes `GET /api/v1/users` (admin), `PATCH /api/v1/users/{id} {active?, role?}` (admin).

- [ ] **Step 1: Write failing tests**

`backend/tests/api/test_users.py`:

```python
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_list_and_deactivate(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()

    h = await auth(client, "a@acme.com")
    emails = [u["email"] for u in (await client.get("/api/v1/users", headers=h)).json()]
    assert set(emails) == {"a@acme.com", "p@acme.com"}

    r = await client.patch(f"/api/v1/users/{plain.id}", json={"active": False}, headers=h)
    assert r.status_code == 200 and r.json()["active"] is False
    # deactivated user can no longer log in
    r2 = await client.post("/api/v1/auth/login", json={"email": "p@acme.com", "password": "pw123456"})
    assert r2.status_code == 401
```

Run: `uv run pytest tests/api/test_users.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement**

Append to `auth/schemas.py`:

```python
from uuid import UUID  # add


class UserOut(BaseModel):
    id: UUID
    email: EmailStr
    role: str
    active: bool

    model_config = {"from_attributes": True}


class UserPatch(BaseModel):
    active: bool | None = None
    role: str | None = None
```

Append to `auth/service.py`:

```python
from uuid import UUID as _UUID  # add
from openrag.core.errors import NotFoundError  # add


async def list_users(session: AsyncSession, ctx: TenantContext) -> list[User]:
    return list(
        (await session.execute(select(User).where(User.org_id == ctx.org_id).order_by(User.email))).scalars()
    )


async def _org_user(session: AsyncSession, ctx: TenantContext, user_id: _UUID) -> User:
    user = (
        await session.execute(select(User).where(User.id == user_id, User.org_id == ctx.org_id))
    ).scalar_one_or_none()
    if user is None or user.role == "superadmin":
        raise NotFoundError("user not found")
    return user


async def set_user_active(session: AsyncSession, ctx: TenantContext, user_id: _UUID, active: bool) -> User:
    user = await _org_user(session, ctx, user_id)
    user.active = active
    await session.commit()
    return user


async def set_user_role(session: AsyncSession, ctx: TenantContext, user_id: _UUID, role: str) -> User:
    user = await _org_user(session, ctx, user_id)
    user.role = role
    await session.commit()
    return user
```

`backend/src/openrag/api/routes/users.py`:

```python
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.modules.auth import service
from openrag.modules.auth.schemas import UserOut, UserPatch
from openrag.modules.tenancy.context import TenantContext, require_role

router = APIRouter(prefix="/users", tags=["users"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]


@router.get("", response_model=list[UserOut])
async def list_users(session: SessionDep, ctx: AdminDep) -> list[UserOut]:
    return [UserOut.model_validate(u) for u in await service.list_users(session, ctx)]


@router.patch("/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: UUID, body: UserPatch, session: SessionDep, ctx: AdminDep
) -> UserOut:
    user = None
    if body.active is not None:
        user = await service.set_user_active(session, ctx, user_id, body.active)
    if body.role is not None:
        user = await service.set_user_role(session, ctx, user_id, body.role)
    if user is None:
        user = (await service.list_users(session, ctx))[0]  # no-op patch; still validated org-scoped
    return UserOut.model_validate(user)
```

Register in `app.py`: `app.include_router(users_router, prefix="/api/v1")`.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat: org user management (list, deactivate, role change)"
```

---

### Task 13: Audit module, wired into services

**Files:**
- Create: `backend/src/openrag/modules/audit/{models.py,service.py}`
- Modify: `auth/service.py` (login, accept_invitation, set_user_active, set_user_role), `tenancy/service.py` (create_workspace, add_member), `migrations/env.py` (import audit models)
- Test: `backend/tests/modules/audit/test_audit.py`

**Interfaces:**
- Produces: `AuditEvent(id, org_id: UUID|None, actor_id: UUID|None, action: str, target_type: str, target_id: str, created_at)`; `async record_audit(session, *, org_id, actor_id, action, target_type, target_id) -> None` (adds to session; caller's commit persists it — audit and action commit atomically). Actions used: `login.success`, `login.failure`, `invitation.created`, `invitation.accepted`, `user.deactivated`, `user.role_changed`, `workspace.created`, `workspace.member_added`.

- [ ] **Step 1: Write failing test**

`backend/tests/modules/audit/test_audit.py`:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.modules.audit.models import AuditEvent
from openrag.modules.auth.service import login
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.tenancy.models import Organization


async def test_login_writes_audit(session: AsyncSession) -> None:
    org = Organization(name="A")
    session.add(org)
    await session.flush()
    session.add(User(org_id=org.id, email="a@a.com",
                     password_hash=hash_password("pw123456"), role="user"))
    await session.commit()

    await login(session, email="a@a.com", password="pw123456", settings=Settings(_env_file=None))
    events = list((await session.execute(select(AuditEvent))).scalars())
    assert [e.action for e in events] == ["login.success"]
    assert events[0].org_id == org.id
```

Run: `uv run pytest tests/modules/audit -v` — Expected: FAIL.

- [ ] **Step 2: Implement**

`backend/src/openrag/modules/audit/models.py`:

```python
from uuid import UUID

from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class AuditEvent(UUIDPk, Base):
    __tablename__ = "audit_events"

    org_id: Mapped[UUID | None] = mapped_column(index=True, default=None)
    actor_id: Mapped[UUID | None] = mapped_column(default=None)
    action: Mapped[str] = mapped_column(index=True)
    target_type: Mapped[str]
    target_id: Mapped[str]
```

`backend/src/openrag/modules/audit/service.py`:

```python
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.audit.models import AuditEvent


async def record_audit(
    session: AsyncSession,
    *,
    org_id: UUID | None,
    actor_id: UUID | None,
    action: str,
    target_type: str,
    target_id: str,
) -> None:
    session.add(
        AuditEvent(
            org_id=org_id, actor_id=actor_id, action=action,
            target_type=target_type, target_id=target_id,
        )
    )
```

Wire calls **before the existing `await session.commit()`** in each mutating service function. Example for `login` (repeat the pattern for the other seven actions listed in Interfaces):

```python
from openrag.modules.audit.service import record_audit  # add to auth/service.py imports

# inside login(), replace the raise path and success path:
    if user is None or not user.active or not verify_password(user.password_hash, password):
        await record_audit(session, org_id=None, actor_id=None, action="login.failure",
                           target_type="user", target_id=email)
        await session.commit()
        raise AuthenticationError("invalid credentials")
    await record_audit(session, org_id=user.org_id, actor_id=user.id, action="login.success",
                       target_type="user", target_id=str(user.id))
    return await _issue_pair(session, user, uuid4(), settings)
```

Add `import openrag.modules.audit.models  # noqa: F401` to `migrations/env.py`, then:
Run: `uv run alembic revision --autogenerate -m "audit events" && uv run alembic upgrade head`

Append to the generated migration's `upgrade()` (append-only enforcement at the DB layer):

```python
    op.execute("REVOKE UPDATE, DELETE ON audit_events FROM openrag")
```

(Testcontainers runs as a superuser so ORM-level discipline is what tests enforce; the REVOKE protects real deployments.)

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS (existing tests confirm audit wiring broke nothing).

- [ ] **Step 4: Commit**

```bash
git add backend/src backend/tests backend/migrations
git commit -m "feat: append-only audit events wired into auth and tenancy services"
```

---

### Task 14: Health endpoints, bootstrap CLI, import-linter gate

**Files:**
- Create: `backend/src/openrag/api/routes/health.py`, `backend/src/openrag/bootstrap.py`
- Modify: `backend/src/openrag/api/app.py` (register health router)
- Test: `backend/tests/api/test_health.py`, `backend/tests/test_bootstrap.py`

**Interfaces:**
- Produces: `GET /healthz -> 200 {"status":"ok"}` (no dependencies); `GET /readyz -> 200 {"status":"ready"}` or `503` (runs `SELECT 1`); `python -m openrag.bootstrap` — creates org "Platform" + superadmin from `OPENRAG_BOOTSTRAP_EMAIL` / `OPENRAG_BOOTSTRAP_PASSWORD` env vars, idempotent (exposed as `async bootstrap_superadmin(session_factory, *, email, password) -> bool` returning True if created).

- [ ] **Step 1: Write failing tests**

`backend/tests/api/test_health.py`:

```python
import httpx


async def test_healthz(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_readyz(client: httpx.AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ready"}
```

`backend/tests/test_bootstrap.py`:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from openrag.bootstrap import bootstrap_superadmin
from openrag.core.db import build_session_factory
from openrag.modules.auth.models import User


async def test_bootstrap_idempotent(engine: AsyncEngine) -> None:
    factory = build_session_factory(engine)
    assert await bootstrap_superadmin(factory, email="root@x.com", password="rootpw12345") is True
    assert await bootstrap_superadmin(factory, email="root@x.com", password="rootpw12345") is False
    async with factory() as s:
        user = (await s.execute(select(User).where(User.email == "root@x.com"))).scalar_one()
        assert user.role == "superadmin"
```

Run: `uv run pytest tests/api/test_health.py tests/test_bootstrap.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement**

`backend/src/openrag/api/routes/health.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(session: Annotated[AsyncSession, Depends(get_session)]) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - readiness must never 500
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return JSONResponse(content={"status": "ready"})
```

Register in `app.py` **without** the `/api/v1` prefix: `app.include_router(health_router)`.

`backend/src/openrag/bootstrap.py`:

```python
"""Idempotent first-run bootstrap: creates the platform org and superadmin.

Usage: OPENRAG_BOOTSTRAP_EMAIL=... OPENRAG_BOOTSTRAP_PASSWORD=... uv run python -m openrag.bootstrap
"""

import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import get_settings
from openrag.core.db import build_engine, build_session_factory
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.tenancy.models import Organization


async def bootstrap_superadmin(
    session_factory: async_sessionmaker[AsyncSession], *, email: str, password: str
) -> bool:
    async with session_factory() as session:
        existing = (
            await session.execute(select(User).where(User.role == "superadmin"))
        ).scalar_one_or_none()
        if existing is not None:
            return False
        org = Organization(name="Platform")
        session.add(org)
        await session.flush()
        session.add(User(org_id=org.id, email=email,
                         password_hash=hash_password(password), role="superadmin"))
        await session.commit()
        return True


def main() -> None:
    email = os.environ.get("OPENRAG_BOOTSTRAP_EMAIL")
    password = os.environ.get("OPENRAG_BOOTSTRAP_PASSWORD")
    if not email or not password:
        print("Set OPENRAG_BOOTSTRAP_EMAIL and OPENRAG_BOOTSTRAP_PASSWORD", file=sys.stderr)
        raise SystemExit(2)
    factory = build_session_factory(build_engine(get_settings().database_url))
    created = asyncio.run(bootstrap_superadmin(factory, email=email, password=password))
    print("superadmin created" if created else "superadmin already exists")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run tests + full gate**

Run: `uv run pytest tests -v && uv run ruff check . && uv run mypy src && uv run lint-imports`
Expected: all tests PASS; import-linter reports `Contracts: 1 kept, 0 broken`.

- [ ] **Step 4: Smoke the real stack**

Run (repo root): `docker compose -f deploy/compose.yaml up -d && cd backend && uv run alembic upgrade head && OPENRAG_BOOTSTRAP_EMAIL=root@openrag.internal OPENRAG_BOOTSTRAP_PASSWORD=changeme123 uv run python -m openrag.bootstrap && uv run uvicorn --factory openrag.api.app:create_app --port 8000 &`
Then: `curl -s localhost:8000/readyz` → `{"status":"ready"}`; `curl -s -X POST localhost:8000/api/v1/auth/login -H 'content-type: application/json' -d '{"email":"root@openrag.internal","password":"changeme123"}'` → JSON with `access_token`. Stop uvicorn afterward.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat: health endpoints, idempotent superadmin bootstrap CLI"
```

---

### Task 15: Login rate limiting (iron rule 4)

**Files:**
- Create: `backend/src/openrag/core/ratelimit.py`
- Modify: `backend/src/openrag/api/routes/auth.py` (guard `/auth/login`)
- Test: `backend/tests/api/test_ratelimit.py`

**Interfaces:**
- Produces: `RateLimitExceeded(OpenRAGError)` with `status_code = 429`, `title = "Too many requests"` (add to `core/errors.py`); `FixedWindowLimiter(limit: int, window_seconds: int)` with method `check(key: str) -> None` (raises `RateLimitExceeded`); dependency factory `rate_limit(scope: str, limit: int = 10, window_seconds: int = 60)` keyed on client IP. In-process store now; the interface stays put when Plan B swaps the store to Redis for multi-worker deployments.

- [ ] **Step 1: Write failing test**

`backend/tests/api/test_ratelimit.py`:

```python
import httpx

from openrag.modules.auth.models import User


async def test_login_rate_limited(client: httpx.AsyncClient, seeded_user: User) -> None:
    for _ in range(10):
        await client.post("/api/v1/auth/login", json={"email": "a@acme.com", "password": "bad"})
    r = await client.post("/api/v1/auth/login", json={"email": "a@acme.com", "password": "pw123456"})
    assert r.status_code == 429
    assert r.headers["content-type"].startswith("application/problem+json")
```

Run: `uv run pytest tests/api/test_ratelimit.py -v` — Expected: FAIL (final status 200, not 429).

- [ ] **Step 2: Implement**

Add to `core/errors.py`:

```python
class RateLimitExceeded(OpenRAGError):
    status_code = 429
    title = "Too many requests"
```

`backend/src/openrag/core/ratelimit.py`:

```python
import time
from collections import defaultdict

from fastapi import Request

from openrag.core.errors import RateLimitExceeded


class FixedWindowLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        now = time.monotonic()
        window = [t for t in self._hits[key] if now - t < self.window_seconds]
        if len(window) >= self.limit:
            self._hits[key] = window
            raise RateLimitExceeded("rate limit exceeded, retry later")
        window.append(now)
        self._hits[key] = window


def rate_limit(scope: str, limit: int = 10, window_seconds: int = 60):  # type: ignore[no-untyped-def]
    limiter = FixedWindowLimiter(limit, window_seconds)

    async def guard(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        limiter.check(f"{scope}:{client_ip}")

    return guard
```

In `api/routes/auth.py`, guard the login route:

```python
from fastapi import Depends  # already imported
from openrag.core.ratelimit import rate_limit  # add

@router.post("/login", response_model=AccessTokenResponse,
             dependencies=[Depends(rate_limit("login", limit=10, window_seconds=60))])
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests -v && uv run ruff check . && uv run mypy src`
Expected: all PASS (note: test isolation — the limiter is per-app-instance; the `client` fixture builds a fresh app per test, so counts don't leak).

- [ ] **Step 4: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat: fixed-window rate limiting on login endpoint"
```

---

## Plan A Completion Criteria

- `uv run pytest tests -v` — all green (unit + integration on real Postgres).
- `uv run ruff check . && uv run mypy src && uv run lint-imports` — clean.
- Fresh-stack smoke (Task 14 Step 4) works end to end: compose up → migrate → bootstrap → login via curl.
- Plans B (ingestion + retrieval), C (models + secrets + chat), D (frontend) are written after this plan executes, referencing the real code.
