# OpenRAG Engineering Foundation

**Date:** 2026-07-18
**Status:** Approved
**Scope:** Platform-wide architecture, security model, coding standards, testing strategy, and operational rules for OpenRAG (see `docs/prd.md` for product requirements). This document is the single source of truth for engineering decisions. Per-phase design specs inherit it and may not contradict it; a contradiction means either the phase spec is wrong or this document receives a deliberate, ADR-recorded amendment.

**Primary consumer:** AI coding sessions. Rules are prescriptive and checkable. A thin `CLAUDE.md` at the repo root is distilled from this document and contains nothing not derived from it.

---

## 1. Decisions Fixed by This Document

| Decision | Choice | Rationale |
|---|---|---|
| Architecture | Modular monolith, monorepo | One repo, two deployable Python processes (API, worker) from one package; simplest to build, deploy, and reason about for a self-hosted product; services extractable later if scale demands |
| Vector store | Qdrant only in v1 | One code path for the tenant/ACL filter (the deal-killing risk); retrieval still defines a `VectorStore` protocol internally so pgvector can be added later without redesign |
| License / commercial model | Fully open source, AGPL-3.0 | Network copyleft protects against closed SaaS forks; self-hosting unmodified copies unaffected |
| Job queue | Celery + Redis | Priority queues (small interactive uploads jump bulk-load backlogs), mature retry/visibility semantics, horizontal scaling. Recorded as ADR-0001; revisitable |
| Backend stack | Python 3.12, FastAPI (async), SQLAlchemy 2.0 async, Alembic, Pydantic v2, uv, ruff, mypy --strict | Industry-standard async stack; strict typing as a correctness tool |
| Frontend stack | React, Vite, TypeScript strict, pnpm, TanStack Query, shadcn/ui + Tailwind, ESLint + Prettier | Matches PRD stack; server state exclusively via TanStack Query |
| External services | Postgres, Qdrant, Redis, MinIO, LiteLLM Proxy, TEI | All self-hostable; app processes are stateless |

---

## 2. Repository Layout

```
openrag/
├── CLAUDE.md                  # thin loader: iron rules + pointers (distilled from this doc)
├── docs/
│   ├── prd.md                 # product requirements
│   ├── adr/                   # architecture decision records (ADR-NNNN-title.md)
│   └── superpowers/specs/     # design specs (this doc, then per-phase specs)
├── backend/
│   ├── pyproject.toml         # uv-managed
│   ├── src/openrag/
│   │   ├── core/              # config, logging, errors, db session, security primitives
│   │   ├── modules/
│   │   │   ├── auth/          # identity, sessions, API keys, (later) SSO
│   │   │   ├── tenancy/       # orgs, workspaces, groups, membership; owns the org-scoping dependency
│   │   │   ├── documents/     # upload, ingestion jobs, metadata, deletion propagation
│   │   │   ├── retrieval/     # vector store client, hybrid search, rerank, ACL filter — ONE code path
│   │   │   ├── chat/          # conversations, streaming, citations, agent loop (Phase 3)
│   │   │   ├── models/        # model registry, LiteLLM sync, capability probes
│   │   │   ├── quotas/        # allocations, usage ledger, enforcement
│   │   │   ├── secrets/       # envelope encryption, KEK handling
│   │   │   └── audit/         # append-only event log
│   │   ├── api/               # FastAPI routers only — thin, delegate to modules
│   │   └── worker/            # Celery task entrypoints — thin, delegate to modules
│   └── tests/                 # mirrors src/; plus tests/isolation/ (adversarial tenancy suite)
├── frontend/
│   └── src/
│       ├── features/          # feature folders mirroring backend modules
│       ├── components/        # shared UI (shadcn/ui-based)
│       ├── lib/               # utilities
│       └── api/               # generated OpenAPI client
└── deploy/                    # compose.yaml, helm/, grafana dashboards
```

### 2.1 Module boundaries

- Each module under `modules/` exposes a small public interface: `service.py` (functions/classes other code may call) and `schemas.py` (Pydantic models). Everything else in the module is internal.
- `api/` and `worker/` may import module **services** only. Modules may import `core/` and other modules' **public services** only — never another module's internals or ORM models.
- Dependency direction: `api`/`worker` → `modules` → `core`. `core` imports nothing above it.
- Boundaries are enforced by **import-linter in CI**, not convention alone.
- Business logic lives in modules, callable identically from an API request or a Celery job. The API/worker split is a deployment detail, not an architectural one: ingestion logic is in `documents/`, not in `worker/`.
- A module has one clear purpose, is understandable without reading its internals, and its internals can change without breaking consumers. A file growing large is a signal the module is doing too much.

---

## 3. Security Model — The Five Iron Rules

Each rule has a single enforcement point in code. Violating any of these is a blocking review failure.

### Rule 1: Tenant isolation has one code path per store

- **Postgres:** every query on org-owned tables flows through the `TenantContext` dependency (owned by `modules/tenancy/`) that injects `org_id` scoping. Raw `session.query`/`select()` on org-owned tables outside that dependency is a CI failure (lint rule + review checklist).
- **Qdrant:** every search goes through one function in `modules/retrieval/` that builds the must-filter: `tenant_id` + workspace membership + ACL-group intersection with the caller's groups. No other code constructs Qdrant filters. Payload schema per PRD §6: `{tenant_id, workspace_id, document_id, acl_groups[], page, doc_type, date, ephemeral, chat_id?}` with keyword indexes on `tenant_id`, `workspace_id`, `acl_groups`.

### Rule 2: Document ACLs are enforced inside the vector query

The ACL filter is applied **in the Qdrant query itself**, never post-filtered in Python. Post-filtering leaks document existence through scores and counts, and breaks top-k semantics. An answer must never cite a document the asking user cannot open (PRD RBAC-5). `tests/isolation/` contains adversarial tests — org A querying org B's content, a user querying an ACL-restricted document — that run on every PR and fail the build.

### Rule 3: Secrets live encrypted in Postgres; one decryption path

- Envelope encryption (AES-256-GCM) per PRD SEC-1–SEC-5. The Key Encryption Key is the sole out-of-DB secret, sourced from keyfile/KMS/Vault at bootstrap. `.env` holds only DB connection + KEK source reference.
- Decryption happens in exactly one function in `modules/secrets/`, called only by the LiteLLM Proxy sync path, in memory.
- Secret API fields are write-only in Pydantic schemas (never serialized back); UI shows fingerprint + last-used only.
- Logging configuration globally redacts fields matching secret-name patterns. Secrets never appear in logs, traces, or API responses.

### Rule 4: AuthN/AuthZ are declarative, at the route boundary

- Argon2id password hashing. JWT access tokens (15-minute lifetime) + rotating refresh tokens. Session revocation supported.
- Permission checks are FastAPI dependencies declared per-route. No inline `if user.role == ...` checks in handlers.
- Rate limiting on auth and chat endpoints from day one.

### Rule 5: The LLM boundary treats documents as data and output as untrusted

- Retrieved chunks and attachment text are wrapped in delimited data blocks with a system-prompt instruction that document content is data, not instructions (PRD SAFE-1).
- Model output is untrusted for rendering: sanitized markdown only, no raw HTML injection.
- Agent tools are read-only in v1.

**Review bar:** OWASP ASVS L2 plus the OWASP LLM Top 10. Dependency scanning and container image scanning run in CI.

---

## 4. Coding Standards

### 4.1 Python (backend)

- Python 3.12. Dependencies managed with `uv`. Lint + format with `ruff`; `mypy --strict` passes in CI.
- FastAPI fully async. **No blocking I/O in request handlers.** CPU-heavy work (parsing, OCR, embedding batches) belongs in Celery workers; unavoidable blocking library calls use `run_in_executor`.
- SQLAlchemy 2.0 async. Alembic migrations are forward-only; the downgrade for the most recent migration is tested.
- Pydantic v2 at every boundary: routers accept and return schemas, never ORM objects. ORM models never cross module boundaries.
- Celery + Redis for jobs: priority queue so small interactive uploads preempt bulk loads; retries with backoff; job status persisted per document (queued / processing / indexed / failed with reason).

### 4.2 Error handling

- Each module defines typed exceptions (e.g. `DocumentNotFound`, `QuotaExceeded`, `TenantMismatch`).
- One global exception handler maps typed exceptions to RFC 9457 `application/problem+json` responses.
- No bare `except:`. No stringly-typed errors. Error responses never leak internals (stack traces, SQL, hostnames).
- Structured logging via `structlog`, JSON output; `org_id`, `user_id`, `request_id` bound automatically per request and propagated into worker jobs.

### 4.3 TypeScript (frontend)

- Vite, `strict: true`, pnpm. ESLint + Prettier in CI.
- TanStack Query for all server state — no hand-rolled fetch-in-`useEffect`.
- shadcn/ui + Tailwind as the component system; dark/light theme support structural from the start.
- The API client is **generated from the backend OpenAPI schema** in CI, so frontend and backend types cannot drift.
- Feature folders mirror backend modules (`features/chat/`, `features/documents/`, …).

### 4.4 Shared conventions

- Conventional Commits.
- Decisions that change architecture get an ADR in `docs/adr/` (`ADR-NNNN-title.md`). ADR-0001 records the Celery choice.
- CI gates, all required before merge: ruff, mypy, ESLint, tsc, unit + integration tests, import-linter, isolation suite, dependency audit, image scan.

---

## 5. Testing Strategy

| Tier | What | How |
|---|---|---|
| Unit | Module logic in isolation | Colocated per module in `tests/`, pytest + pytest-asyncio |
| Integration | Real Postgres, Qdrant, Redis via testcontainers | No mocked stores — retrieval-filter correctness on mocks is worthless |
| Isolation (adversarial) | Tenancy + ACL leakage attempts | `tests/isolation/`, distinct always-run tier, fails the build on any leak |
| Retrieval quality (eval harness) | Golden query set with expected sources, scored on hit-rate and citation precision | Deferred to its roadmap phase; this doc reserves the seam: golden-query fixtures live per workspace |
| Frontend | Component + hook tests | Vitest; E2E added when the first full user flow exists |

Coverage gate: ~80% on `modules/`; no coverage gate on glue code (`api/`, `worker/`, config).

---

## 6. Observability and Operations

- Every request carries a `request_id`, propagated into logs, traces, and Celery jobs.
- Prometheus metrics named `openrag_<module>_<metric>`; per-stage latency histograms for the RAG pipeline (embed, search, rerank, LLM first-token) because the PRD's performance targets (§4.1) are per-stage.
- OpenTelemetry tracing wired from day one — retrofitting tracing across an agent loop later is far harder.
- `/healthz` (liveness) and `/readyz` (readiness, checks real dependencies) on both API and worker processes.
- All app processes stateless; state only in Postgres, Qdrant, Redis, MinIO — each with a documented backup/restore procedure.
- Graceful degradation is a stated contract per dependency:
  - Reranker down → fall back to fusion order.
  - LLM error/timeout → configured fallback chain, event logged.
  - Redis down → quota checks fail **closed** (block), caches fail **open** (miss).
- Docker Compose is the canonical dev environment: `docker compose up` + one bootstrap command always produces a working stack. CI runs against the same images.

---

## 7. CLAUDE.md Contract

`CLAUDE.md` at the repo root is the always-loaded surface for AI sessions, ~150 lines:

1. The five iron security rules, verbatim.
2. The module map as a one-screen table.
3. Tooling commands (`uv run pytest`, `uv run ruff check`, `pnpm test`, compose commands).
4. The "never do" list: no raw Qdrant filters outside `retrieval/`; no ORM objects across module boundaries; no secrets in `.env` or logs; no blocking I/O in handlers; no `except:`; no fetch-in-`useEffect`.
5. Pointers into this document's sections for depth.

CLAUDE.md contains nothing that is not derived from this document. When this document changes, CLAUDE.md is regenerated in the same commit.

---

## 8. What This Document Does Not Cover

Per-phase designs (API shapes, schemas, UI flows, the agent loop, connectors, deployment charts) are produced as separate specs in `docs/superpowers/specs/`, starting with Phase 1 (auth + orgs + RBAC, upload → ingestion → Qdrant, single-shot hybrid RAG with streaming + citations, LiteLLM with one provider + Ollama, chat history, basic admin). Each phase spec goes through its own brainstorm → spec → plan → implementation cycle and inherits this foundation.
