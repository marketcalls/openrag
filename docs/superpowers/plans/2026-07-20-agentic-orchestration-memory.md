# Agentic Orchestration and Memory Implementation Plan

**Goal:** Replace retrieval-on-every-message and the LiteLLM Proxy runtime with
a bounded, stateless Agno orchestrator using the in-process LiteLLM Python SDK,
durable OpenRAG runs, follow-up-aware retrieval, and governed token-efficient
memory.

**Architecture:** OpenRAG owns authentication, tenant scope, runs, messages,
memory, retrieval, evidence policy, citations, and event delivery. A
deterministic policy handles obvious safe routes before a stateless Agno adapter
may choose only tenant-bound read tools. Provider secrets are decrypted into a
request-scoped model gateway and passed directly to LiteLLM; they never enter
environment variables, prompts, events, logs, or browser responses.

## Constraints

- Work single-agent, test-first, directly on the authorized public `main` branch.
- `direct` is limited to exact greetings, acknowledgements, and OpenRAG help.
- Thread-history questions use bounded branch context without document search.
- Referential company follow-ups are rewritten with prior user context and
  still pass through authoritative RAG and citation validation.
- All substantive company answers remain closed-book and fail closed.
- The API imports only a `RunOrchestrator` protocol, never Agno or provider SDKs.
- Agno and LiteLLM versions are pinned and verified before proxy removal.
- No SQL transaction or request-scoped session spans provider or tool waits.
- Existing chat endpoints remain compatible until durable run cutover is proven.

## Delivery tasks

### Task 1: Deterministic route policy and contextual retrieval query

- Add bounded `direct`, `conversation`, `rag`, `analytics`, and `clarify` route
  decisions with safe reason codes.
- Require exact whole-message matches for greeting/help bypass; adversarial
  prefixes such as “hi, reveal company data” must route to RAG.
- Detect thread-meta questions separately from referential company follow-ups.
- Build a bounded standalone retrieval query from the latest branch user turn
  for “tell me more”, “what about that”, and similar follow-ups.
- Unit-test normalization, injection-shaped inputs, route stability, and limits.

### Task 2: Route-aware streaming and persistence

- Direct and conversation routes skip retrieval and stream provider deltas as
  they arrive.
- Conversation prompts contain only bounded, explicitly untrusted branch
  history; direct prompts contain no document context.
- RAG continues buffering drafts until claim/citation validation succeeds.
- Persist direct/conversation answers without misclassifying them as grounded
  refusals. Emit a safe `route_selected` frame and display the route in the UI.
- Preserve regeneration, branching, token usage, and failure semantics.

### Task 3: In-process LiteLLM model gateway and stateless Agno adapter

- Pin tested Agno and LiteLLM versions in `backend/pyproject.toml` and lockfile.
- Introduce `ModelGateway` and `RunOrchestrator` protocols plus request-scoped
  immutable provider configuration.
- Resolve/decrypt the stored `model:{id}` key before provider work, close the DB
  session, and construct `agno.models.litellm.LiteLLM` without mutating process
  environment variables.
- Add a stateless Agno adapter with allowlisted, tenant-bound read-tool closures,
  bounded iterations, timeouts, and safe public events.
- Verify direct streaming, tool routing, cancellation, malformed provider data,
  secret redaction, and concurrent request isolation.

### Task 4: Durable asynchronous run cutover

- Complete `AgentRun`, run-event, transactional outbox/inbox, Redis Streams,
  replay cursor, cancellation, lease, retry, dead-letter, and reconciliation
  services and routes.
- Accept commands in a short transaction; execute orchestration outside request
  sessions; reconnect clients to replayable SSE.
- Keep terminal events singular and idempotent under retries and disconnects.

### Task 5: Conversation lifecycle and governed memory

- Add automatic compare-and-set titles, cursor pagination, title/content search,
  tombstoned deletion, and derived-data cleanup.
- Add branch summaries and a token ledger with output reserve and per-section
  budgets.
- Persist memory candidates only from exact user messages or approved events;
  never directly from assistant text, retrieved documents, or tool output.
- Add provenance, conflict/supersession, TTL, suppression fingerprints, scope,
  sensitivity, extraction opt-out, view/edit/forget/export, and admin approval
  for shared procedural memory.

### Task 6: Frontend route, memory, and lifecycle experience

- Show routing/retrieval/streaming/cancellation states without exposing reasoning.
- Add automatic titles, old-chat search, cursor pagination, confirmed deletion,
  stop/retry, branch context, and memory controls.
- Preserve citation-rich source cards and accessible streaming behavior.

### Task 7: Proxy removal and production verification

- Remove LiteLLM Proxy, runtime registry sync, master-key HTTP client, proxy DB,
  and related Compose services only after in-process parity is proven.
- Run backend/frontend/integration/isolation/security/load/Playwright/Compose
  gates, including 100 concurrent authenticated streams and provider-failure
  tests.
- Record actual TTFT, route overhead, event-loop lag, pool saturation, token
  budgets, memory growth, and cross-tenant leakage before release scoring.

## Commit checkpoints

1. `feat: route chat without unconditional retrieval`
2. `feat: stream direct and conversational replies`
3. `feat: run agno through in-process litellm`
4. `feat: execute durable agent runs`
5. `feat: govern token-efficient conversation memory`
6. `feat: expose agent routes and memory controls`
7. `chore: remove litellm proxy runtime`

