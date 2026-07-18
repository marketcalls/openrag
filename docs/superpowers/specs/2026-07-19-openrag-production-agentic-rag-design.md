# OpenRAG Production Agentic RAG Design

**Date:** 2026-07-19

**Status:** Approved direction

**Supersedes:** RAG-quality and enterprise-governance portions of the phase-one design

**Complements:** `docs/superpowers/gaps/2026-07-18-openrag-enterprise-agentic-platform-design.md`

## Objective

Raise OpenRAG from a functional hybrid-RAG prototype to a production-grade,
agentic enterprise knowledge platform. The target is a measurable 9/10 across
document intelligence, retrieval quality, grounding, orchestration, model
configuration, security, operability, scalability, and user experience.

The number is a release scorecard, not a marketing assertion. A capability only
earns credit when it has current-state code, automated coverage, runtime
evidence, and an operator-visible health or evaluation signal.

## Non-negotiable product policy

OpenRAG is strict closed-book by default for substantive company-knowledge
questions.

- Greetings, product help, and safe conversational acknowledgements may bypass
  document retrieval and stream directly from the configured completion model.
- Questions about the current thread may use persisted thread context and
  governed memory without document retrieval when no company fact is asserted.
- Substantive company answers must be grounded only in current, approved,
  authorized document versions.
- Every material factual claim must resolve to citations containing document
  name, version, section, and page.
- If eligible evidence is absent, insufficient, contradictory, or cannot be
  cited completely, OpenRAG must refuse rather than guess.
- General-world chat can be introduced later only as an explicit workspace
  policy. It is not the default.

## Alternatives considered

### Selected: policy-driven agentic RAG

A deterministic policy and authorization layer surrounds a bounded Agno
orchestrator. The agent selects only tenant-bound, allowlisted tools. Retrieval,
evidence sufficiency, citation validation, and answer release remain OpenRAG
policy decisions, not model discretion.

This gives agentic routing and multi-step retrieval while keeping latency,
authorization, refusal, and audit behavior testable.

### Rejected as the default: user-selectable chat/query modes

AnythingLLM-style modes are useful controls, but exposing an ungrounded mode by
default would make corporate answer provenance dependent on user selection.
Selected controls such as top-k, threshold, reranking, and pinned scope will be
adapted as governed workspace policies instead.

### Rejected as the default: multi-agent review for every question

A planner, researcher, writer, and reviewer on every request would increase
latency and token cost and would jeopardize the 3-5 second target. Expensive
query decomposition and verification are conditional and bounded.

## Target topology

```text
Browser
  |-- authenticated command: ask / cancel / retry / approve
  `-- authenticated replayable SSE: run events and answer deltas
                  |
         API and stream gateway
                  |
   short PostgreSQL transaction + transactional outbox
                  |
             Redis Streams
                  |
        bounded async agent runners
        |-- deterministic policy gate
        |-- Agno router/orchestrator
        |-- in-process LiteLLM
        |-- retrieval and memory tools
        `-- answer/citation verifier
                  |
       PostgreSQL + Qdrant + object storage
                  |
     projections, evaluation and observability
```

PostgreSQL is authoritative. Redis is durable transport and bounded replay, not
the source of truth. Qdrant stores derived search indexes; every result is
revalidated against PostgreSQL document policy before prompt inclusion.

## Agentic query lifecycle

1. Authenticate the user and resolve organization, workspace, capabilities,
   document ACL scope, and active policy versions.
2. Persist the user message, `AgentRun`, and outbox event in one short
   transaction using the client idempotency key.
3. Route with a bounded classifier:
   - `direct`: greeting, OpenRAG help, or safe acknowledgement;
   - `conversation`: current-thread context or governed memory;
   - `rag`: substantive company-knowledge question;
   - `analytics`: RAG answer plus safe structured presentation;
   - `clarify`: ambiguity that materially changes the evidence scope.
4. For RAG routes, build an evidence plan from the query, explicit filters,
   conversation context, abbreviations, date/version intent, and requested
   document scope.
5. Retrieve only current approved, effective, non-superseded, ACL-accessible
   versions using parallel dense and sparse candidate generation.
6. Fuse candidates with reciprocal-rank fusion, deduplicate, rerank, expand
   parent sections, and enforce per-document/section coverage limits.
7. Compute evidence sufficiency from calibrated retrieval/rerank scores,
   coverage, requested fields, contradictions, and citation-ready provenance.
8. Refuse before generation when evidence is insufficient.
9. Generate a structured, streamed answer through in-process LiteLLM. Retrieved
   content is explicitly delimited as untrusted data.
10. Validate citation markers, citation snapshots, material-claim coverage, and
    policy compliance. A failed validation replaces the draft with a grounded
    refusal; an unverified draft is never persisted as a successful answer.
11. Persist the answer, citations, route, prompt/config versions, retrieval facts,
    usage, latency, and safe error codes. Emit one terminal run event.

The model never receives unrestricted database, URL, shell, browser, MCP, or
mutation tools. Tool arguments are schema-validated, authorization is repeated
inside every tool, results are size bounded, and reasoning traces are not stored
or exposed.

## Document authority and version lifecycle

### Logical document

`Document` represents the stable business identity, not an uploaded blob.

Required fields include organization, workspace, stable title/name,
department, document type, owner, optional external identifier, ACL policy,
created actor, and lifecycle timestamps.

### Immutable document version

`DocumentVersion` represents one immutable source revision and contains:

- version label and normalized sortable version;
- revision/effective/expiry dates;
- state: `draft`, `processing`, `review`, `approved`, `rejected`, `superseded`,
  `obsolete`, or `failed`;
- source filename, detected MIME, size, hash, object-storage key, page count;
- parser, OCR, chunking, embedding, and index-generation versions;
- approval actor/time and supersession relationship;
- processing/error summary without leaking document content.

Only one current approved version may be active for a logical document within
an effective period. Approval and supersession use transactional constraints and
are fully audited. Uploading a new file never silently removes the former index.

### Normalized content

Page/slide/sheet-aware `DocumentBlock` records preserve:

- page number or slide/sheet identity;
- block type such as title, heading, paragraph, list, table, image caption, OCR;
- section heading path;
- source coordinates when available;
- extraction method and OCR confidence;
- parent-child structure and stable content hash.

`DocumentChunk` records preserve block membership, page range, section path,
parent chunk, token count, content hash, and chunking profile version.

### Index generations

An `IndexGeneration` binds an embedding profile, vector dimensions, distance,
sparse strategy, chunking profile, collection/alias, state, evaluation result,
and activation time.

Embedding changes create a new generation. Reindexing proceeds in the
background with checkpointed batches, optional dual read, evaluation, atomic
alias cutover, and rollback. Existing indexes are never destructively reset
before the replacement is proven usable.

## Secure ingestion and OCR

Supported source formats are PDF, DOCX, XLSX, PPTX, TXT, Markdown, and scanned
or mixed PDFs. CSV may remain supported as an additional text/table format.

The upload path must:

- stream or multipart-upload directly to object storage;
- enforce edge and application byte limits before unbounded buffering;
- generate server-side storage identifiers and preserve the original name only
  as metadata;
- validate extension, declared MIME, magic bytes, and parser compatibility;
- quarantine before indexing and expose a malware-scanner integration point;
- reject active/unsupported content, archive bombs, encrypted files without an
  approved flow, and parser-limit violations;
- use object-storage encryption and TLS in production;
- record an immutable audit event without document text.

Parsing is executed in isolated CPU workers with byte, page, time, memory, and
concurrency budgets. OCR is decided per page, so mixed PDFs do not lose scanned
pages. OCR profiles define engine, languages, confidence threshold, fallback,
concurrency, and timeout. Low-confidence pages remain visible to operators and
can block approval.

Ingestion is an idempotent state machine with persisted attempts, leases,
checkpoints, progress, cancellation, retries, dead-letter state, and manual
replay. Bulk ingestion cannot monopolize interactive query capacity.

## Retrieval architecture

### Policy prefilter

PostgreSQL resolves allowed document-version IDs from organization, workspace,
role/capability, document ACL, approval, effective date, supersession, and
obsolete state. Qdrant receives those immutable constraints. Results are
revalidated before use.

### Candidate generation

- dense semantic retrieval through the active embedding profile;
- sparse BM25-style keyword retrieval;
- exact identifiers, filenames, version labels, amounts, and codes through
  sparse/metadata paths;
- optional bounded query variants for acronyms, follow-ups, comparisons, and
  multi-part questions;
- optional pinned document/version scopes.

Candidate breadth is bounded and measured. “Scan all relevant documents” means
candidate generation across every eligible document and evidence-coverage
evaluation, not reading 500 GB into one prompt.

### Fusion and reranking

Use RRF as the provider-independent baseline. Apply metadata-aware boosts only
after authorization and eligibility filtering. Latest approved version is a
policy filter or deterministic tie-breaker, never an excuse to ignore semantic
relevance.

A versioned cross-encoder reranker recalibrates the bounded candidate set.
Near-duplicate chunks are collapsed, parent sections are expanded, and final
context selection balances relevance, coverage, diversity, and token budget.

### Sufficiency and contradiction

The sufficiency evaluator considers:

- calibrated fused/rerank scores;
- whether the requested entities/fields are present;
- coverage across question sub-parts;
- source diversity where required;
- conflicting current approved sources;
- complete page/section/version provenance;
- workspace-specific calibrated thresholds.

Thresholds are tuned using golden datasets, never chosen only by intuition.
Contradictory approved evidence produces an explicit conflict response with
citations rather than silent model arbitration.

## Prompt engineering and answer contract

Prompts are server-owned, immutable/versioned records with a stable purpose,
template hash, compatible routes/models, status, evaluation result, and rollout
percentage. Changes pass prompt-injection, grounding, refusal, citation, token,
and latency regression tests before activation.

The prompt boundary separates:

- trusted system policy;
- trusted route and output schema;
- untrusted conversation text;
- untrusted retrieved evidence;
- tool results with explicit provenance.

Context assembly uses model-aware tokenization and reserves budgets for system
policy, current question, thread summary, recent messages, evidence, tool
results, output, and structured schema. Older turns are summarized with
provenance instead of silently discarded.

The answer schema includes status, Markdown answer, citation markers,
no-answer/conflict reason, optional `AnalyticsResponseV1`, and safe suggested
follow-ups. The user sees streamed text, but completion is not marked successful
until validation passes.

## Citation contract

Every persisted citation is an immutable snapshot containing:

- marker;
- logical document ID and document-version ID;
- displayed document name;
- displayed version;
- section path;
- page number or slide/sheet locator;
- chunk/block reference and content hash;
- retrieval, fused, and rerank scores;
- prompt/config version and cited claim identifiers.

The verifier rejects nonexistent markers, inaccessible versions, superseded or
obsolete versions, mismatched snapshots, unsupported material claims, and
answers with inadequate citation coverage.

## Model, embedding, reranker, and OCR profiles

OpenRAG uses LiteLLM only as an in-process Python library. The target runtime has
no LiteLLM Proxy and no direct OpenAI application SDK.

`ProviderCredential` stores encrypted organization/platform credentials with a
write-only API, fingerprint, provider type, scopes, rotation state, and audit
history. Secrets are decrypted only inside the bounded worker operation that
needs them and are never placed in events, logs, traces, prompts, or browser
responses.

`AIProfile` is a versioned capability record with kind `completion`,
`embedding`, `reranker`, or `ocr`. It includes provider, LiteLLM/provider model
identifier, display name, capability flags, context/dimension metadata, batch
limits, latency/cost class, compatible endpoints, credential reference,
validation status, last validation time, allowlist scope, and enabled state.

The preset catalog is curated and versioned. Discovery data may inform presets,
but a remote model map never becomes executable configuration without policy
validation. Free-form provider base URLs require scheme/host/port allowlists,
DNS/IP validation, redirect controls, timeouts, and network egress policy.

Workspace policy selects allowed/default completion, embedding, reranker, OCR,
chunking, retrieval, prompt, and verification profiles. Compatibility is
validated before saving. Live tests are explicit, cost bounded, redacted, and
do not print or return stored keys.

## Enterprise RBAC

The existing free-form role string is unsafe: an organization administrator can
assign `superadmin`, and any arbitrary role can bypass checks that only restrict
the literal `user` role. This must be fixed before custom-role UI ships.

The replacement uses:

- non-assignable platform-superadmin identity managed outside organization role
  administration;
- organization-scoped `Role`, `Permission`, `RolePermission`, and `RoleBinding`;
- optional workspace/document-scoped bindings;
- deny-by-default capability checks on every protected route and service;
- mandatory organization plus object-level authorization independent of role
  name;
- templates for Administrator, HSE Manager, Engineer, and basic User;
- explicit capabilities such as `user.manage`, `role.manage`,
  `workspace.manage`, `document.upload`, `document.read`, `document.approve`,
  `model.configure`, `rag.evaluate`, and `audit.read`;
- prevention of privilege escalation, last-admin removal, cross-organization
  assignment, and platform-role assignment;
- auditable role, permission, binding, and document-approval changes.

Frontend role checks remain UX hints only. The backend is authoritative.

## Safe analytical presentation

OpenRAG adopts OpenUI's visual vocabulary, not its arbitrary execution model.

`AnalyticsResponseV1` is a server-owned discriminated schema with allowlisted,
read-only components: KPI group, bar/line/area chart, table, explainer, callout,
source note, and follow-up suggestion. It enforces component count, rows, series,
points, string length, numeric bounds, palette, and total serialized size.

Every analytical fact carries provenance/citations. The browser renders React
components from validated JSON. It never executes generated code, raw HTML,
URLs, MCP tools, generic queries/mutations, or privileged actions. Streaming
uses last-good state and frame-batched updates; invalid artifacts degrade to
safe Markdown.

## Full-stack administration

The application must expose:

- document list, version history, processing/OCR detail, approval,
  supersession, rejection, reindex, and deletion;
- completion/embedding/reranker/OCR preset catalog, profile editor, validation,
  workspace assignment, migration preview, reindex progress, cutover, rollback;
- custom role templates, permission matrix, bindings, effective-access preview,
  and audit history;
- chat title, rename, search, pagination, deletion, branch context, memory
  inspection, promotion/suppression, and retention controls;
- RAG Operations dashboard and run drill-down;
- evaluation datasets, cases, runs, configuration comparisons, release gates,
  and human-review queue;
- centralized log/error views with trace correlation and redacted detail.

## Observability and evaluation

OpenTelemetry traces cover admission, routing, memory, query transformation,
embedding, dense/sparse retrieval, fusion, reranking, context construction,
LiteLLM first token/stream, validation, persistence, and projection.

Structured logs use request/run/trace correlation and recursive allowlist-based
redaction. Prompts, document text, tool content, credentials, tokens, and memory
are excluded by default. Security audit events are immutable and separate from
retention-controlled operational logs.

The development observability profile uses OpenTelemetry Collector, Prometheus,
Tempo, Loki, and Grafana. Production can export OTLP to managed equivalents.
Optional Sentry-compatible error grouping is PII-off by default.

Versioned golden datasets measure:

- retrieval recall@k, MRR, and nDCG;
- answer correctness, relevance, and faithfulness;
- citation precision, recall, and material-claim coverage;
- correct refusal and false-refusal rates;
- version/approval selection correctness;
- cross-tenant leakage (must be zero);
- latency, time to first token, token/cost usage, and provider errors.

The requested 95-98% accuracy is an acceptance target on representative,
versioned datasets and is reported with dataset size and confidence. It is not a
universal guarantee.

## Security baseline

- Authentication is explicit and protected routers are deny by default.
- Access tokens remain short lived and in memory; refresh credentials remain
  HTTP-only, Secure in production, SameSite protected, rotated, and revocable.
- JWT algorithms are allowlisted; production issuer/audience policy is explicit.
- API docs are disabled or protected in production.
- trusted hosts, trusted proxy boundaries, TLS, CSP, `nosniff`, clickjacking,
  referrer, and permissions headers are enforced at app/edge.
- CORS stays disabled for same-origin deployment or uses a strict allowlist.
- Request/multipart/rate/concurrency limits protect expensive endpoints.
- Uploads use content validation, safe identifiers, quarantine, and encrypted
  storage outside the web root.
- SQL stays parameterized; shell execution is avoided; outbound endpoints are
  SSRF constrained.
- Markdown disables raw HTML; analytical UI is schema allowlisted; URLs use
  safe protocol/origin validation.
- Frontend bundles contain no secrets and public source maps are disabled.
- Dependencies are locked, reproducible, audited, and patched.
- Prompt-injection defenses treat documents, memories, history, metadata, and
  tool results as untrusted data.

## Concurrency and scale

For the initial production target:

- 2-4 stateless API replicas;
- horizontally scalable async agent runners with per-provider/model semaphores;
- separate parse/OCR, embedding, deletion, and projection worker pools;
- PgBouncer transaction pooling in production;
- no SQL transaction or request-scoped session across provider/tool streams;
- pooled async HTTP, Redis, object-storage, and provider clients;
- tenant/user/provider RPM, TPM, concurrency, queue-depth, and queue-age limits;
- durable retries, dead letters, fair scheduling, and `Retry-After` admission;
- bounded SSE buffers, heartbeat, replay, cancellation, and slow-client policy;
- resumable bulk ingestion suitable for repositories around 500 GB.

The response goal is first visible progress quickly and a normal grounded answer
within 3-5 seconds when provider and retrieval capacity permit. Complex
multi-document analysis may take longer but must stream progress and remain
cancellable.

## 9/10 release scorecard

Each category is scored only from evidence. A production release requires no
critical security issue, no tenant leakage, all P0 capabilities, and at least 90
of 100 total points.

| Category | Points | Required evidence |
|---|---:|---|
| Document intelligence and OCR | 12 | all required formats, page provenance, OCR fixtures, lifecycle tests |
| Retrieval quality | 14 | hybrid, rerank, expansion, version policy, golden retrieval metrics |
| Grounding and citations | 14 | sufficiency, refusal, claim coverage, immutable citation contract |
| Agentic orchestration | 10 | bounded routes/tools, cancellation, replay, failure tests |
| Profiles and index migration | 10 | curated presets, compatibility, reindex/cutover/rollback tests |
| Conversation and memory | 8 | follow-ups, summaries, search/delete, governed memory tests |
| Security and governance | 14 | RBAC/object isolation, upload, SSRF, headers, secret/audit tests |
| Reliability and scale | 8 | 100-user soak, backpressure, idempotency, recovery evidence |
| Observability and evaluation | 6 | correlated traces/logs/metrics, dashboard, release gates |
| Full-stack UX | 4 | accessible operator/user workflows and browser smoke tests |

## Acceptance gates

- Required-format parse/OCR golden fixtures pass with correct page/section
  provenance.
- Current approved version selection is deterministic; obsolete, superseded,
  unauthorized, and cross-tenant content never reaches the prompt.
- Every successful substantive answer has valid document/version/section/page
  citations; insufficient evidence produces a refusal.
- Prompt-injection suites cannot override system policy or invoke tools.
- Organization admins cannot assign platform superadmin or gain permissions
  outside explicit bindings.
- Embedding migration preserves service, supports rollback, and never deletes
  the active index before validation.
- 100 concurrent authenticated streaming users meet the agreed success, queue,
  latency, cancellation, DB-pool, event-loop, and memory thresholds.
- Backend tests, Ruff, strict mypy, import-linter, frontend tests, ESLint,
  TypeScript, production build, Playwright smoke tests, dependency audits, and
  Compose smoke tests pass.
- A bounded live LiteLLM smoke test proves stored credential resolution,
  streaming, grounded citation behavior, and redaction without exposing the key.

## Delivery sequence

1. Fix RBAC privilege escalation and introduce capability authorization.
2. Introduce logical documents, immutable versions, approval/supersession, and
   the complete citation contract.
3. Secure and expand ingestion/OCR with all required format fixtures.
4. Introduce versioned AI profiles and safe index generations/migrations.
5. Implement policy routing, multi-stage retrieval, reranking, sufficiency, and
   answer/citation verification.
6. Move orchestration to Agno plus in-process LiteLLM runners on the durable
   event foundation.
7. Complete conversation memory/search/delete and token-efficient context.
8. Add safe analytical responses and the administration workflows.
9. Add RAG operations, centralized observability, evaluations, security tests,
   load tests, and production deployment hardening.

Each sequence item is delivered as a separate reviewed implementation plan with
TDD, migrations, compatibility notes, rollback, focused verification, and a
periodic public `main` push. The local `anything-llm/` and `openui/` repositories
remain read-only benchmarks and are never committed.
