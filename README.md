# OpenRAG

OpenRAG is an open, self-hosted platform for building secure AI assistants over private business knowledge. It brings document ingestion, hybrid retrieval, model choice, citations, access control, and administration into one system that can run in the cloud, on private infrastructure, or fully on-premises.

OpenRAG is under active development. The repository currently includes a working FastAPI backend, React frontend, authentication and invitations, multi-tenant workspaces, asynchronous document ingestion, hybrid retrieval, streamed cited chat, and model/user administration.

## Why OpenRAG

Businesses want to use generative AI with internal documents, but production deployments must solve more than document search. They need to protect sensitive data, respect user permissions, avoid dependence on one model provider, operate at large corpus sizes, and produce answers that people can verify.

OpenRAG is designed around those needs:

- **Self-hosted and privacy-first:** Run every component inside your own infrastructure, including language models, embeddings, reranking, storage, and observability.
- **Model-agnostic:** Use hosted providers or local models through the in-process LiteLLM Python library, with support for OpenAI-compatible endpoints and no proxy control plane.
- **Grounded answers:** Combine dense and sparse retrieval, stream responses, and attach citations that resolve to the original document and page.
- **Enterprise access controls:** Isolate organizations and workspaces, enforce document permissions during retrieval, and maintain append-only audit records.
- **Built for scale:** Process large document collections asynchronously and search them through Qdrant with tenant-aware filters.
- **Operationally transparent:** Expose ingestion status, usage, model health, retrieval traces, and administrative controls through a professional web interface.

## Core capabilities

OpenRAG is planned to provide:

- Email/password authentication, invitations, SSO, MFA, and role-based access control
- Multi-tenant organizations, workspaces, groups, and document-level permissions
- Upload and ingestion for PDF, DOCX, XLSX, PPTX, CSV, text, Markdown, HTML, and images
- OCR, semantic chunking, metadata extraction, deduplication, and document versioning
- Hybrid dense and sparse retrieval with optional reranking and no-answer safeguards
- Streaming chat with conversation history, source citations, and document previews
- Agentic retrieval for multi-step questions using read-only search tools
- Hosted and local model registration, capability probing, fallback chains, and BYOK
- Encrypted secret storage, quotas, budgets, usage reporting, and audit logs
- Connectors for object storage, shared drives, knowledge systems, and websites
- Docker Compose, Kubernetes, and air-gapped deployment options

## How it works

1. An administrator creates an organization and one or more workspaces.
2. Users upload documents or synchronize them from connected sources.
3. Background workers parse, chunk, embed, and index the content.
4. A user asks a question in a workspace.
5. OpenRAG retrieves only the content that the user is authorized to access.
6. The selected language model generates a streamed, grounded answer with citations.
7. Usage, retrieval activity, administrative changes, and feedback feed operational and quality reporting.

## Capability-based access control

OpenRAG authorizes every protected request from current PostgreSQL state. A signed
access token identifies the user and carries UI hints, but the API reloads active
role bindings, workspace membership, and platform status on every request. Role
names are labels only; they never grant authority by string comparison.

### Built-in organization roles

| Template | Intended use | Capabilities |
|---|---|---|
| Administrator | Organization administration and knowledge governance | All organization capabilities listed below |
| HSE Manager | Manage and approve HSE knowledge in assigned workspaces | `chat.use`, `document.read`, `document.upload`, `document.approve` |
| Engineer | Use chat and contribute knowledge in assigned workspaces | `chat.use`, `document.read`, `document.upload` |
| User | Use grounded chat and read assigned knowledge | `chat.use`, `document.read` |

The built-in templates are protected system roles. An organization role manager
may create custom roles from the same closed capability catalog, update a custom
role, and delete an unbound custom role. Custom roles cannot add unknown
capabilities, cannot become platform superadmin, cannot cross organization
boundaries, and cannot bypass workspace membership. The last active
Administrator binding cannot be removed.

Workspace membership and role assignment are independent:

- A role answers **what actions** a user may perform.
- Membership answers **which workspaces** the user may reach.
- An organization capability does not grant workspace access without membership,
  except the explicit `workspace.read_all` governance capability.
- A workspace-scoped role binding applies only to that workspace, even if the user
  is a member of other workspaces.

### Permission meanings

| Permission | Meaning |
|---|---|
| `chat.use` | Ask grounded questions in authorized workspaces |
| `document.read` | View authorized documents and cited evidence |
| `document.upload` | Add documents for background extraction and indexing |
| `document.approve` | Approve governed document versions for retrieval |
| `workspace.manage` | Create workspaces and manage membership |
| `workspace.read_all` | Read every workspace in the current organization |
| `user.manage` | Invite, activate, and deactivate organization users |
| `role.manage` | Create roles and replace organization role bindings |
| `model.configure` | Configure organization model and retrieval profiles |
| `rag.evaluate` | Run and review grounded-answer quality evaluations |
| `audit.read` | Review immutable security and administration events |

Platform superadmin is not a permission and is not an organization role. It is a
non-assignable platform flag created only by the serialized bootstrap command.
Organization administrators cannot view, create, invite, bind, or promote a
platform superadmin. Platform model and secret administration remains behind the
separate platform boundary.

## Architecture

The platform uses a modular monolith with independently scalable API and worker processes plus dedicated infrastructure services:

| Area | Technology |
|---|---|
| API | FastAPI, Python, Pydantic, SQLAlchemy |
| Web application | React, TypeScript, Vite, Tailwind CSS, shadcn/ui |
| Relational data | PostgreSQL |
| Vector search | Qdrant |
| Object storage | MinIO or S3-compatible storage |
| Background jobs | Celery and Redis |
| Model runtime | Agno orchestration with the in-process LiteLLM Python library |
| Embeddings | Text Embeddings Inference with BGE-M3 |
| Document parsing and OCR | Docling with local RapidOCR |
| Observability | OpenTelemetry, Prometheus, and Grafana |

The design follows five foundational rules: tenant filtering has one enforced path per data store, document permissions are applied inside retrieval, secrets have one controlled decryption path, authorization is declared at API boundaries, and all document content and model output are treated as untrusted data.

See the [high-level architecture](docs/architecture.md) for service boundaries and ingestion/query flows.

## Deploy with Docker Compose

Docker Compose is the supported single-node evaluation and development deployment. It starts PostgreSQL, isolated broker and durable event Redis services, Qdrant, MinIO, database migrations, bootstrap, the FastAPI service, ingestion and event workers, the event scheduler, and the React web application. Completion and hosted-embedding calls run through the LiteLLM Python library inside bounded application workers; no LiteLLM Proxy service or master key is required.

Uploads are streamed through an owner-only quarantine and validated by extension,
declared MIME, file signature, and bounded Office archive expansion. PDF parsing
enforces byte, page, rendered-pixel, extracted-block, text-output, worker-memory,
and wall-time budgets. Scanned and mixed PDFs use local per-page OCR by default;
set `OPENRAG_OCR_MODE` to `auto`, `force`, or `disabled` and configure comma-separated
RapidOCR languages with `OPENRAG_OCR_LANGUAGES`.

### 1. Prepare the host

Install Git and Docker with the Compose v2 plugin, then clone the public repository:

```bash
git clone https://github.com/marketcalls/openrag.git
cd openrag
cp .env.example .env
```

Before the first startup, edit `.env` and replace at least:

```dotenv
OPENRAG_BOOTSTRAP_EMAIL=admin@example.com
OPENRAG_BOOTSTRAP_PASSWORD=replace-with-a-long-random-password
```

Create the local event-transport credential as an ignored, owner-readable file.
It is mounted only into the dedicated event Redis and event worker, not placed
in Compose environment variables or exposed to the API and ingestion worker.

```bash
install -d -m 700 data
openssl rand -hex -out data/event_redis_password 32
chmod 600 data/event_redis_password
```

`OPENRAG_EVENT_REDIS_SECRET_FILE` may point to a different secret-managed file
for nonlocal deployments. Never commit that file.

The bootstrap credentials create the first platform superadmin only when one does
not already exist. Bootstrap is the only application path that can set platform
superadmin, concurrent bootstrap attempts are serialized, and changing the
variables later does not change an existing account password.

### 2. Start OpenRAG

```bash
docker compose -f deploy/compose.yaml up -d --build
```

The first build downloads base images and document-processing dependencies, so it can take several minutes. Compose waits for the infrastructure, runs Alembic migrations, prepares the encryption key, creates the first superadmin, and then starts the application services.

Check the deployment:

```bash
docker compose -f deploy/compose.yaml ps
curl --fail http://localhost:8000/readyz
```

Every listed service should be running or successfully completed, and readiness should return `{"status":"ready"}`. If startup fails, inspect the application services:

```bash
docker compose -f deploy/compose.yaml logs --tail=200 api worker event-worker event-scheduler migrate bootstrap web
```

### 3. Sign in and configure a model

Open [http://localhost:5173](http://localhost:5173) and use the bootstrap email and password from `.env`. With the unchanged development defaults, they are:

- Email: `root@openrag.internal`
- Password: `changeme123`

Go to **Superadmin → Models**, register a hosted or local completion model and its write-only credential, then create a workspace and select its default model. Upload a document, wait until its status is **Indexed**, and start a chat.

### 4. Choose an embedding mode

The default `hash` embedder avoids a model download and is intended only for installation smoke tests. It is lexical rather than semantic, so generic questions such as “tell me about the invoice” may not match invoice text reliably.

For real document question answering, start the optional local ML profile with Text Embeddings Inference and BGE-M3:

```bash
OPENRAG_EMBEDDING_BACKEND=tei \
docker compose -f deploy/compose.yaml --profile ml up -d --build
```

The first start downloads model images and weights. Documents indexed with a different embedding implementation must be re-indexed before querying them with the new model. Configurable embedding registration and managed re-indexing are active roadmap work; do not switch an already populated production workspace by changing only the environment variable.

The same `ml` profile starts Ollama. Its host port defaults to `11434` and can be changed with `OPENRAG_OLLAMA_PORT`.

### 5. Operate and upgrade the deployment

Before upgrading an installation created before capability RBAC, take a tested
PostgreSQL backup. Revision `6c4a2f8b9d10` locks the affected legacy tables,
fails closed if it finds an unknown legacy role, seeds the four protected role
templates for every organization, and performs this backfill:

- legacy `superadmin` becomes the bootstrap-only platform flag;
- legacy `admin` receives an organization-wide Administrator binding;
- legacy `user` receives an organization-wide User binding;
- pending invitations receive the corresponding role ID; and
- workspace membership receives an explicit organization scope and no longer
  stores a role string.

Run and inspect the migration separately when operating a controlled upgrade:

```bash
docker compose -f deploy/compose.yaml run --rm migrate
docker compose -f deploy/compose.yaml run --rm bootstrap
docker compose -f deploy/compose.yaml up -d api worker web
```

For the revision-fenced ingestion upgrade, keep
`OPENRAG_INGEST_REVISION_PROTOCOL_V2_ENABLED=false` and use this order:

1. stop new uploads, drain both Celery queues, and stop every old worker;
2. deploy the new API and worker images and run the database migration;
3. start only new workers and verify no old worker remains connected;
4. set `OPENRAG_INGEST_REVISION_PROTOCOL_V2_ENABLED=true`, then restart the
   API and workers before accepting uploads again.

While the flag is false, revision-1 uploads use rolling-compatible one-argument
tasks; retries and stale-attempt recovery remain disabled. New workers can also
consume a residual one-argument legacy task as revision 1.
The database rejects lifecycle writes from an accidentally retained old worker,
and retrieval excludes its non-approved vectors. After cutover, retrying an
exact-Legacy processing item is allowed only after all unfinished work is older
than `OPENRAG_STALE_INGEST_RECOVERY_SECONDS` (default 900). Recovery locks the
jobs, advances the revision fence, terminalizes the stale jobs, and dispatches
a fresh v2 attempt. A recent attempt fails with a conflict and is not stolen.

Rollback warning: downgrading to `4f2e1c9a7b30` is intentionally lossy. Custom
roles, multiple role bindings, workspace-scoped bindings, and capability detail
cannot be represented by the legacy three-role schema. The downgrade maps a
platform superadmin to `superadmin`, an organization-wide Administrator to
`admin`, and every other user to least-privileged `user`. Never downgrade a
production database without a restore point and an explicit data-loss decision.

```bash
docker compose -f deploy/compose.yaml run --rm migrate \
  /app/.venv/bin/alembic downgrade 4f2e1c9a7b30
```

After migration, use **Users** to manage accounts and **Roles** to manage
capabilities. Invitation requests deliberately return only a generic accepted
response. The raw one-time token is never returned to an administrator; an
out-of-band email/worker delivery adapter is still required before invitations
can be completed in a production deployment.

Follow application logs:

```bash
docker compose -f deploy/compose.yaml logs -f api worker event-worker event-scheduler web
```

Restart only the application processes without deleting stored data:

```bash
docker compose -f deploy/compose.yaml restart api worker event-worker event-scheduler web
```

Upgrade from the public `main` branch:

```bash
git pull --ff-only
docker compose -f deploy/compose.yaml up -d --build
```

Stop the stack while retaining PostgreSQL, Qdrant, MinIO, and key volumes:

```bash
docker compose -f deploy/compose.yaml down
```

Back up the `openrag_kekdata` volume together with PostgreSQL. Provider credentials are encrypted with that key-encryption key; losing it, or restoring a database with a different KEK volume, makes the stored credentials intentionally undecryptable. Never use `docker compose down -v` on an installation whose data must be retained.

Application ports bind to loopback by default. Set `OPENRAG_WEB_PORT` and `OPENRAG_API_PORT` in `.env` to change host ports. Put a TLS reverse proxy or ingress in front of OpenRAG before exposing it outside the host.

### Production and 500 GB deployments

The Compose topology is not a production proof for a 500 GB corpus. A large enterprise deployment requires external or highly available PostgreSQL and object storage, a distributed Qdrant cluster with planned sharding and replication, multiple ingestion workers, monitored Redis, backups and restore drills, TLS, metrics, traces, capacity tests, and an explicit embedding migration strategy. Those production artifacts will be documented separately under `docs/superpowers/gaps` after the enterprise architecture is approved.

## Local source development

Prerequisites: Docker, Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 20+, and Corepack.

Start only the infrastructure:

```bash
docker compose -f deploy/compose.yaml up -d postgres redis qdrant minio litellm
```

Prepare and start the backend:

```bash
cd backend
uv sync
uv run alembic upgrade head
OPENRAG_BOOTSTRAP_EMAIL=root@openrag.internal \
OPENRAG_BOOTSTRAP_PASSWORD=changeme123 \
  uv run python -m openrag.bootstrap
OPENRAG_EMBEDDING_BACKEND=hash \
  uv run uvicorn --factory openrag.api.app:create_app --port 8000
```

Start the ingestion worker in a second terminal from `backend/`:

```bash
OPENRAG_EMBEDDING_BACKEND=hash \
  uv run celery -A openrag.worker.celery_app:celery_app \
  worker -Q interactive,default -l info
```

On macOS, add `--pool=solo --concurrency=1` to the worker command. Celery's prefork pool can abort when document-parsing libraries initialize macOS frameworks inside a forked child.

Start the frontend in a third terminal:

```bash
cd frontend
corepack pnpm install
corepack pnpm generate:api
corepack pnpm dev
```

Open [http://localhost:5173](http://localhost:5173). During local development, sign in with:

- Email: `root@openrag.internal`
- Password: `changeme123`

These are development-only bootstrap credentials. Change them for any shared or internet-accessible deployment.

Useful service URLs:

| Service | URL |
|---|---|
| OpenRAG web app | <http://localhost:5173> |
| API documentation | <http://localhost:8000/api/docs> |
| API liveness / readiness | <http://localhost:8000/healthz> / <http://localhost:8000/readyz> |
| MinIO console | <http://localhost:59001> |

## Verification

```bash
# Backend
cd backend
uv run pytest -q
OPENRAG_RUN_LOAD_TESTS=1 uv run pytest tests/load -q
uv run ruff check src tests
uv run mypy src/openrag
uv run lint-imports
uv run alembic check

# Frontend
cd frontend
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm test -- --run
corepack pnpm build
corepack pnpm e2e  # RBAC cases always run; the live RAG journey needs E2E=1
```

The RBAC browser suite starts an isolated Vite server when `E2E_BASE_URL` is not
set. `frontend/e2e/rbac.spec.ts` deliberately intercepts `/api` with deterministic
fixtures. It verifies frontend navigation, route guards, and presentation for an
organization Administrator, Engineer, workspace-scoped HSE Manager, and platform
superadmin. It does **not** prove live login, catalog enforcement, API denial,
workspace isolation, or logout. Frontend guards are a convenience, not a
security boundary.

The authoritative live RBAC smoke is the credential-safe backend script. Start
the standard Compose stack first, then run it from `backend/`:

```bash
docker compose -f deploy/compose.yaml up -d
cd backend
uv run python scripts/rbac_compose_smoke.py
```

The script defaults to `http://127.0.0.1:8000`. For a custom loopback API port,
set `OPENRAG_SMOKE_API_URL`; an explicitly remote target must use HTTPS:

```bash
OPENRAG_SMOKE_API_URL="http://127.0.0.1:${OPENRAG_API_PORT}" \
  uv run python scripts/rbac_compose_smoke.py
```

It obtains the existing bootstrap credentials from the standard
`openrag-bootstrap-1` container without printing them, sends bearer tokens only
in authorization headers, and reports fixed PASS/status labels. In one
PostgreSQL transaction it creates a random temporary Engineer, two random
workspaces, and membership in exactly one of them. It then proves health,
readiness, the non-assignable platform boundary, exact 403 administration
denials, workspace isolation, and logout/refresh behavior against the real API.
The `finally` cleanup locks and validates the immutable fixture user plus both
exact organization/workspace ID/name records before deleting anything. A
mismatch fails closed and rolls the transaction back; cleanup never infers
ownership from a name. The script never requires a model provider or spends
model tokens.

The deterministic durable-stream load gate is opt-in so the normal unit suite
stays fast. It drives 100 streams concurrently, reconnects half from a real
event cursor, cancels ten midstream, and verifies ordering, deduplication,
retention bounds, and workspace envelope isolation:

```bash
cd backend
OPENRAG_RUN_LOAD_TESTS=1 uv run pytest tests/load/test_100_run_streams.py -q
```

After the full Compose stack is ready, run the live run-event smoke with
credentials supplied only through the process environment. It validates
readiness, login, workspace/chat creation, idempotent acceptance, Redis-backed
SSE ordering, worker cancellation, and—when a second user is supplied—tenant
denial. It prints fixed stage names and never prints tokens, passwords, or
response bodies:

```bash
cd backend
OPENRAG_SMOKE_EMAIL="$OPENRAG_BOOTSTRAP_EMAIL" \
OPENRAG_SMOKE_PASSWORD="$OPENRAG_BOOTSTRAP_PASSWORD" \
  uv run python scripts/smoke_run_events.py
```

For the optional tenant-denial assertion, also set
`OPENRAG_SMOKE_SECOND_EMAIL` and `OPENRAG_SMOKE_SECOND_PASSWORD`. A remote
`OPENRAG_SMOKE_API_URL` must use HTTPS; plain HTTP is accepted only for
loopback development.

The automated API and isolation selections used for the RBAC handoff are:

```bash
cd backend
uv run pytest -q \
  tests/api/test_roles.py \
  tests/api/test_users.py \
  tests/api/test_auth_routes.py \
  tests/api/test_workspaces.py \
  tests/isolation/test_rbac_isolation.py
```

For the real-stack browser journey, configure a completion model or provide `E2E_OPENAI_API_KEY`, keep the API, worker, and frontend running, then run:

```bash
E2E=1 \
E2E_EMAIL=root@openrag.internal \
E2E_PASSWORD=changeme123 \
E2E_OPENAI_API_KEY=sk-... \
  corepack pnpm e2e
```

## Roadmap

- **Phase 1 — Foundation:** Authentication, organizations, workspaces, document ingestion, hybrid retrieval, streaming chat with citations, model registration, and core administration.
- **Phase 2 — Enterprise controls:** SSO, document ACLs, model capability checks, budgets, audit views, and usage dashboards.
- **Phase 3 — Agentic retrieval and polish:** Multi-step retrieval, reranking, attachments, richer output, exports, and quality safeguards.
- **Phase 4 — Scale and ecosystem:** Connectors, evaluation tooling, deployment bundles, integrations, document viewing, and white-label support.

## Documentation

- [Product requirements](docs/prd.md)
- [Engineering foundation](docs/superpowers/specs/2026-07-18-openrag-engineering-foundation-design.md)
- [Phase 1 design](docs/superpowers/specs/2026-07-18-openrag-phase1-design.md)
- [Frontend theme](docs/superpowers/specs/2026-07-18-openrag-frontend-theme-design.md)
- [Architecture decisions](docs/adr)
- [Implementation plans](docs/superpowers/plans)
