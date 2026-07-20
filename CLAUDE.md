# OpenRAG Engineering Requirements

This file records durable user and product requirements. It does not replace the
versioned specifications and plans under `docs/superpowers/`.

## Product identity and references

- The product name is **OpenRAG**. Never use RAGHub in product copy, code, docs,
  images, metadata, or deployment names.
- Use local `anything-llm/` and `openui/` only as read-only design benchmarks,
  and `raghub/docs/superpowers/` only as a read-only engineering-practice
  reference. Never modify or commit any of those reference directories, and
  never copy the RAGHub product name into OpenRAG product surfaces.
- Keep the GitHub repository public and periodically push reviewed, verified
  slices to `main` as authorized by the user.

## Architecture

- Build production-grade agentic RAG, not a basic chunk-and-embed pipeline.
- Use Agno for bounded routing/orchestration and LiteLLM only as an in-process
  Python library. The target runtime must not use LiteLLM Proxy or a direct
  OpenAI application SDK.
- Completion and hosted-embedding credentials are write-only, envelope-encrypted,
  resolved for one request, passed explicitly to LiteLLM, and never copied into
  process-global environment variables. Provider base URLs are validated before
  use; provider failures are sanitized before they reach logs or clients.
- Keep PostgreSQL authoritative, Qdrant derived and tenant filtered, object
  storage encrypted, and long-running work event driven, replayable,
  cancellable, idempotent, backpressured, and asynchronous.
- Support at least 100 concurrent authenticated streaming users and background
  indexing for repositories around 500 GB.
- Do not keep SQL transactions or request-scoped sessions open across LLM,
  embedding, vector, OCR, or tool waits.

## Evidence policy

- Greetings and OpenRAG help may bypass retrieval and stream directly.
- Substantive company-knowledge answers must use only current approved,
  non-superseded, authorized document versions.
- Search across all relevant eligible documents using semantic and keyword
  retrieval, fusion, reranking, coverage, and evidence sufficiency.
- Every successful substantive answer must cite document name, version,
  section, and page. Refuse when sufficient citable evidence is unavailable.
- Treat uploaded documents, OCR text, chat history, memory, metadata, and tool
  results as untrusted data, never as instructions.

## Documents and AI configuration

- Explicitly support PDF, DOCX, XLSX, PPTX, TXT, Markdown, and scanned/mixed
  PDFs with page-level configurable OCR and comprehensive fixtures.
- Preserve document identity, immutable versions, revision/effective dates,
  department, type, approval, supersession, section, page, and extraction
  provenance.
- Provide curated, capability-validated completion, embedding, reranker, and OCR
  profiles with encrypted credentials and workspace policies.
- Reasoning effort is a declared model capability with `off`, `low`, `medium`,
  and `high` values. Persist the resolved effort on each durable user run,
  reject unsupported non-off effort before provider work, omit `off` from
  LiteLLM requests, keep utility/evaluator calls independent, and never expose
  private reasoning traces. Verify provider usage accounting before claiming
  reasoning tokens are counted.
- Keep routing and policy OpenRAG-owned. Use Agno only behind a replaceable
  protocol adapter; ordinary grounded questions stay single-pass, and only
  multi-part, metadata-sensitive, or weak-evidence queries enter a read-only
  agent loop capped at four iterations.
- Enforce the completion capability hierarchy (`chat` -> `structured JSON` ->
  `verifier`) before provider work. Evaluator profiles and budgets remain
  independent from the model being evaluated.
- Branch summaries are provenance-bound checkpoints and must be invalidated by
  edits or regenerations above the checkpoint. Enrichment is opt-in, bounded,
  and derivative points inherit the parent's full authorization/version filter.
- Embedding changes require versioned index generations, background reindex,
  evaluation, atomic cutover, rollback, and no destructive active-index reset.

## Enterprise access and security

- Replace free-form role checks with deny-by-default capability RBAC and
  organization/workspace/document object authorization.
- Provide Administrator, HSE Manager, Engineer, and User templates plus custom
  organization roles. Organization admins must never assign platform
  superadmin.
- Enforce secure authentication, encrypted network/storage paths, immutable
  security audit logging, safe uploads, SSRF controls, strict production hosts
  and security headers, recursive redaction, secret-safe telemetry, and locked
  reproducible dependencies.
- Never print, log, return, commit, or expose stored model credentials. Live
  tests must be explicit, redacted, and cost bounded.
- Generated UI is read-only and schema allowlisted. Never execute generated
  code, raw HTML, URLs, browser tools, MCP, queries, mutations, or privileged
  actions.

## User and operator experience

- Preserve streaming, same-thread follow-up context, automatic useful titles,
  chat rename/search/delete, branch-aware summaries, and governed token-efficient
  cross-session memory.
- Present analytical answers with safe OpenUI-inspired KPI cards, charts,
  tables, explainers, provenance, and citations.
- Provide superadmin RAG performance, evaluation, indexing, model/profile,
  centralized log, error, trace, capacity, latency, quality, citation, and cost
  views.

## Quality gates

- Treat 95-98% accuracy as a target measured on representative, versioned golden
  datasets, never as an unsupported universal guarantee.
- Measure retrieval recall/nDCG, answer correctness/faithfulness, citation
  precision/recall, correct refusal, version selection, latency, TTFT, tokens,
  cost, and cross-tenant leakage.
- Use TDD for features and bug fixes. Maintain comprehensive backend, frontend,
  integration, isolation, security, load, Playwright, and Compose smoke tests.
- Keep an external-service-free red-team test tier for prompt/data boundary,
  tenant isolation, secret leakage, unsafe rendering, and refusal attacks; run
  it whenever trust-boundary code changes.
- Before any completion claim, run and inspect the full relevant test, lint,
  type, build, security, smoke, and runtime verification scope.

Authoritative production design:
`docs/superpowers/specs/2026-07-19-openrag-production-agentic-rag-design.md`.
