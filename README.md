# OpenRAG

OpenRAG is an open, self-hosted platform for building secure AI assistants over private business knowledge. It brings document ingestion, hybrid retrieval, model choice, citations, access control, and administration into one system that can run in the cloud, on private infrastructure, or fully on-premises.

OpenRAG is under active development. The repository currently includes a working FastAPI backend, React frontend, authentication and invitations, multi-tenant workspaces, asynchronous document ingestion, hybrid retrieval, streamed cited chat, and model/user administration.

## OpenAI Build Week submission

**Track:** Work & Productivity

**Repository:** <https://github.com/marketcalls/openrag>
**Supported judge platform:** Docker Desktop or Docker Engine with Compose v2 on
macOS or Linux (Windows through WSL2)

OpenRAG turns private company documents into a governed, agentic knowledge
assistant. The submitted build uses GPT-5.6 through the in-process LiteLLM
Python library and was built collaboratively with Codex during the July 13–21,
2026 submission period. It demonstrates streamed answers, follow-up context,
hybrid retrieval, OCR ingestion, page-level citations, safe analytical tables
and charts, configurable completion and embedding models, RBAC, token budgets,
and RAG operations/evaluation views.

### Hosted demo instance (fastest path for judges)

A live instance is running for judging and testing, with no local setup
required:

- **URL:** <https://ragdemo.openalgo.in>
- **Email:** `demo@openalgo.in`
- **Password:** `DemoOpen1234#`

This is a shared demo account, not an isolated sandbox per judge — please
avoid deleting other judges' workspaces or documents. A tested LiteLLM model
is already configured, so no API key is required. Once signed in, create a
workspace, upload
[`frontend/e2e/fixtures/sample.pdf`](frontend/e2e/fixtures/sample.pdf), wait
for **Awaiting approval**, approve it, and ask: “What is the internal launch
codename for the OpenRAG payroll project?” The grounded answer is
`ZEBRA-COMET-7` with a page-level citation. You can also explore an existing
demo workspace instead of uploading another copy.
The instance stays available, free of charge and without restriction, through
the end of the Judging Period.

The instance is deployed with [`install/install.sh`](install/install.sh),
which stands up the same Docker Compose stack described below behind nginx
and Let's Encrypt on a single Ubuntu host. Run it from a clone of this
repository to reproduce the deployment on another domain:

```bash
sudo ./install/install.sh \
  --domain ragdemo.openalgo.in \
  --admin-email demo@openalgo.in \
  --admin-password 'DemoOpen1234#'
```

These intentionally public credentials are only for the hackathon VPS demo.
Use a unique administrator email and a long randomly generated password for any
private or production deployment, and rotate the demo password after judging.

It installs Docker if missing, starts the stack with the semantic `tei`
embedding profile, writes an nginx site for the given domain, and requests a
TLS certificate with certbot. Run `./install/install.sh --help` for all
options (including `--skip-nginx` / `--skip-tls` for environments that
already terminate TLS elsewhere).

### Judge quick start

This is the shortest clean-machine path. It does not require Python, Node, or a
locally installed database—only Git, Docker, and an OpenAI API key with access
to the GPT-5.6 model used for judging.

```bash
git clone https://github.com/marketcalls/openrag.git
cd openrag
cp .env.example .env
install -d -m 700 data
openssl rand -hex -out data/event_redis_password 32
chmod 600 data/event_redis_password
docker compose -f deploy/compose.yaml up -d --build
docker compose -f deploy/compose.yaml ps
curl --fail http://localhost:8000/readyz
```

On macOS, if `install` is unavailable, use `mkdir -p data`, create the password
file with `openssl rand -hex 32 > data/event_redis_password`, and then run
`chmod 600 data/event_redis_password`.

Open <http://localhost:5173> and sign in with the development judge account:

- **Email:** `root@openrag.internal`
- **Password:** `changeme123`

Then complete this one-time setup in the UI:

1. Open **Models**, choose **Add model**, select **OpenAI via LiteLLM**, enter a
   display name, the GPT-5.6 model ID available to the judge account (the demo
   build uses `gpt-5.6-luna`), and the API key. The key is write-only and
   envelope-encrypted. Wait for the live capability probe to pass.
2. Open the workspace switcher, choose **New workspace**, then use the adjacent
   settings button to select the model as the workspace default.
3. Open **Documents** and upload
   [`frontend/e2e/fixtures/sample.pdf`](frontend/e2e/fixtures/sample.pdf). Wait
   for **Awaiting approval**, choose **Approve**, and wait for **Indexed**.
4. Open **Chat** and ask: `What is the internal launch codename?` The answer
   should stream and cite the uploaded PDF. Ask `Put that in a table` to show
   follow-up context and safe structured presentation.
5. Open **Users → Token budget** to set organization and per-user monthly token
   allocations; the chat header now reports real prompt + completion usage.

If any service is not healthy, the fastest diagnosis is:

```bash
docker compose -f deploy/compose.yaml logs --tail=200 \
  migrate bootstrap api run-worker ingestion-worker event-worker web
```

No provider secret is bundled in the repository or image. Judges may remove the
stack with `docker compose -f deploy/compose.yaml down`; add `-v` only if all
local evaluation data may be deleted.

### How Codex and GPT-5.6 were used

The product direction and final decisions came from the project owner: rename
the product to OpenRAG, use only the LiteLLM library for model access, enforce
grounded citations and enterprise permissions, and prioritize a polished,
testable self-hosted workflow. Codex accelerated implementation by:

- comparing local RAGFlow, AnythingLLM, OpenUI, and earlier RAGHub patterns;
- building the async ingestion, durable streaming, follow-up memory, retrieval,
  model catalog/probing, evaluation, analytics, RBAC, and quota slices;
- tracing live PostgreSQL, Redis, Qdrant, worker, and browser failures to root
  causes and adding regression tests before each fix; and
- continuously running focused backend/frontend tests, type checks, lint,
  migrations, and live health/smoke checks before reviewed commits were pushed.

GPT-5.6 is both the featured completion model in the demo and the model used
with Codex for the core build session. Dated commits on July 18–21 distinguish
the hackathon work. The Devpost submission must also include the `/feedback`
Codex Session ID from that primary build thread, plus a public YouTube demo under
three minutes with audio explaining both Codex and GPT-5.6 usage.

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
- Fail-closed, asynchronous LiteLLM probes measure streaming, structured output,
  tool calling, vision, context metadata, and verifier eligibility before use
- Encrypted secret storage, quotas, budgets, usage reporting, and audit logs
- Versioned golden datasets with deterministic metrics, optional verifier
  judging, and budgeted on-demand or recurring evaluation runs
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

## Grounded analytical artifacts

When a request explicitly asks for analysis, comparison, metrics, a chart, or a
table, OpenRAG first produces the same citation-validated grounded Markdown
answer used by normal RAG. Only after that answer passes the evidence and
citation contract may a model with a passed `structured JSON` capability probe
perform one bounded, non-streaming presentation call. The presentation call is
supplemental: it can never add evidence, convert an insufficient answer into a
successful answer, or replace the authoritative Markdown response.

The only accepted contract is `analytics.v1`. It allows:

- up to 8 source-bound KPI cards;
- up to 12 total bar charts, line charts, semantic tables, and Markdown
  explainers;
- up to 50 chart categories, 8 series, 200 table rows, 12 columns, and 5
  suggested follow-ups; and
- a maximum serialized size of 49,152 UTF-8 bytes.

Every KPI and block must cite one or more markers from the persisted citation
snapshot. Unknown fields or component kinds, invalid markers, non-finite
numbers, malformed table/chart shapes, HTML, scripts, URLs, executable
expressions, control characters, and oversized values fail closed. Composition
failure leaves the grounded answer intact and simply omits the analytical view.

Artifacts are content-addressed, immutable, tenant scoped, persisted atomically
with their assistant message, replayed through the durable event stream, and
returned with historical chats. The browser reparses the contract before every
render and uses a closed React component registry—never generated code. Charts
include a screen-reader data table. Table CSV exports use RFC 4180 quoting and
neutralize spreadsheet formulas; JSON export contains only the validated
`analytics.v1` object. Export files are created locally in the browser.

The extra model call is capped at 4,096 output tokens and 45 seconds, uses no
tools or history, and is available only when the selected LiteLLM model has a
passed structured-output probe. Operational telemetry records bounded timing,
usage, state, and safe error codes; prompts, evidence text, artifact contents,
credentials, and provider errors are excluded from logs and traces.

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

Docker Compose is the supported single-node evaluation and development deployment. It starts PostgreSQL, isolated broker and durable event Redis services, Qdrant, MinIO, database migrations, bootstrap, the FastAPI service, independently scalable ingestion, run, summary, evaluation, model-probe, event, and enrichment workers, the event scheduler, and the React web application. Completion and hosted-embedding calls run through the LiteLLM Python library inside bounded application workers; no LiteLLM Proxy service or master key is required.

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
docker compose -f deploy/compose.yaml logs --tail=200 \
  api worker ingestion-worker run-worker summary-worker enrichment-worker model-worker \
  event-worker event-scheduler migrate bootstrap web
```

### 3. Sign in and configure a model

Open [http://localhost:5173](http://localhost:5173) and use the bootstrap email and password from `.env`. With the unchanged development defaults, they are:

- Email: `root@openrag.internal`
- Password: `changeme123`

Go to **Superadmin → Models**, register a hosted or local completion model and its write-only credential, and wait for its measured probe to pass. Designate one measured model as the utility model for bounded background AI work. Then create a workspace, select its default model, and optionally enable **Enrich approved documents** in workspace settings. Enrichment runs asynchronously and never holds ingestion in **Processing**. Upload a document, wait until its status is **Indexed**, and start a chat.

### 4. Choose an embedding mode

The default `hash` embedder avoids a model download and is intended only for installation smoke tests. It is lexical rather than semantic, so generic questions such as “tell me about the invoice” may not match invoice text reliably.

For real document question answering, start the optional local ML profile with Text Embeddings Inference and BGE-M3:

```bash
OPENRAG_EMBEDDING_BACKEND=tei \
docker compose -f deploy/compose.yaml --profile ml up -d --build
```

The first start downloads model images and weights. For governed changes, use **Superadmin → Embeddings** to register an immutable embedding profile, test it, build a new versioned generation, and activate it only after the complete approved corpus is indexed. Do not switch a populated production workspace by changing only an environment variable. Activation is an atomic cutover; background enrichment automatically backfills the newly active generation.

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
docker compose -f deploy/compose.yaml -f deploy/compose.local.yaml up -d \
  postgres redis qdrant minio event-redis
```

Prepare the backend, then export one shared local runtime profile for the API
and every worker. Do not run the API with the TEI default while running workers
with the hash embedder; query and document vectors must come from the same
immutable embedding profile.

```bash
cd backend
uv sync
uv run alembic upgrade head
OPENRAG_BOOTSTRAP_EMAIL=root@openrag.internal \
OPENRAG_BOOTSTRAP_PASSWORD=changeme123 \
  uv run python -m openrag.bootstrap

export OPENRAG_ENVIRONMENT=dev
export OPENRAG_EMBEDDING_BACKEND=hash
export OPENRAG_EMBEDDING_MODEL_ID=openrag-hash-v1
export OPENRAG_EMBEDDING_DIM=1024
export OPENRAG_EVENT_REDIS_URL=redis://openrag@127.0.0.1:56380/0
export OPENRAG_EVENT_REDIS_PASSWORD_FILE=../data/event_redis_password

uv run uvicorn --factory openrag.api.app:create_app --port 8000
```

From `backend/`, start the durable event, run, and ingestion workers in three
additional terminals. The solo pool is deliberate for the 4 GB Docker Desktop
development profile and for macOS document-parser safety.

```bash
uv run celery -A openrag.worker.celery_app:celery_app worker \
  -Q events -l warning --pool=solo --concurrency=1 \
  --hostname=event-local@%h --without-gossip --without-mingle

uv run celery -A openrag.worker.celery_app:celery_app worker \
  -Q runs -l warning --pool=solo --concurrency=1 \
  --hostname=runs-local@%h --without-gossip --without-mingle

uv run celery -A openrag.worker.celery_app:celery_app worker \
  -Q ingestion -l warning --pool=solo --concurrency=1 \
  --hostname=ingestion-local@%h --without-gossip --without-mingle
```

Use one memory-efficient threaded worker for the remaining local queues, then
start the scheduler in another terminal:

```bash
uv run celery -A openrag.worker.celery_app:celery_app worker \
  -Q models,evaluations,enrichment,summaries,interactive,default \
  -l warning --pool=threads --concurrency=8 \
  --hostname=aux-local@%h --without-gossip --without-mingle

uv run celery -A openrag.worker.celery_app:celery_app beat -l warning
```

If `run-worker` is absent, a new chat can persist the user question but remain
in a pending state indefinitely. If the ingestion worker or scheduler is
absent, uploads remain queued. For a source checkout, keep the scheduler and
workers on the host together; mixing a host scheduler with Docker workers can
introduce clock skew and expired Celery ticks. Before building images, keep at
least 10 GiB of free host disk; do not solve a space issue with
`docker compose down -v`, because that deletes application data.

New or reconfigured models remain unavailable until their measured LiteLLM
probe passes. The provider key is decrypted only inside the probe worker and is
never returned by the API or stored in probe results.

Start the frontend in another terminal:

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
