# OpenRAG Enterprise Agentic Platform: Gap Analysis and Design

**Date:** 2026-07-18  
**Status:** Approved direction  
**Scope:** Conversation intelligence, agentic routing, memory, analytical presentation, observability, evaluation, and event-driven scale

## Executive summary

OpenRAG will combine the strongest product ideas from the local `anything-llm`
and `openui` reference repositories without inheriting their runtime and security
limitations.

- AnythingLLM is the benchmark for practical RAG controls: explicit chat/query/
  automatic modes, pinned documents, configurable history, similarity thresholds,
  `topN`, reranking, memory controls, and visible ingestion progress.
- OpenUI is the benchmark for presentation: progressive structured responses,
  KPI cards, charts, tables, explainers, related queries, artifacts, and typed tool
  activity.
- OpenRAG will be the authority for authentication, tenant isolation, chat trees,
  durable runs, memory governance, provenance, retention, deletion, event replay,
  evaluation, and operational telemetry.

The target is an event-driven modular application capable of at least 100
simultaneous authenticated streaming users. Agno performs orchestration through
tenant-bound tools. LiteLLM is used only as an in-process Python library. No
LiteLLM Proxy and no direct OpenAI application SDK remain in the target runtime.

## Product outcomes

The completed platform must provide:

1. Direct, streaming LLM responses for greetings and general conversation.
2. Agent-selected document retrieval only when evidence is useful or required.
3. Same-thread follow-up context, branch-aware summaries, and searchable prior
   chats.
4. Governed, token-efficient cross-session memory with user controls.
5. Automatic useful thread titles plus rename, search, pagination, and deletion.
6. Configurable completion, embedding, reranking, OCR, chunking, and retrieval
   policies at the appropriate global/workspace level.
7. Safe OpenUI-style analytical answers with charts, tables, metrics,
   explainers, citations, and follow-up actions.
8. A superadmin RAG Operations area for quality, latency, cost, capacity,
   ingestion, errors, traces, evaluations, and regressions.
9. Centralized structured logs, distributed traces, metrics, error grouping,
   alerting, redaction, and trace correlation.
10. A replayable, cancellable, backpressured event model that remains stable
    across multiple API and worker processes.
11. Verified handling of 100 sustained concurrent streams and a 200-stream spike.
12. Enterprise-safe behavior for large installations, including deployments
    managing approximately 500 GB of source documents.

## Current-state evidence

### Strengths already present

- Chats, branchable messages, citations, token counts, organizations,
  workspaces, users, models, documents, and ingestion jobs are persisted in
  PostgreSQL.
- Authentication uses bearer access tokens and HTTP-only refresh cookies.
- Retrieval applies immutable organization and workspace filters in Qdrant.
- Retrieved text is labelled as untrusted data in the server-owned prompt.
- JSON logging includes a basic sensitive-key redactor.
- Audit records cover several administrative and document operations.
- Ingestion is already separated into parse, chunk, embed, and upsert stages.
- The frontend already renders Markdown, citations, message branches, edit,
  regenerate, copy, workspaces, documents, users, and models.

### Critical gaps

- `backend/src/openrag/modules/chat/service.py` always retrieves before calling
  the LLM. A no-answer retrieval result returns early, so greetings, follow-up
  questions, and requests about prior turns never reach the model.
- `backend/src/openrag/modules/chat/models.py` has no durable run state,
  cancellation, idempotency, event sequence, context snapshot, memory link, or
  analytical artifact.
- The request-scoped SQLAlchemy session remains involved for the duration of
  retrieval and provider streaming. Default pool sizes cannot safely support
  100 long-lived streams.
- `backend/src/openrag/modules/chat/llm.py` and
  `backend/src/openrag/modules/models/sync.py` target a LiteLLM HTTP proxy,
  conflicting with the in-process-only requirement.
- HTTPX clients for LiteLLM and TEI are created per operation instead of being
  long-lived pooled clients.
- The deployed API is one Uvicorn process; the deployed Celery worker has
  concurrency one. There is no horizontally replayable run stream.
- The current SSE vocabulary lacks run IDs, sequence numbers, event versions,
  heartbeat, tool lifecycle, cancellation, artifacts, safe errors, and resume.
- Chat titles remain `New chat`; frontend chat rename/delete controls and search
  are absent even though basic backend rename/delete endpoints exist.
- Conversation context is raw ancestor history only. There are no rolling
  branch summaries, semantic memories, past-chat tools, provenance, conflict
  handling, expiry, or suppression.
- Token budgeting uses a character heuristic and has no model-aware ledger or
  reserved output/tool/schema capacity.
- Logs have no request/run/trace correlation. No metrics, traces, collector,
  centralized backend, error grouping, RAG-run facts, evaluation datasets, or
  superadmin analytics exist.
- The frontend can only render Markdown responses; it cannot persist or render a
  validated analytical response.

## Benchmark findings

### AnythingLLM patterns to adopt

Reference repository: `/Users/openalgo/openrag/anything-llm`

- Explicit `automatic`, `chat`, and `query` modes.
- Workspace-level history count, similarity threshold, `topN`, model, chat
  mode, reranking, pinned documents, and source deduplication.
- Visible memory CRUD and promotion/demotion controls.
- Small memory injection caps with relevance reranking.
- Observer/reflector separation for background memory extraction and
  consolidation.
- Isolating CPU/OOM-prone embedding from interactive web processes.
- Durable-looking state-machine semantics used by scheduled jobs.
- Request IDs, expiry, and clarification/approval interaction patterns.

### AnythingLLM patterns not to adopt

- Process-local Maps for connections, event history, workers, and agent state.
- SSE writes without drain/backpressure handling or true upstream cancellation.
- A WebSocket UUID acting as the visible authorization capability.
- Ad hoc event JSON without ordering, schema versions, replay, or guaranteed
  terminal events.
- Direct provider clients, an external LiteLLM proxy, and global environment
  provider selection.
- SQLite as the concurrency datastore or workspace slugs as vector isolation.
- Middle deletion of prompts that can remove system policy.
- Global/raw-string memories lacking provenance, confidence, expiry, conflicts,
  suppression, or tenant governance.
- Dynamic client/MCP/plugin execution as an authorization boundary.
- Logging raw prompts, tool arguments, provider errors, or model reasoning.

### OpenUI patterns to adopt

Reference repository: `/Users/openalgo/openrag/openui`

- A small explicit component registry and progressive, root-first rendering.
- Typed KPI, chart, table, explainer, callout, detailed-view, and follow-up
  components.
- Tool-call lifecycle UI with correlated IDs and collapsed safe details.
- Frame-batched streamed updates and a last-good parsed render tree.
- Responsive container layouts, mobile drawers, accessible live states, and
  chart export metadata.
- Artifact preview/detail patterns and cursor-based storage abstractions.
- Cancellation affordances and context reset when changing threads.

### OpenUI patterns not to adopt

- Direct provider SDK calls in demo backends.
- Client-supplied system prompts, wildcard CORS, localStorage/in-memory
  authority, and client-owned conversation history.
- Rendering model/tool output as arbitrary UI code.
- Browser-side generic `Query`, `Mutation`, MCP, URL, or tool execution.
- Full prompt/catalog injection when a compact OpenRAG schema is sufficient.
- Treating example thread storage, sharing, or attachment rendering as an
  enterprise backend.

## Architecture decision

### Options considered

1. **Async monolith:** keep generation inside the request handler and add more
   Uvicorn workers. This is the smallest change but still couples a browser
   connection to DB/provider resources and cannot provide durable replay or
   reliable cross-process cancellation.
2. **Event-driven modular OpenRAG:** separate command/API, stream gateway,
   agent runner, ingestion, and background consumers while sharing one codebase
   and PostgreSQL schema. Use PostgreSQL outbox plus Redis Streams. This is the
   selected option.
3. **Kafka microservices:** split every domain into an independent service now.
   This offers greater theoretical scale but introduces operational and schema
   coordination cost that is unnecessary for the initial 100-stream target.

### Selected topology

```text
Browser
  |-- authenticated commands: send, cancel, approve, clarify
  `-- authenticated replayable SSE subscription
                     |
            Async API / Stream Gateway
                     |
        short PostgreSQL transaction
    Message + AgentRun + Transactional Outbox
                     |
            Outbox Relay / Redis Streams
                     |
              Async Agent Runners
        |-- Agno stateless orchestrator
        |-- in-process LiteLLM model gateway
        |-- tenant-bound document tools
        |-- tenant-bound memory tools
        |-- tenant-bound past-chat tools
        `-- validated analytics planner
                     |
        ordered, bounded per-run event stream
                     |
         SSE Gateway + background consumers
        |-- chat/message projection
        |-- title and summary materialization
        |-- memory extraction/consolidation
        |-- past-chat indexing
        |-- usage and RAG-run projection
        |-- online/offline evaluation
        `-- telemetry and notifications
```

PostgreSQL remains authoritative. Redis Streams is a transport, work queue, and
short-lived replay buffer. The application exposes an `EventBus` boundary so a
large installation can adopt Kafka/Redpanda without changing domain services.

## Durable run and event model

### `agent_runs`

Each accepted user request creates one run containing:

- immutable `org_id`, `workspace_id`, `user_id`, `chat_id`, and input message;
- unique client idempotency key;
- parent run/attempt linkage;
- selected agent, route, model, embedding policy, and configuration versions;
- `accepted`, `queued`, `running`, `completed`, `failed`, or `cancelled` state;
- cancellation request/acknowledgement timestamps;
- first-event, first-token, completion, and failure timestamps;
- prompt, completion, tool, embedding, and evaluation token/cost ledgers;
- safe error category/code and trace ID;
- optional final assistant message and analytical artifact IDs.

State transitions use conditional updates. Every accepted run reaches exactly
one terminal state. Replayed commands cannot create duplicate messages, runs, or
mutating tool effects.

### Versioned event envelope

```json
{
  "id": "uuid",
  "sequence": 12,
  "schema_version": 1,
  "run_id": "uuid",
  "org_id": "uuid",
  "workspace_id": "uuid",
  "chat_id": "uuid",
  "message_id": "uuid-or-null",
  "type": "message.delta",
  "occurred_at": "2026-07-18T16:00:00Z",
  "payload": {}
}
```

Public event types include:

- `run.accepted`, `run.started`, `run.completed`, `run.failed`,
  `run.cancel.requested`, and `run.cancelled`;
- `route.selected` with a safe reason code, never chain-of-thought;
- `retrieval.started`, `retrieval.sources`, and `retrieval.completed`;
- `agent.started`, `agent.progress`, and `agent.completed`;
- `tool.started`, `tool.progress`, `tool.completed`, and `tool.failed`;
- `message.started`, `message.delta`, and `message.completed`;
- `ui.block.upsert`, `ui.committed`, `artifact.created`, and
  `artifact.versioned`;
- `usage.updated`, `approval.requested`, `clarification.requested`, and
  `heartbeat`.

Events are authorized using the current tenant context, ordered by `sequence`, and
resumable with `Last-Event-ID`. Durable milestones are persisted; token deltas
are coalesced and kept in the broker/replay buffer instead of inserting one
database row per token.

### Outbox, inbox, and delivery semantics

- Domain writes and outbox records commit in one database transaction.
- The relay publishes unprocessed outbox records with deterministic event IDs.
- Consumers record an inbox/dedupe key before applying an effect.
- Delivery is at-least-once; deterministic idempotency provides effectively-once
  domain effects.
- Consumers use leases, heartbeats, retries with jitter, bounded attempts, dead
  letters, and reconciliation jobs.
- Slow clients receive bounded coalesced events. When their buffer fills, the
  connection closes with a resumable cursor instead of growing memory.

## Agno and in-process LiteLLM

- Pin a tested Agno 2.7.x and LiteLLM release rather than accepting unbounded
  dependency upgrades.
- `ModelGateway` resolves the stored encrypted provider configuration and builds
  `agno.models.litellm.LiteLLM` inside the agent-runner process.
- Remove the LiteLLM Proxy service, proxy master key, proxy HTTP client, model
  deployment synchronization, and proxy database from the target Compose stack.
- The API layer depends on a `RunOrchestrator` protocol and never imports provider
  SDKs.
- No per-request mutation of process-global environment variables or callbacks
  is allowed. Provider parameters and secrets are request-scoped objects.
- Model policies store context window, maximum output, tokenizer/capabilities,
  supported modalities/tools/structured output, default temperatures, allowed
  uses, and global/workspace allowlists.
- Agno does not own authoritative sessions or memories. OpenRAG passes bounded
  context and tenant-bound tool closures into a stateless orchestrator.
- Public events omit raw prompts, raw model requests, provider credentials,
  hidden reasoning, and chain-of-thought.

## Routing and retrieval

### Modes

- `chat`: do not retrieve documents unless the user explicitly invokes a tool.
- `query`: require document retrieval and grounded citations; return a clear
  evidence-not-found answer when retrieval cannot satisfy policy.
- `automatic`: the default. A deterministic policy handles obvious greetings,
  conversation/meta-history requests, and explicit document requests before an
  Agno router chooses among authorized read tools.

The selected route is visible as a safe status such as `general_chat`,
`document_query`, `past_chat`, or `analytical`, without exposing internal
reasoning.

### Tenant-bound tools

- `search_documents(query, filters, top_k)`
- `search_past_chats(query, filters, limit)`
- `read_chat_segment(chat_id, anchor_message_id, radius)`
- `recall_memories(query, scope, limit)`
- later, explicitly approved read-only analytical data tools

Tool closures contain immutable tenant IDs and authorization. Model-generated
arguments cannot specify or override identity, organization, workspace, role,
or provider credentials. PostgreSQL authorization is rechecked after every
derived Qdrant search.

### Workspace RAG policy

Expose controlled workspace settings based on the useful AnythingLLM surface:

- mode, default completion model, allowed models;
- embedding model/version and reindex state;
- hybrid search enablement and dense/sparse weights;
- `top_k`, candidate count, minimum score, reranker/model, and rerank count;
- chunk strategy, target size, overlap, OCR policy, table/image extraction;
- pinned documents and metadata filters;
- history budget, output reserve, and evidence budget;
- no-answer policy and citation requirement.

Embedding changes create a new versioned collection/index and background
reindex. They never silently mix incompatible vector dimensions in one index.

## Conversation lifecycle

- The first successfully completed user run emits `title.requested.v1`.
- A background consumer creates a short privacy-aware title and applies it only
  with compare-and-set while the title is still `New chat`.
- Manual rename always wins.
- Chat list APIs use cursor pagination, workspace/date filters, and full-text
  title/content search. Semantic search is an additional option, not the only
  search path.
- Delete writes a tombstone and cleanup event atomically. Authorization fails
  immediately, even if derived-index cleanup is pending.
- Cleanup removes chat vectors, branch summaries, artifacts, sole-source
  memories, and content-bearing telemetry. Multi-source memories are
  recalculated.
- The frontend provides rename, confirmed delete, search, pagination, Stop,
  cancellation status, and a stable title list.

## Token-efficient memory kernel

OpenRAG owns all memory state. Agno receives only the selected context for the
current run.

### Memory types and scopes

- `semantic`: stable facts and preferences.
- `episodic`: time-bounded events and outcomes, default configurable 90-day TTL.
- `procedural`: versioned workflow rules created or approved explicitly.
- Default inferred scope: `user_workspace`.
- `user_org`: explicit stable user preferences only.
- `workspace_shared`: curated procedural knowledge approved by an authorized
  workspace actor.

Never create a durable memory directly from assistant output, retrieved
documents, or arbitrary tool text.

### Memory records

Persist content, canonical key, structured value, scope, type, confidence,
importance, sensitivity, status, TTL/validity, conflict group, supersession,
policy/model version, source trust, and hashes. Separate provenance edges link a
memory to exact user messages, verified events, or approvals.

Candidate, active, conflicted, superseded, retracted, expired, and quarantined
states are explicit. A suppression fingerprint prevents a forgotten memory from
being recreated from retained source messages.

### Background materialization

After a completed run, outbox consumers may request:

- branch-anchored rolling summary;
- memory candidate extraction;
- deterministic candidate validation and conflict resolution;
- past-chat text/vector indexing;
- title generation;
- usage/evaluation projections.

None of these jobs may delay the first token or completion event. Saturation
coalesces/deprioritizes title, summary, memory, and indexing jobs before
interactive work.

### Context budget

Use LiteLLM/model-aware token counting:

```text
input budget = min(workspace cap, model input limit)
               - reserved output tokens
               - tool/schema framing
               - safety margin
```

Initial allocation targets:

- documents/evidence: 35%;
- recent branch tail: 25%;
- branch summary: 10%;
- memories: 10%, maximum approximately eight items;
- past-chat tool result: maximum 15%;
- remaining space: system/tool/framing reserve.

System policy and the current query are immutable. Lowest-ranked evidence,
past-chat snippets, memories, and old tail messages are trimmed first. Persist an
estimated and actual per-section token ledger for tuning and audit.

### User controls

Users can view why a memory exists, edit it, forget it, export it, disable
extraction, choose permitted scopes/types, and inspect conflicts. Workspace-shared
memory requires an admin approval flow and audit record.

## Safe analytical presentation

### Contract

The model returns a closed, versioned `AnalyticsResponseV1` union. Initial block
types are:

- `MetricGrid` and `Metric`;
- `BarChart`, `LineChart`, and `AreaChart`;
- `DataTable`;
- `Explainer` and `Callout`;
- `SourceList`;
- `RelatedQueries` with continue-conversation actions only.

Each block has a stable ID, bounded props/data, optional `dataset_ref`, and
required source markers for factual values. Unknown blocks, props, chart types,
actions, URLs, and oversized data are rejected server-side using Pydantic.

No model output may contain executable JavaScript, JSX, HTML, CSS, arbitrary
URLs, tool names, MCP instructions, generic queries, or mutations.

### Persistence and rendering

- Persist canonical analytical JSON separately from assistant Markdown with
  schema and renderer versions, hash, run, tenant, chat, and message linkage.
- `ui.block.upsert` contains one complete validated block, never partial invalid
  JSON.
- The frontend maps validated block kinds to a fixed OpenRAG component registry.
- OpenUI Lang may be used as a trusted internal render target after validation;
  it is not the trust boundary.
- Malformed presentation cannot hide the cited Markdown fallback.
- Stream updates are batched with `requestAnimationFrame`; settled messages are
  memoized and the last valid tree remains visible during incomplete updates.

### Accessibility and export

- Charts have titles, captions, keyboard-readable values, non-color encodings,
  and equivalent accessible data tables.
- KPI grids collapse from four to two to one column; tables scroll on mobile;
  detailed views become full-screen overlays on small screens.
- Canonical datasets support CSV download. Print/PDF views disable animation and
  retain citations.
- Related-query actions only submit an ordinary user message to the existing
  authenticated chat command endpoint.

## Observability and error management

### Three complementary data planes

1. **Product facts in PostgreSQL:** durable RAG runs, stages, retrieval hits,
   token/cost ledgers, feedback, evaluation results, and configuration versions.
   These power the OpenRAG superadmin dashboard.
2. **OpenTelemetry:** traces and metrics for request/run/tool/retrieval/provider/
   ingestion execution, exported through an OTLP collector.
3. **Central logs and errors:** structured redacted JSON logs in Loki with trace
   correlation; optional Sentry-compatible exception grouping, release tracking,
   and alerting.

Grafana, Prometheus, Loki, Tempo, and the OpenTelemetry Collector are available
through an opt-in Compose observability profile. Production uses the same OTLP
contract with self-hosted or managed backends.

### Correlation and privacy

Every request, run, job, tool execution, log, span, and safe error contains
`trace_id`, `run_id`, environment, release, service, and tenant-safe identifiers.
Tenant IDs are attributes, not high-cardinality Prometheus labels.

Default logs and traces exclude:

- prompts and response bodies;
- retrieved chunk/document content;
- memory content;
- provider credentials and authorization values;
- raw tool arguments/results;
- filenames when organization policy marks them sensitive;
- hidden reasoning.

The current key-name redactor is extended with recursive structured redaction,
allowlists, maximum field sizes, sampling, retention, and tests. Superadmin trace
views show safe metadata and content only through a separate audited,
policy-controlled reveal action.

### Error tracking

Errors use stable categories and codes:

- validation/policy/authentication/authorization;
- rate limit/admission overload;
- provider transient/permanent;
- retrieval/embedding/reranking;
- ingestion/OCR/storage;
- tool/cancellation;
- persistence/broker;
- internal.

Provider details are retained in restricted telemetry but clients receive safe
problem details. Issues are grouped by fingerprint, service, release, and
environment, with counts, first/last occurrence, affected tenants/runs, status,
owner, alert state, and trace deep links.

## Superadmin RAG Operations experience

The page is a fixed, responsive operational dashboard sharing the analytical
component registry used in chat. It is not a free-form drag/drop dashboard.

### Global filters

- time range and comparison period;
- environment/release;
- organization and workspace;
- route and RAG mode;
- completion, embedding, reranking, and evaluator model/version;
- chunking/OCR/retrieval configuration version;
- document type and ingestion stage;
- success/no-answer/error/cancelled status.

### Overview

- query throughput and concurrent runs;
- success, no-answer, error, and cancellation rates;
- p50/p95/p99 end-to-end latency and time-to-first-token;
- retrieval, reranking, tool, provider, and serialization latency;
- prompt/completion/tool/evaluation tokens and estimated cost;
- groundedness, answer relevance, citation coverage/precision, user feedback;
- provider/model error and throttling rates;
- active alerts and recent regressions.

### Retrieval and answer quality

- retrieval threshold pass rate and empty-result rate;
- recall@k, precision@k, MRR/nDCG for golden datasets;
- reranker lift and source duplication;
- groundedness/faithfulness and response relevance;
- no-answer correctness and citation support;
- side-by-side comparison of model, embedding, chunker, reranker, prompt, and
  route policy versions.

Live LLM-judge evaluations run asynchronously and are sampled/budgeted. Offline
golden-dataset evaluations are versioned, reproducible, and required before
promoting high-impact retrieval changes.

### Ingestion and capacity

- bytes/documents/pages/chunks processed over time;
- queue depth, oldest job age, throughput, retry, failure, and dead-letter rates;
- stage latency for storage, OCR, parse, chunk, embed, and upsert;
- stuck-job detection and safe retry/reindex controls;
- vector collection size and index/version migration progress;
- CPU, memory, database pool, Redis, Qdrant, object storage, file descriptors,
  and active stream saturation.

### Run trace drill-down

A trace view shows safe, timestamped stages:

- accepted route and configuration versions;
- context/token ledger;
- retrieval queries, chunk IDs/pages/scores, filters, and rerank changes;
- tool lifecycle and safe result summaries;
- model/provider, first-token and completion timing, usage, and retry attempts;
- citations/artifact blocks produced;
- evaluation, feedback, error, and cancellation outcomes;
- links to Tempo/Loki/error tracking using the same trace ID.

No chain-of-thought is stored or displayed.

## Fully asynchronous capacity design

- API/stream processes perform async network/database operations only.
- Blocking parsing/OCR, local embedding, and CPU-heavy evaluation run in
  isolated processes and separate queues.
- Agent runners use shared long-lived async LiteLLM/provider, Qdrant, Redis,
  storage, and embedding clients.
- Interactive, ingestion, memory, evaluation, deletion, and maintenance queues
  have separate concurrency and priority budgets.
- Provider, organization, user, retrieval, and tool semaphores enforce fairness
  and protect upstream services.
- Admission control returns a retryable overload response before accepting work
  that cannot meet bounded queue policy.
- Per-run event queues and payload sizes are bounded. Deltas coalesce every
  approximately 20-50 ms or 256-1024 bytes.
- API DB pool sizing is explicit across replicas and remains below PostgreSQL's
  connection budget. Streams do not hold SQLAlchemy sessions.
- Cancellation propagates from command to run flag, task group, provider stream,
  retrieval/tool operations, and terminal persistence.
- Heartbeats, timeouts, broker lag, dead letters, consumer leases, and stuck runs
  are observable and alertable.

Initial runner guardrails allow approximately 32-64 concurrent I/O-bound runs
per process, subject to measured provider and memory limits. CPU-bound queues use
separate concurrency sized to available cores.

## Large-data enterprise requirements

For approximately 500 GB of documents:

- Object storage holds immutable originals and versioned parse/chunk artifacts.
- Uploads support multipart/resumable behavior, checksums, quotas, and malware
  scanning hooks.
- Ingestion uses bounded batches, backpressure, queue partitions, checkpoints,
  retries, and idempotent stage outputs.
- OCR policy can be automatic, forced, disabled, or page-selective; OCR engine
  and version are recorded.
- Vector indexes are versioned by embedding model/dimension/configuration and
  migrated with dual-read/cutover rather than destructive in-place changes.
- Deletion, retention, legal hold, reindex, reconciliation, and derived-data
  cleanup are explicit workflows with progress and SLOs.
- Quotas and capacity views cover stored bytes, documents, pages, chunks,
  vectors, tokens, concurrent runs, and evaluation spend.
- Backup and recovery cover PostgreSQL, object storage, vector indexes or
  reproducible reindex artifacts, and the encryption key as one consistency set.

## Security invariants

- Every database query, vector query, event subscription, tool, artifact,
  memory, evaluation, and admin operation is authorized by immutable tenant
  context.
- Derived vector hits are revalidated against PostgreSQL authority.
- Client/model arguments cannot override tenant identity or authorization.
- Mutating tools are disabled initially; later mutations require explicit
  allowlists, fresh authorization, confirmation, idempotency, and audit.
- Prompt/tool/document/memory content is untrusted data and cannot modify system
  policy.
- Secrets are decrypted only inside the model gateway and never emitted.
- Public events and dashboards never expose hidden model reasoning.
- Membership revocation takes effect immediately regardless of asynchronous
  cleanup lag.

## Verification and service-level objectives

### Functional acceptance

- `hi` selects `general_chat`, performs no document search, and streams tokens.
- An explicit document question retrieves evidence, streams an answer, and
  persists valid citations.
- A follow-up such as `Tell me more about it` uses the branch context and can
  retrieve the prior subject without repeating it.
- `What was my previous question?` answers from conversation context.
- Thread titles update after the first completed run; manual rename wins.
- Chat search finds titles and message content; deletion disappears immediately
  and cleans all derived data asynchronously.
- Memory survives a new chat when policy permits and can be viewed, edited,
  forgotten, disabled, and traced to evidence.
- Analytical requests render validated KPI/chart/table/explainer blocks with
  citations and accessible data fallback.
- Superadmins can filter dashboard data and drill from an error/quality metric to
  the corresponding safe run trace.

### Capacity acceptance

- 100 sustained live streams and a 200-stream spike do not exhaust DB pools,
  file descriptors, Redis connections, or unbounded memory.
- Every accepted run emits exactly one terminal event.
- Reconnect with `Last-Event-ID` resumes events in order without duplicated UI
  blocks or mutating effects.
- Cancellation acknowledgement is under one second at p95 and upstream work
  stops under two seconds at p95.
- Slow-client tests demonstrate bounded queues and delta coalescing.
- Broker restart/outbox replay produces no duplicate messages or mutations.
- A 30-minute soak has less than 10% unexplained memory growth and no pool
  exhaustion.
- Adversarial concurrent isolation tests produce zero cross-tenant results,
  events, memories, artifacts, or analytics.

### Performance SLO starting targets

- API command acceptance: p95 below 250 ms under nominal load.
- First status event: p95 below 500 ms.
- General-chat time to first provider token: p95 below 2.5 seconds, excluding
  documented provider throttling.
- Retrieval route overhead before provider request: p95 below 1.5 seconds for a
  healthy local retrieval stack.
- Stream event delivery from broker to connected client: p95 below 250 ms.
- Interactive availability target: 99.9% excluding explicitly documented
  provider outages.

Targets must be validated against representative hardware and providers; the
dashboard must display actual SLO compliance rather than hard-coded sample data.

## Delivery slices

### Slice 1: Durable event/run foundation

Agent runs, event schemas, transactional outbox/inbox, Redis Streams adapter,
stream replay, cancellation, short transactions, shared clients, and concurrency
tests. This slice removes the database-session-per-stream bottleneck.

### Slice 2: Agno + in-process LiteLLM routing

Model gateway, secret resolution, stateless Agno orchestrator, chat/query/
automatic routing, tenant-bound document tool, direct streaming, and removal of
the LiteLLM Proxy/runtime sync.

### Slice 3: Conversation lifecycle and memory

Titles, rename/delete/search/pagination, branch-aware context, summaries,
past-chat tools, memory kernel, provenance, controls, deletion cascade, and
token ledger.

### Slice 4: Structured analytical presentation

`AnalyticsResponseV1`, validation, persistence, event blocks, OpenRAG component
registry, charts/tables/explainers/follow-ups, accessibility, exports, mobile
views, and artifacts.

### Slice 5: Observability and RAG Operations

OpenTelemetry, structured correlation/redaction, Grafana stack, safe error
grouping, durable RAG facts/evaluations, superadmin APIs/dashboard, trace
drill-down, alerts, and regression comparisons.

### Slice 6: Enterprise ingestion and hardening

Configurable embeddings/OCR/chunking/reranking, versioned index migrations,
resumable uploads, isolated workers, quotas, reconciliation, retention/legal
hold, 500-GB operational controls, security tests, load/soak tests, deployment
guides, and disaster recovery.

Each slice receives its own TDD implementation plan and ends in independently
deployable, browser-verifiable software. The full objective remains incomplete
until all slices and the service-level acceptance criteria pass.
