# OpenRAG Phase 1 Design — Foundation

**Date:** 2026-07-18
**Status:** Approved
**Inherits:** `2026-07-18-openrag-engineering-foundation-design.md` (architecture, security rules, standards) and `2026-07-18-openrag-frontend-theme-design.md` (design tokens, components). Product context: `docs/prd.md` §7 Phase 1.

---

## 1. Scope

### In

- **Auth:** email + password (Argon2id), JWT access (15 min) + rotating refresh tokens (httpOnly cookie), invite-based onboarding with expiring links (AUTH-1, AUTH-6).
- **Tenancy:** organizations, workspaces, three built-in roles (Superadmin, Admin, User), `TenantContext` dependency enforcement (RBAC-1, RBAC-3, RBAC-4).
- **Documents:** upload PDF/DOCX/XLSX/CSV/TXT/MD; async Celery pipeline parse → chunk → embed → upsert; per-document job status; deletion propagation; content-hash dedup; embedding-model lock per workspace (DOC-1, DOC-2, DOC-7, DOC-8, DOC-10).
- **Retrieval:** hybrid dense + sparse with RRF fusion in Qdrant, tenant + workspace must-filters in the single retrieval code path; workspace-tunable `min_score` no-answer threshold (CHAT-2 minus reranker, CHAT-9 basic).
- **Chat:** SSE streaming with typed events, inline `[n]` citations + source panel, persistent conversation history, rolling context budget with oldest-turn summarization (CHAT-1 minus agent events, CHAT-4, CHAT-6, CHAT-7).
- **Message controls (ChatGPT/Claude.ai parity):** actions row on every message — copy text on all messages; **edit-and-resend on user messages** (in-place textarea with Cancel/Send) creating a sibling version with `< n/n >` navigation between versions, each version keeping its own downstream answers; **regenerate** on assistant messages creating a sibling answer under the same navigation. Thumbs up/down capture stays with CHAT-10 in a later phase.
- **Models:** LiteLLM Proxy as the sole gateway. **Superadmin model registry CRUD: add, edit, disable, remove any model** — hosted (OpenAI in Phase 1's tested path), Ollama, or any OpenAI-compatible base URL. Config synced to LiteLLM via its management API. Per-workspace default model (MODEL-1 subset, MODEL-4 subset, MODEL-9 basic).
- **Secrets (deviation from roadmap, required by iron rule 3):** minimal secrets module — envelope encryption (AES-256-GCM), KEK from keyfile, write-only API, fingerprint display. Provider keys never in `.env` (SEC-1, SEC-2, SEC-5).
- **Audit (deviation from roadmap):** append-only `audit_events` written from auth, documents, models, and admin services from day one. Viewer UI is Phase 2 (AUD-1 write path).
- **Frontend:** themed shell + pages: Chat, Documents, Admin › Users, Superadmin › Models, Login/Invite flows.
- **Deployment:** Docker Compose only (app, worker, Postgres, Qdrant, Redis, MinIO, LiteLLM, TEI; Ollama optional profile).

### Out (Phase 2+)

SSO, MFA, API keys, session management UI, document ACLs, groups, custom roles, quotas/budgets/usage dashboards, reranker, agentic mode, per-chat attachments, connectors, OCR, exports, prompt templates, model catalog sync, capability probing, aggregator templates, webhooks, MCP, Helm charts, air-gapped bundle, audit viewer UI, white-label settings.

### Fixed choices

| Decision | Choice | Note |
|---|---|---|
| Hosted provider tested in Phase 1 | OpenAI | Ollama also first-class; other providers arrive via registry with Phase 2 probing |
| Hybrid retrieval implementation | TEI dense (bge-m3) + FastEmbed sparse (BM25 family), Qdrant native hybrid query with RRF | True bge-m3 learned-sparse is a Phase 3+ swap isolated inside `retrieval/`; recorded as ADR-0002 |
| Reranker | None in Phase 1 | Fusion order only; arrives Phase 3 per roadmap |

---

## 2. Data Model

### 2.1 Postgres (all org-owned tables carry `org_id`; access via `TenantContext` only)

```
organizations(id, name, created_at)
users(id, org_id, email, password_hash, role, active, created_at)
invitations(id, org_id, email, role, token_hash, expires_at, accepted_at)
refresh_tokens(id, user_id, token_hash, expires_at, revoked_at)

workspaces(id, org_id, name, default_model_id, embedding_model,
           min_score, created_at)            -- embedding_model locked after first index
workspace_members(workspace_id, user_id, role)

documents(id, org_id, workspace_id, filename, mime, size_bytes,
          content_hash, status: queued|processing|indexed|failed,
          error, storage_key, page_count, created_by, timestamps)
ingest_jobs(id, document_id, stage: parse|chunk|embed|upsert,
            progress, error, started_at, finished_at)

chats(id, org_id, workspace_id, user_id, title, created_at, updated_at)
messages(id, chat_id, parent_message_id?, sibling_index, role, content,
         model_id, prompt_tokens, completion_tokens, created_at)
         -- tree, not list: an edit inserts a new user message sharing the
         -- edited message's parent (next sibling_index); a regenerate
         -- inserts a new assistant sibling under the same user message.
         -- GET /chats/{id} returns the tree; the client renders the path
         -- that follows the newest sibling at each branch point by default,
         -- with < n/n > navigation across siblings (selection is
         -- client-side state in Phase 1, not persisted).
citations(id, message_id, document_id, chunk_ref, page, score)

models(id, litellm_model_name, display_name,
       provider_kind: openai|ollama|openai_compatible,
       base_url?, enabled, created_at)        -- superadmin CRUD
secrets(id, name, ciphertext, nonce, key_version, fingerprint, last_used_at)
audit_events(id, org_id?, actor_id, action, target_type, target_id,
             ip, created_at)                  -- append-only, no UPDATE/DELETE grants
```

### 2.2 Qdrant

- Collection `chunks_bge_m3` (one collection per embedding model, per foundation).
- Named vectors: `dense` (1024-d, cosine) + `sparse` (BM25 family via FastEmbed).
- Payload: `{tenant_id, workspace_id, document_id, page, chunk_index, text, doc_type, date, acl_groups: []}` — `acl_groups` reserved empty now so Phase 2 ACLs need no schema migration.
- Keyword indexes: `tenant_id`, `workspace_id`, `document_id`.

### 2.3 MinIO

Originals at `{org_id}/{workspace_id}/{document_id}/{filename}`. Deletion is one Celery task propagating Postgres row → MinIO object → Qdrant points (filter on `document_id`), with an audit entry (DOC-8).

---

## 3. Backend Flows

### 3.1 Auth & tenancy

- Login → access JWT (15 min) + rotating refresh (httpOnly, secure). Refresh rotation invalidates the old token; reuse of a rotated token revokes the family.
- Invitations: admin creates (email, role, expiry); accept-link → set-password → active user. Domain allowlist deferred to Phase 2.
- `TenantContext` (from `modules/tenancy/`) resolves user, org, role, workspace memberships per request; routes declare role requirements as dependencies. No inline role checks.

### 3.2 Ingestion pipeline (Celery)

`upload → MinIO put + documents row (queued) → chain:`

1. **parse** — Docling → structured blocks with page numbers; unsupported/empty → `failed` with reason.
2. **chunk** — heading-aware semantic chunking, target ~512 tokens, 15% overlap, tables kept whole with their caption context.
3. **embed** — batched: TEI (dense) + FastEmbed (sparse) in the worker.
4. **upsert** — Qdrant points with payload; `documents.status = indexed`.

Each stage updates `ingest_jobs` (live UI progress). Retries: 3× exponential backoff per stage; terminal failure records stage + reason. Priority queue: uploads < 10 MB jump the bulk queue. Same `content_hash` in a workspace short-circuits as duplicate (DOC-7).

### 3.3 Retrieval — the one code path

`retrieve(ctx: TenantContext, workspace_id, query, top_k=8)`:

1. Assert workspace ∈ `ctx.memberships` (typed `WorkspaceAccessDenied` otherwise).
2. Embed query dense (TEI) + sparse (FastEmbed).
3. Qdrant hybrid query: prefetch dense + sparse → RRF fusion, with must-filters `tenant_id == ctx.org_id` and `workspace_id == workspace_id`. **No other code constructs Qdrant filters** (iron rule 1).
4. Results below workspace `min_score` → no-answer path: respond honestly, show nearest sources (CHAT-9).

### 3.4 Chat

`POST /chats/{id}/messages` → SSE stream, typed events:

```
retrieval_started → sources(chunk refs) → token(delta)* → citations(final map) → done(usage)
```

- Prompt: system instructions + delimited data blocks (iron rule 5) numbered `[1..n]` + rolling conversation context (token budget; oldest turns summarized when exceeded).
- Model call exclusively through LiteLLM Proxy using the app's single gateway key (per-org virtual keys and budgets arrive in Phase 2 per MODEL-2/MODEL-5).
- Citations parsed from `[n]` markers, resolved to chunk refs, persisted in `citations`.

### 3.5 Models & secrets

- Superadmin CRUD on `models`; add/edit/remove/disable takes effect via an idempotent **full-config replay** to LiteLLM's management API; the same replay runs on proxy restart (SEC-4 pattern).
- Keys: `PUT /admin/secrets/{name}` (write-only) → envelope-encrypted row; decryption only inside the LiteLLM sync function; API returns fingerprint + `last_used_at` only.
- KEK source in Phase 1: keyfile path from `.env` (KMS/Vault sources are Phase 2+ additions behind the same interface).

### 3.6 Audit

`record_audit(ctx, action, target_type, target_id)` called from: login success/failure, invitation create/accept, user deactivate/role change, document upload/delete, model add/remove/disable, secret write, workspace create/member change. Append-only (DB role lacks UPDATE/DELETE on the table).

---

## 4. API Surface (`/api/v1`, OpenAPI published, frontend client generated)

```
POST /auth/login /auth/refresh /auth/logout
POST /auth/invitations            POST /auth/invitations/accept
GET  /orgs/{id}/users             POST invite | PATCH role/deactivate
GET/POST /workspaces              GET/POST /workspaces/{id}/members
POST /workspaces/{id}/documents   (multipart)
GET  /workspaces/{id}/documents   (list + status)
DELETE /documents/{id}
GET/POST /chats                   GET /chats/{id}   (returns message tree)
POST /chats/{id}/messages         → SSE   (body: content, parent_message_id?
                                           — set when editing a prior message)
POST /messages/{id}/regenerate    → SSE   (new assistant sibling)
GET/POST/PATCH/DELETE /admin/models        (superadmin)
PUT  /admin/secrets/{name}        (superadmin, write-only)
GET  /healthz  /readyz
```

Errors: RFC 9457 problem+json via the global handler (foundation §4.2).

---

## 5. Frontend Pages

| Page | Contents |
|---|---|
| **Chat** | Approved mock realized: sidebar (workspace switcher, chat list, user footer), streaming thread, citation chips + source panel, model selector in top bar. Message actions row: copy on all messages; edit-in-place (textarea with Cancel/Send) on user messages; regenerate on assistant messages; `< n/n >` sibling navigation at branch points |
| **Documents** | Drag-drop multi-file upload, live status table (Indexed/Processing/Failed pills per theme spec), delete, failure reason on click |
| **Admin › Users** | Invite (email + role), deactivate, role change, workspace membership |
| **Superadmin › Models** | Model list; add form: display name, provider kind (OpenAI / Ollama / OpenAI-compatible URL), model id, key (write-only, fingerprint after save); enable/disable/remove; gateway sync status |
| **Auth** | Login, accept-invite, set-password — minimal, themed |

Server state exclusively via TanStack Query + generated client; SSE via a typed event-stream hook. Theme tokens per the theme spec; no raw palette classes.

---

## 6. Testing & Done Criteria

### Tests

- **Isolation (every PR):** org A → org B workspace query = denied/empty; non-member workspace access denied; deleted document unretrievable after task completes.
- **Integration (testcontainers: Postgres, Qdrant, Redis, MinIO):** fixture ingestion (PDF with tables, DOCX, empty-scan → clean failure); retrieval asserts a keyword query and a semantic query each hit the right chunk in top-5.
- **Unit:** chunker boundaries, citation parsing, secrets round-trip + fingerprint, LiteLLM sync idempotency (mocked proxy — the sanctioned mock), refresh-token rotation/reuse-revocation.
- **E2E smoke (Playwright):** login → upload → Indexed → ask → streamed answer with citation chip resolving to the uploaded document.

### Phase 1 definition of done (fresh `docker compose up`)

1. Bootstrap creates superadmin; superadmin adds an OpenAI model and an Ollama model in the UI; both pass a chat round-trip; removing a model takes it out of the picker.
2. Admin invites a user; user accepts, uploads a 200-page PDF, watches queued → processing → indexed live.
3. A question answerable only from that PDF returns a streamed, correctly-cited answer with working source panel; a keyword query (e.g. "invoice 0231") also retrieves correctly (hybrid proof).
4. A second org's user sees nothing of the first org anywhere, including retrieval.
5. ~10 GB / ~50k chunks ingested locally without failure; p95 retrieval < 500 ms at that scale. (500 GB / 150 ms remain GA targets, not Phase 1 gates.)

---

## 7. New ADR

- **ADR-0002:** Hybrid retrieval in Phase 1 = TEI dense (bge-m3) + FastEmbed sparse (BM25 family) fused with RRF in Qdrant; true bge-m3 learned sparse deferred, swap isolated in `modules/retrieval/`.
