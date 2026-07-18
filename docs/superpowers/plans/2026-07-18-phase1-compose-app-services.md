# Phase 1 Compose Application Services Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a fresh Docker Compose launch run the complete OpenRAG web, API, bootstrap, worker, and infrastructure stack with an optional TEI/Ollama profile.

**Architecture:** Build one Python image shared by migrations, bootstrap, API, and Celery worker. Build the React application into an Nginx image that serves SPA routes and proxies `/api`, `/healthz`, and `/readyz` to FastAPI; Compose health/dependency conditions order stateful infrastructure, migrations, bootstrap, and runtime services.

**Tech Stack:** Docker Compose, uv Python 3.12 image, Node 20, Nginx Alpine, FastAPI/Uvicorn, Celery, React/Vite.

## Global Constraints

- Work directly on `main`; periodic direct pushes are authorized.
- Do not bake provider secrets or the KEK into images.
- Bind host ports to `127.0.0.1` by default and make app/web ports overridable.
- Use the committed generated frontend OpenAPI schema during image builds.
- Base Compose defaults to the deterministic hash dense embedder; `OPENRAG_EMBEDDING_BACKEND=tei --profile ml` enables BGE-M3.
- Bootstrap is idempotent and uses development-only defaults that are overridable by environment variables.
- Preserve existing named volumes; verification must not remove user volumes.

---

### Task 1: Backend runtime image

**Files:**
- Create: `backend/Dockerfile`
- Create: `backend/.dockerignore`

**Interfaces:**
- Produces image working directory `/app`, project virtual environment `/app/.venv`, and installed `openrag` package.
- Supports commands for Alembic, bootstrap, Uvicorn, and Celery without development dependencies.

- [ ] **Step 1: Write the Dockerfile structure test**

Create `backend/tests/test_dockerfile.py`:

```python
from pathlib import Path


def test_backend_dockerfile_supports_runtime_processes() -> None:
    text = (Path(__file__).parents[2] / "backend" / "Dockerfile").read_text()
    assert "uv sync --frozen --no-dev" in text
    assert "COPY src ./src" in text
    assert "COPY migrations ./migrations" in text
    assert "USER openrag" in text
```

- [ ] **Step 2: Verify the structure test fails**

Run: `uv run pytest tests/test_dockerfile.py -v`

Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Create the backend image files**

`backend/Dockerfile`:

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy
WORKDIR /app

RUN groupadd --system openrag && useradd --system --gid openrag --create-home openrag
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev \
    && mkdir -p /data \
    && chown -R openrag:openrag /app /data

USER openrag
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "--factory", "openrag.api.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
```

`backend/.dockerignore`:

```text
.venv
data
.pytest_cache
.mypy_cache
.ruff_cache
.import_linter_cache
tests
__pycache__
*.pyc
```

- [ ] **Step 4: Verify test and image build**

Run:

```bash
uv run pytest tests/test_dockerfile.py -v
docker build -t openrag-backend:local backend
docker run --rm openrag-backend:local uv run python -c "import openrag; print('openrag import ok')"
```

Expected: test passes, image builds, and container prints `openrag import ok`.

- [ ] **Step 5: Commit and push**

```bash
git add backend/Dockerfile backend/.dockerignore backend/tests/test_dockerfile.py
git commit -m "build: add OpenRAG backend runtime image"
git push origin main
```

---

### Task 2: Frontend Nginx image and SPA proxy

**Files:**
- Create: `frontend/Dockerfile`
- Create: `frontend/nginx.conf`
- Modify: `frontend/.gitignore` only if image-generated files need ignoring; no new generated directory is expected.
- Test: `frontend/src/app/deployment-config.test.ts`

**Interfaces:**
- Produces an Nginx image serving the Vite build on port 80.
- Proxies `/api/`, `/healthz`, and `/readyz` to `http://api:8000`.
- Falls back unknown non-API routes to `/index.html`.

- [ ] **Step 1: Write the failing deployment-config test**

Create `frontend/src/app/deployment-config.test.ts`:

```ts
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('nginx serves SPA routes and proxies the OpenRAG API', () => {
  const config = readFileSync(resolve(process.cwd(), 'nginx.conf'), 'utf8');
  expect(config).toContain('try_files $uri $uri/ /index.html');
  expect(config).toContain('proxy_pass http://api:8000');
  expect(config).toContain('location /api/');
});
```

- [ ] **Step 2: Verify the test fails**

Run: `corepack pnpm test src/app/deployment-config.test.ts -- --run`

Expected: FAIL because `nginx.conf` does not exist.

- [ ] **Step 3: Create Nginx and multi-stage Docker files**

`frontend/Dockerfile`:

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

`frontend/nginx.conf`:

```nginx
server {
  listen 80;
  server_name _;
  root /usr/share/nginx/html;
  index index.html;

  location /api/ {
    proxy_pass http://api:8000;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  location = /healthz { proxy_pass http://api:8000/healthz; }
  location = /readyz { proxy_pass http://api:8000/readyz; }

  location / {
    try_files $uri $uri/ /index.html;
  }
}
```

- [ ] **Step 4: Verify tests and image**

Run:

```bash
corepack pnpm test src/app/deployment-config.test.ts -- --run
corepack pnpm build
docker build -t openrag-frontend:local frontend
```

Expected: tests/build pass and the image builds.

- [ ] **Step 5: Commit and push**

```bash
git add frontend/Dockerfile frontend/nginx.conf frontend/src/app/deployment-config.test.ts
git commit -m "build: add OpenRAG frontend runtime image"
git push origin main
```

---

### Task 3: Compose migrations, bootstrap, API, worker, web, and Ollama

**Files:**
- Modify: `deploy/compose.yaml`
- Modify: `.env.example`
- Test: `backend/tests/test_compose.py`

**Interfaces:**
- Produces services `migrate`, `bootstrap`, `api`, `worker`, and `web`.
- Produces optional profile services `tei` and `ollama` under profile `ml`.
- Produces `kekdata` and `ollamadata` named volumes.
- Maps API to `${OPENRAG_API_PORT:-8000}` and web to `${OPENRAG_WEB_PORT:-5173}` on loopback.

- [ ] **Step 1: Write the failing Compose contract test**

Create `backend/tests/test_compose.py` with no new Python dependency:

```python
import json
import subprocess
from pathlib import Path


def test_compose_contains_complete_application_stack() -> None:
    compose = Path(__file__).parents[2] / "deploy" / "compose.yaml"
    rendered = subprocess.run(
        ["docker", "compose", "-f", str(compose), "config", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    config = json.loads(rendered.stdout)
    required = {"postgres", "redis", "qdrant", "minio", "litellm", "migrate", "bootstrap", "api", "worker", "web"}
    assert required <= set(config["services"])
    assert config["services"]["api"]["depends_on"]["bootstrap"]["condition"] == "service_completed_successfully"
    assert "kekdata" in config["volumes"]
    assert "ollama" in config["services"]
```

- [ ] **Step 2: Verify the Compose test fails**

Run: `uv run pytest tests/test_compose.py -v`

Expected: FAIL because application services are absent.

- [ ] **Step 3: Add shared environment and ordered services**

Extend `deploy/compose.yaml` with a YAML anchor containing internal service URLs:

```yaml
x-openrag-environment: &openrag-environment
  OPENRAG_DATABASE_URL: postgresql+asyncpg://openrag:openrag@postgres:5432/openrag
  OPENRAG_REDIS_URL: redis://redis:6379/0
  OPENRAG_QDRANT_URL: http://qdrant:6333
  OPENRAG_MINIO_ENDPOINT: http://minio:9000
  OPENRAG_MINIO_ACCESS_KEY: openrag
  OPENRAG_MINIO_SECRET_KEY: openrag123
  OPENRAG_MINIO_BUCKET: openrag-documents
  OPENRAG_TEI_URL: http://tei:80
  OPENRAG_EMBEDDING_BACKEND: ${OPENRAG_EMBEDDING_BACKEND:-hash}
  OPENRAG_EMBEDDING_DIM: ${OPENRAG_EMBEDDING_DIM:-1024}
  OPENRAG_LITELLM_URL: http://litellm:4000
  OPENRAG_LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY:-sk-openrag-dev-master}
  OPENRAG_KEK_FILE: /data/openrag_kek
```

Add one-shot migration/bootstrap services using the backend image and `kekdata`; add API and worker services that depend on bootstrap plus healthy infrastructure. Use these exact runtime commands:

```yaml
command: ["uv", "run", "alembic", "upgrade", "head"]
```

```yaml
command: ["uv", "run", "python", "-m", "openrag.bootstrap"]
environment:
  <<: *openrag-environment
  OPENRAG_BOOTSTRAP_EMAIL: ${OPENRAG_BOOTSTRAP_EMAIL:-root@openrag.internal}
  OPENRAG_BOOTSTRAP_PASSWORD: ${OPENRAG_BOOTSTRAP_PASSWORD:-changeme123}
```

```yaml
command: ["uv", "run", "uvicorn", "--factory", "openrag.api.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
ports: ["127.0.0.1:${OPENRAG_API_PORT:-8000}:8000"]
```

```yaml
command: ["uv", "run", "celery", "-A", "openrag.worker.celery_app:celery_app", "worker", "-Q", "interactive,default", "-l", "info", "--concurrency=1"]
```

Add `web` built from `frontend/`, depending on healthy API, mapped to `127.0.0.1:${OPENRAG_WEB_PORT:-5173}:80`. Add optional Ollama:

```yaml
ollama:
  image: ollama/ollama:latest
  profiles: ["ml"]
  ports: ["127.0.0.1:${OPENRAG_OLLAMA_PORT:-11434}:11434"]
  volumes: [ollamadata:/root/.ollama]
```

- [ ] **Step 4: Update environment documentation**

Add the bootstrap credentials, overridable API/web ports, `OPENRAG_EMBEDDING_BACKEND`, and profile examples to `.env.example`. Label `changeme123` development-only.

- [ ] **Step 5: Verify Compose rendering and tests**

Run:

```bash
docker compose -f deploy/compose.yaml config --quiet
uv run pytest tests/test_compose.py -v
```

Expected: both exit zero.

- [ ] **Step 6: Commit and push**

```bash
git add deploy/compose.yaml .env.example backend/tests/test_compose.py
git commit -m "feat: run complete OpenRAG stack with Compose"
git push origin main
```

---

### Task 4: Non-destructive containerized smoke and documentation

**Files:**
- Modify: `README.md`
- Modify: `frontend/README.md`

**Interfaces:**
- Documents default one-command startup and optional ML profile.
- Verifies containerized login without deleting or replacing named user volumes.

- [ ] **Step 1: Update startup documentation**

Replace the three-terminal default path with:

```bash
docker compose -f deploy/compose.yaml up -d --build
```

Document the local-source development commands as an alternative. Add ML startup:

```bash
OPENRAG_EMBEDDING_BACKEND=tei \
docker compose -f deploy/compose.yaml --profile ml up -d --build
```

- [ ] **Step 2: Build and launch app services on alternate host ports**

Keep existing infrastructure volumes and avoid collisions with local dev processes:

```bash
OPENRAG_API_PORT=58000 OPENRAG_WEB_PORT=55173 \
docker compose -f deploy/compose.yaml up -d --build api worker web
```

- [ ] **Step 3: Condition-poll readiness and login**

Poll `http://localhost:58000/readyz` until it returns 200, then verify:

```bash
curl -fsS http://localhost:58000/healthz
curl -fsS -H 'content-type: application/json' \
  -d '{"email":"root@openrag.internal","password":"changeme123"}' \
  http://localhost:58000/api/v1/auth/login
curl -fsS -o /dev/null http://localhost:55173/login
```

Expected: health is `{"status":"ok"}`, login returns a bearer access token, and the web route returns 200.

- [ ] **Step 4: Run complete repository gates**

Backend:

```bash
uv run pytest tests -q
uv run ruff check .
uv run mypy src
uv run lint-imports
```

Frontend:

```bash
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm test -- --run
corepack pnpm build
corepack pnpm e2e
```

Expected: all gates pass; Playwright skips exactly once without `E2E=1`.

- [ ] **Step 5: Commit and push**

```bash
git add README.md frontend/README.md
git commit -m "docs: document one-command OpenRAG startup"
git push origin main
```

---

## Completion Evidence

- `docker compose config --quiet` and the Compose contract test pass.
- Backend and frontend images build reproducibly from committed files.
- Migration and bootstrap jobs complete before API/worker start.
- Containerized `/healthz`, `/readyz`, login, and SPA route checks pass on alternate ports.
- Default Compose uses no external model credential; the ML profile adds TEI and Ollama without changing application code.
