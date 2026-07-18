# OpenRAG

OpenRAG is an open, self-hosted platform for building secure AI assistants over private business knowledge. It brings document ingestion, hybrid retrieval, model choice, citations, access control, and administration into one system that can run in the cloud, on private infrastructure, or fully on-premises.

OpenRAG is under active development. The repository currently includes a working FastAPI backend, React frontend, authentication and invitations, multi-tenant workspaces, asynchronous document ingestion, hybrid retrieval, streamed cited chat, and model/user administration.

## Why OpenRAG

Businesses want to use generative AI with internal documents, but production deployments must solve more than document search. They need to protect sensitive data, respect user permissions, avoid dependence on one model provider, operate at large corpus sizes, and produce answers that people can verify.

OpenRAG is designed around those needs:

- **Self-hosted and privacy-first:** Run every component inside your own infrastructure, including language models, embeddings, reranking, storage, and observability.
- **Model-agnostic:** Use hosted providers or local models through a single LiteLLM gateway, with support for OpenAI-compatible endpoints.
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
| Model gateway | LiteLLM Proxy |
| Embeddings | Text Embeddings Inference with BGE-M3 |
| Document parsing | Docling |
| Observability | OpenTelemetry, Prometheus, and Grafana |

The design follows five foundational rules: tenant filtering has one enforced path per data store, document permissions are applied inside retrieval, secrets have one controlled decryption path, authorization is declared at API boundaries, and all document content and model output are treated as untrusted data.

See the [high-level architecture](docs/architecture.md) for service boundaries and ingestion/query flows.

## Deploy with Docker Compose

Docker Compose is the supported single-node evaluation and development deployment. It starts PostgreSQL, Redis, Qdrant, MinIO, the model gateway, database migrations, bootstrap, the FastAPI service, an ingestion worker, and the React web application.

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
LITELLM_MASTER_KEY=replace-with-a-long-random-key
```

The bootstrap credentials create the first superadmin only when one does not already exist. Changing them later does not change an existing account password.

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
docker compose -f deploy/compose.yaml logs --tail=200 api worker migrate bootstrap web
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

Follow application logs:

```bash
docker compose -f deploy/compose.yaml logs -f api worker web
```

Restart only the application processes without deleting stored data:

```bash
docker compose -f deploy/compose.yaml restart api worker web
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
| LiteLLM proxy | <http://localhost:54000> |

## Verification

```bash
# Backend
cd backend
uv run pytest
uv run ruff check .
uv run mypy src
uv run lint-imports

# Frontend
cd frontend
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm test
corepack pnpm build
corepack pnpm e2e  # skips unless E2E=1
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
