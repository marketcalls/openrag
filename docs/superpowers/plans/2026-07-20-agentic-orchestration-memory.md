# Agentic Orchestration and Memory Implementation Plan

**Goal:** Replace retrieval-on-every-message and the LiteLLM Proxy runtime with
a bounded, stateless Agno orchestrator using the in-process LiteLLM Python SDK,
durable OpenRAG runs, follow-up-aware retrieval, and governed token-efficient
memory.

**Architecture:** OpenRAG owns authentication, tenant scope, runs, messages,
memory, retrieval, evidence policy, citations, tool selection, loop limits, and
event delivery. A deterministic policy handles obvious safe routes and ordinary
single-pass RAG before an escalation-only, four-iteration loop invokes a
stateless Agno adapter with tenant-bound read tools. Provider secrets are
decrypted into a request-scoped model gateway and passed directly to LiteLLM;
they never enter environment variables, prompts, events, logs, or browser
responses.

## Constraints

- Work single-agent, test-first, directly on the authorized public `main` branch.
- `direct` is limited to exact greetings, acknowledgements, and OpenRAG help.
- Thread-history questions use bounded branch context without document search.
- Referential company follow-ups are rewritten with prior user context and
  still pass through authoritative RAG and citation validation.
- All substantive company answers remain closed-book and fail closed.
- General-knowledge fallback requires an explicit workspace policy, is labeled
  ungrounded, and never emits document citations.
- The API imports only a `RunOrchestrator` protocol, never Agno or provider SDKs.
- OpenRAG, not Agno, owns the maximum four iterations, tool schemas,
  authorization, timeouts, evidence checks, and terminal outcome.
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
- Gate chat, structured-output, verifier, reasoning, and tool capabilities before
  any provider call; evaluator selection requires chat + structured JSON +
  verifier capabilities.
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
- Bind each rolling-summary checkpoint to the active message branch and
  invalidate it when an edit or regeneration forks above the checkpoint.
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

### Task 8: Escalation, validation, and enrichment quality gates

- Keep ordinary grounded questions on the single-pass path; escalate only
  multi-part, metadata-sensitive, or weak-evidence questions to the loop.
- Run an asynchronous quality auditor for answered messages. In strict
  workspaces, allow one synchronous critique-guided regeneration before a
  validation-failed refusal.
- Add opt-in, budgeted chunk summaries, keywords, and hypothetical questions;
  derivative search points inherit all tenant/ACL/version filters and dedupe to
  the parent chunk.
- Trigger versioned evaluation runs after retrieval/profile/prompt policy
  changes and nightly, in addition to explicit on-demand runs.
- Add an independently runnable red-team tier covering injection, ACL/version
  evasion, ungrounded fallback, malformed tool calls, and citation fabrication.

## Commit checkpoints

1. `feat: route chat without unconditional retrieval`
2. `feat: stream direct and conversational replies`
3. `feat: run agno through in-process litellm`
4. `feat: execute durable agent runs`
5. `feat: govern token-efficient conversation memory`
6. `feat: expose agent routes and memory controls`
7. `chore: remove litellm proxy runtime`
8. `feat: gate agent escalation with measured quality`
