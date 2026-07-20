# RAG Operations and Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The user requires single-agent execution; do not dispatch subagents.

**Goal:** Build a secure, full-stack super-admin operations surface for OpenRAG that measures RAG quality, latency, throughput, token/cost usage, indexing health, evaluations, and safely grouped errors with correlated traces and centralized logs.

**Architecture:** PostgreSQL stores bounded product facts, evaluation records, and redacted error groups. Super-admin APIs aggregate those facts with database-side filters and percentiles; the React dashboard renders a fixed responsive view with accessible charts and drill-downs. OpenTelemetry exports correlated traces, metrics, and structured redacted logs through an opt-in Compose profile to Prometheus, Tempo, and Loki/Grafana without storing prompts, document text, memory, credentials, or hidden reasoning.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, PostgreSQL, Celery, Redis, Agno with in-process LiteLLM, OpenTelemetry OTLP, Grafana/Prometheus/Loki/Tempo, React 18, TypeScript, TanStack Query, Tailwind CSS, Vitest, Pytest, Playwright.

## Global Constraints

- Work directly on the authorized public `main` branch as a single agent and push every independently useful checkpoint.
- All operations routes require `require_platform_superadmin`; organization-scoped `rag.evaluate` is insufficient for platform-wide data.
- Product facts contain IDs, bounded numeric measurements, version identifiers, status, and safe codes only. Never persist prompts, responses, retrieved text, memory content, filenames, provider payloads, credentials, authorization values, tool arguments/results, or chain-of-thought.
- Correlation uses a server-issued 32-lowercase-hex `trace_id`; untrusted inbound correlation values are discarded unless they match the exact format.
- Tenant identifiers may be trace/log attributes but never Prometheus labels.
- API aggregation must be database-side and bounded; no loading unbounded run or error rows into Python.
- Live evaluations are asynchronous, explicitly budgeted, and isolated on an `evaluations` queue.
- The dashboard is a fixed responsive layout, not a drag/drop editor. Every chart has an equivalent accessible table and does not rely on color alone.
- Generated data presentation is schema-allowlisted and read-only. Never execute model-generated HTML, JavaScript, URLs, queries, or privileged actions.
- Use TDD for every feature and bug fix. Preserve Ruff, mypy, ESLint, TypeScript, backend/frontend tests, OpenAPI generation, Compose rendering, isolation tests, and browser smoke coverage.

---

## File and Boundary Map

- `backend/src/openrag/modules/operations/models.py`: durable RAG fact, error issue/occurrence, and alert rows only.
- `backend/src/openrag/modules/operations/schemas.py`: bounded public admin contracts and filter enums.
- `backend/src/openrag/modules/operations/facts.py`: idempotent fact recording from durable runs without request sessions.
- `backend/src/openrag/modules/operations/errors.py`: recursive redaction, stable fingerprints, grouping, and safe occurrence recording.
- `backend/src/openrag/modules/operations/queries.py`: database-side aggregate/read-model queries.
- `backend/src/openrag/modules/evaluations/`: versioned datasets, cases, runs, results, scoring, and worker runtime.
- `backend/src/openrag/core/telemetry.py`: correlation context, OTEL setup, safe attributes, and metric instruments.
- `backend/src/openrag/api/middleware/correlation.py`: request trace boundary.
- `backend/src/openrag/api/routes/rag_operations.py`: platform-superadmin read/drill-down APIs.
- `backend/src/openrag/api/routes/evaluations.py`: platform-superadmin dataset/run APIs.
- `frontend/src/features/admin/rag-operations/`: query hooks, filters, KPI cards, accessible SVG charts/tables, run/error drill-down, and page tests.
- `deploy/observability/`: collector and Grafana/Prometheus/Loki/Tempo configuration.

---

### Task 1: Durable Safe RAG Facts and Error Groups

**Files:**
- Create: `backend/src/openrag/modules/operations/__init__.py`
- Create: `backend/src/openrag/modules/operations/models.py`
- Create: `backend/src/openrag/modules/operations/schemas.py`
- Create: `backend/migrations/versions/c8e0a3b5d7f9_rag_operations_facts.py`
- Modify: `backend/migrations/env.py`
- Test: `backend/tests/modules/operations/test_models.py`
- Test: `backend/tests/modules/operations/test_schemas.py`
- Modify: `backend/tests/test_migrations.py`

**Interfaces:**
- Produces: `RagRunFact`, `ErrorIssue`, `ErrorOccurrence`, `RagRunOutcome`, `ErrorCategory`, and bounded Pydantic admin DTOs.
- `RagRunFact` is unique on `(org_id, run_id)` and references the tenant-bound durable run.
- `ErrorIssue` is unique on `(environment, service, fingerprint)`; `ErrorOccurrence` references one issue and may reference a run.

- [x] **Step 1: Write failing schema/model tests**

```python
def test_rag_fact_contains_metrics_but_no_raw_content_fields() -> None:
    fields = set(RagRunFact.__table__.columns.keys())
    assert {"run_id", "route", "outcome", "latency_ms", "ttft_ms", "retrieval_ms"} <= fields
    assert not fields & {"prompt", "response", "query", "document_text", "memory"}

def test_error_contract_rejects_unbounded_or_unknown_categories() -> None:
    with pytest.raises(ValidationError):
        ErrorOccurrenceCreate(category="secret", code="x", service="api", exception_type="X")
```

- [x] **Step 2: Run tests and verify they fail because operations models do not exist**

Run: `cd backend && uv run pytest -q tests/modules/operations/test_models.py tests/modules/operations/test_schemas.py`

- [x] **Step 3: Implement bounded models and schemas**

Use closed status/category check constraints, non-negative duration/token/count checks, 64-character lowercase SHA-256 fingerprints, composite tenant foreign keys, bounded strings, and no JSON payload column. `ErrorOccurrence` stores only safe `trace_id`, `run_id`, `code`, `exception_type`, HTTP method/route-template/status, release, and timestamps.

- [x] **Step 4: Add and offline-compile the migration**

Run: `cd backend && uv run alembic heads && uv run pytest -q tests/modules/operations/test_models.py -k migration`

Expected: one head; the isolated PostgreSQL operation stream contains all three creates and reverse-order drops. The full historical chain remains covered by the container-backed migration suite because an older RBAC migration deliberately performs live data validation and cannot render offline.

- [x] **Step 5: Run focused verification and commit**

Run: `cd backend && uv run ruff check src tests/modules/operations && uv run mypy && uv run pytest -q tests/modules/operations`

Commit: `feat: persist safe rag operations facts`

---

### Task 2: Correlation, Recursive Redaction, and Error Recording

**Files:**
- Create: `backend/src/openrag/core/telemetry.py`
- Create: `backend/src/openrag/api/middleware/correlation.py`
- Create: `backend/src/openrag/modules/operations/errors.py`
- Modify: `backend/src/openrag/core/logging.py`
- Modify: `backend/src/openrag/api/app.py`
- Modify: `backend/src/openrag/modules/runs/runner.py`
- Test: `backend/tests/core/test_telemetry.py`
- Test: `backend/tests/api/test_correlation.py`
- Test: `backend/tests/modules/operations/test_errors.py`

**Interfaces:**
- Produces: `current_trace_id() -> str`, `safe_log_fields(value) -> object`, and `record_error(session_factory, occurrence) -> UUID`.
- Consumes: `ErrorIssue` and `ErrorOccurrence` from Task 1.

- [ ] **Step 1: Write failing redaction and correlation tests**

```python
def test_recursive_redaction_bounds_nested_values() -> None:
    value = {"authorization": "Bearer secret", "nested": {"prompt": "private"}, "code": "timeout"}
    assert safe_log_fields(value) == {
        "authorization": "[REDACTED]",
        "nested": {"prompt": "[REDACTED]"},
        "code": "timeout",
    }

async def test_invalid_inbound_trace_id_is_replaced(client) -> None:
    response = await client.get("/healthz", headers={"X-Trace-ID": "../../secret"})
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Trace-ID"])
```

- [ ] **Step 2: Implement server-owned correlation context**

Use `contextvars.ContextVar`, accept only exact `[0-9a-f]{32}`, bind `trace_id`, method, route template, service, release, and environment to structlog, then reset the token after the response.

- [ ] **Step 3: Implement recursive allowlist/redaction and grouping**

Fingerprint only `(category, code, service, exception_type, top_application_frame)`. Never hash or retain exception messages because messages may contain provider or customer data. Upsert issue counts and bounded occurrences in a short independent transaction.

- [ ] **Step 4: Wire safe exception recording**

OpenRAG typed 4xx errors remain expected and are not grouped as internal issues. Unexpected API and worker errors record safe codes and `trace_id`; client problem responses include `trace_id` but no exception detail.

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest -q tests/core/test_telemetry.py tests/api/test_correlation.py tests/modules/operations/test_errors.py && uv run ruff check src tests && uv run mypy`

Commit: `feat: correlate and group safe errors`

---

### Task 3: Idempotent Run Fact Projection

**Files:**
- Create: `backend/src/openrag/modules/operations/facts.py`
- Modify: `backend/src/openrag/modules/runs/runner.py`
- Modify: `backend/src/openrag/modules/runs/lifecycle.py`
- Modify: `backend/src/openrag/modules/chat/service.py`
- Modify: `backend/src/openrag/modules/retrieval/service.py`
- Test: `backend/tests/modules/operations/test_facts.py`
- Test: `backend/tests/modules/runs/test_runner.py`

**Interfaces:**
- Produces: `record_run_fact(session_factory, run_id, observation) -> None` and bounded `RunObservation`.
- Fact writes are idempotent per run and never hold SQL while retrieval or provider calls execute.

- [ ] **Step 1: Write failing idempotency and timing tests**

```python
async def test_projection_is_idempotent_and_derives_terminal_metrics(factory, completed_run) -> None:
    await record_run_fact(factory, completed_run.id, observation)
    await record_run_fact(factory, completed_run.id, observation)
    facts = await load_facts(factory, completed_run.id)
    assert len(facts) == 1
    assert facts[0].latency_ms >= facts[0].ttft_ms >= 0
```

- [ ] **Step 2: Instrument monotonic stage timings**

Measure route, retrieval, provider wait, TTFT, persistence, and total duration with `time.perf_counter_ns()`. Store integer milliseconds only after terminal persistence. Derive prompt/completion tokens from the durable run and retrieval/context counts from bounded recorders.

- [ ] **Step 3: Project every terminal outcome**

Completed, failed, refused/no-answer, and cancelled runs all emit one fact with safe outcome/error code. A projection failure is logged and retried independently; it must not change the already terminal user run.

- [ ] **Step 4: Verify and commit**

Run: `cd backend && uv run pytest -q tests/modules/operations/test_facts.py tests/modules/runs/test_runner.py && uv run ruff check src tests && uv run mypy`

Commit: `feat: project durable rag run metrics`

---

### Task 4: Super-Admin Operations APIs

**Files:**
- Create: `backend/src/openrag/modules/operations/queries.py`
- Create: `backend/src/openrag/api/routes/rag_operations.py`
- Modify: `backend/src/openrag/api/app.py`
- Test: `backend/tests/modules/operations/test_queries.py`
- Test: `backend/tests/api/test_rag_operations.py`

**Interfaces:**
- Produces:
  - `GET /api/v1/admin/rag-operations/overview`
  - `GET /api/v1/admin/rag-operations/series`
  - `GET /api/v1/admin/rag-operations/runs`
  - `GET /api/v1/admin/rag-operations/runs/{run_id}`
  - `GET /api/v1/admin/rag-operations/errors`
  - `GET /api/v1/admin/rag-operations/errors/{issue_id}`
- Filters: UTC `from`, `to`, organization, workspace, route, outcome, model, environment, release; maximum range 90 days and maximum page size 100.

- [ ] **Step 1: Write failing authorization, aggregation, and isolation tests**

```python
async def test_non_platform_admin_cannot_read_operations(client, org_admin_headers) -> None:
    response = await client.get("/api/v1/admin/rag-operations/overview", headers=org_admin_headers)
    assert response.status_code == 403

async def test_filtered_overview_uses_only_requested_workspace(superadmin_client, facts) -> None:
    response = await superadmin_client.get(
        "/api/v1/admin/rag-operations/overview",
        params={"workspace_id": str(facts.workspace_a), "range": "24h"},
    )
    assert response.json()["queries"] == facts.workspace_a_count
```

- [ ] **Step 2: Implement bounded database-side queries**

Use SQL `FILTER`, `date_trunc`, and `percentile_cont` for counts/rates/p50/p95/p99. Use keyset pagination `(accepted_at, run_id)` for run/error lists. Return zero-valued metrics and empty series for periods without data.

- [ ] **Step 3: Add safe drill-down DTOs**

Run detail exposes stage timings, IDs, route/outcome, model/version identifiers, token counts, citation counts, score summaries, attempts, safe error code, and trace link identifier. It excludes message content, retrieval text, document names, memory, and provider payloads.

- [ ] **Step 4: Verify OpenAPI and commit**

Run: `cd backend && uv run pytest -q tests/modules/operations/test_queries.py tests/api/test_rag_operations.py && uv run ruff check src tests && uv run mypy`

Commit: `feat: expose superadmin rag operations api`

---

### Task 5: Full-Stack RAG Operations Dashboard

**Files:**
- Create: `frontend/src/features/admin/rag-operations/queries.ts`
- Create: `frontend/src/features/admin/rag-operations/rag-operations-page.tsx`
- Create: `frontend/src/features/admin/rag-operations/metric-card.tsx`
- Create: `frontend/src/features/admin/rag-operations/throughput-chart.tsx`
- Create: `frontend/src/features/admin/rag-operations/run-table.tsx`
- Create: `frontend/src/features/admin/rag-operations/error-panel.tsx`
- Create: `frontend/src/features/admin/rag-operations/rag-operations-page.test.tsx`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/components/layout/sidebar.tsx`
- Modify: `frontend/src/api/schema.d.ts`

**Interfaces:**
- Consumes: Task 4 operations endpoints.
- Produces: lazy `/admin/rag-operations`, visible only to platform superadmins.

- [ ] **Step 1: Write failing page contract tests**

```tsx
it('renders operational KPIs, accessible chart data, and recent errors', async () => {
  renderPage();
  expect(await screen.findByRole('heading', { name: 'RAG operations' })).toBeVisible();
  expect(screen.getByText('P95 latency')).toBeVisible();
  expect(screen.getByRole('table', { name: 'Query throughput data' })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Recent errors' })).toBeVisible();
});
```

- [ ] **Step 2: Implement coordinated filters and parallel queries**

Use one URL-backed filter state for range, organization/workspace, route, outcome, and model. Fetch overview, series, runs, and errors in parallel with TanStack Query; refetch every 30 seconds only while the tab is visible.

- [ ] **Step 3: Build the fixed responsive visual hierarchy**

Use a four/two/one-column KPI grid, a two-column desktop analysis area, accessible inline SVG throughput/latency charts with point labels and equivalent visually-hidden tables, and scrollable run/error tables. Include skeleton, empty, error/retry, and stale-data states. Do not add a drag/drop dependency.

- [ ] **Step 4: Add drill-down and responsive behavior**

Selecting a run or issue opens a keyboard-accessible dialog showing safe stage metadata and trace identifier. On narrow screens, the dialog is full-screen and tables retain horizontal scroll.

- [ ] **Step 5: Verify and commit**

Run: `cd frontend && pnpm generate:api && pnpm test -- rag-operations && pnpm typecheck && pnpm lint && pnpm build`

Commit: `feat: add superadmin rag operations dashboard`

---

### Task 6: Versioned Evaluation Datasets and Async Scoring

**Files:**
- Create: `backend/src/openrag/modules/evaluations/__init__.py`
- Create: `backend/src/openrag/modules/evaluations/models.py`
- Create: `backend/src/openrag/modules/evaluations/schemas.py`
- Create: `backend/src/openrag/modules/evaluations/metrics.py`
- Create: `backend/src/openrag/modules/evaluations/runtime.py`
- Create: `backend/src/openrag/api/routes/evaluations.py`
- Create: `backend/migrations/versions/d9f1b4c6e8a0_rag_evaluations.py`
- Modify: `backend/src/openrag/worker/celery_app.py`
- Modify: `backend/src/openrag/worker/tasks.py`
- Modify: `deploy/compose.yaml`
- Test: `backend/tests/modules/evaluations/test_metrics.py`
- Test: `backend/tests/modules/evaluations/test_runtime.py`
- Test: `backend/tests/api/test_evaluations.py`

**Interfaces:**
- Produces versioned datasets/cases, queued evaluation runs, per-case retrieval/answer/citation/refusal results, and aggregate recall@k, precision@k, MRR, nDCG, citation precision/recall, groundedness, answer relevance, and correct-refusal rates.
- Evaluation jobs run on queue `evaluations` with an explicit case count, token, and estimated-cost budget.

- [ ] **Step 1: Write deterministic metric tests**

```python
def test_rank_metrics_match_known_order() -> None:
    result = rank_metrics(retrieved=["b", "a", "c"], relevant={"a", "c"}, k=3)
    assert result.recall == 1.0
    assert result.precision == pytest.approx(2 / 3)
    assert result.mrr == 0.5
    assert 0 < result.ndcg <= 1
```

- [ ] **Step 2: Add immutable dataset versions and budgeted run state**

Cases store bounded questions plus expected immutable document-version/evidence IDs; only this explicitly approved evaluation corpus may retain question text. Dataset versions are immutable once used. Runs are lease-fenced and terminally idempotent.

- [ ] **Step 3: Implement deterministic retrieval/citation/refusal scoring**

Every case executes through the same tenant-authorized production retrieval path. Store identifiers and numeric scores, not retrieved text or model reasoning. Live LLM-judge scoring is optional per run, uses a configured evaluator model through in-process LiteLLM, has a closed JSON schema, and fails independently from deterministic metrics.

- [ ] **Step 4: Wire isolated worker/API and verify**

Run: `cd backend && uv run pytest -q tests/modules/evaluations tests/api/test_evaluations.py tests/worker/test_celery.py tests/test_compose.py && uv run ruff check src tests && uv run mypy`

Commit: `feat: evaluate rag with versioned golden datasets`

---

### Task 7: Evaluation and Regression UI

**Files:**
- Create: `frontend/src/features/admin/evaluations/evaluations-page.tsx`
- Create: `frontend/src/features/admin/evaluations/queries.ts`
- Create: `frontend/src/features/admin/evaluations/evaluations-page.test.tsx`
- Modify: `frontend/src/features/admin/rag-operations/rag-operations-page.tsx`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/components/layout/sidebar.tsx`
- Modify: `frontend/src/api/schema.d.ts`

**Interfaces:**
- Consumes: Task 6 dataset and run endpoints.
- Produces: `/admin/evaluations`, dataset import/editor, budget confirmation, run progress, metric matrix, case failures, and two-run regression comparison.

- [ ] **Step 1: Write failing accessibility and workflow tests**

```tsx
it('requires a budget confirmation and compares regressions accessibly', async () => {
  renderEvaluationsPage();
  await user.click(await screen.findByRole('button', { name: 'Run evaluation' }));
  expect(screen.getByText('Maximum evaluation tokens')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Confirm and run' }));
  expect(await screen.findByRole('status')).toHaveTextContent('Evaluation queued');
  expect(screen.getByRole('table', { name: 'Evaluation metric comparison' })).toBeVisible();
});
```

- [ ] **Step 2: Implement dataset/case management with immutable-version messaging**

- [ ] **Step 3: Implement budget-confirmed run creation and progress polling**

- [ ] **Step 4: Implement regression comparison with metric deltas and non-color indicators**

- [ ] **Step 5: Verify and commit**

Run: `cd frontend && pnpm generate:api && pnpm test -- evaluations && pnpm typecheck && pnpm lint && pnpm build`

Commit: `feat: add rag evaluation and regression ui`

---

### Task 8: OTLP, Central Logs, Traces, Metrics, and Grafana Profile

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Modify: `backend/src/openrag/core/config.py`
- Modify: `backend/src/openrag/core/telemetry.py`
- Modify: `deploy/compose.yaml`
- Create: `deploy/observability/otel-collector.yaml`
- Create: `deploy/observability/prometheus.yaml`
- Create: `deploy/observability/loki.yaml`
- Create: `deploy/observability/tempo.yaml`
- Create: `deploy/observability/grafana/provisioning/datasources/openrag.yaml`
- Create: `deploy/observability/grafana/provisioning/dashboards/openrag.yaml`
- Create: `deploy/observability/grafana/dashboards/openrag-platform.json`
- Test: `backend/tests/core/test_telemetry_export.py`
- Modify: `backend/tests/test_compose.py`

**Interfaces:**
- Produces opt-in Compose profile `observability` with OTLP collector, Prometheus, Loki, Tempo, and Grafana.
- Services export OTLP over the private Compose network; only Grafana binds to loopback.

- [ ] **Step 1: Write failing no-export and Compose isolation tests**

```python
def test_telemetry_is_noop_without_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("OPENRAG_OTEL_ENDPOINT", raising=False)
    runtime = build_telemetry(Settings(_env_file=None))
    assert runtime.export_enabled is False

def test_observability_stores_are_not_host_exposed() -> None:
    services = render_compose("--profile", "observability")["services"]
    assert "ports" not in services["prometheus"]
    assert "ports" not in services["loki"]
    assert "ports" not in services["tempo"]
    assert services["grafana"]["ports"][0]["host_ip"] == "127.0.0.1"
```

- [ ] **Step 2: Pin OpenTelemetry dependencies and configure batch exporters**

When `OPENRAG_OTEL_ENDPOINT` is absent, providers are no-op. When present, export traces, metrics, and logs with bounded queues, batch timeouts, resource attributes, and recursive redaction before export.

- [ ] **Step 3: Add pinned observability services and private storage**

Collector receives OTLP, applies memory limiting/batching/attribute filtering, and exports to the three stores. Prometheus, Loki, and Tempo have no host ports. Grafana binds to `127.0.0.1:${OPENRAG_GRAFANA_PORT:-53000}:3000`, uses provisioned datasources, and has authentication enabled.

- [ ] **Step 4: Add actionable alerts and dashboards**

Provision p95 latency, TTFT, error/no-answer rate, provider failure, run/ingest queue age, event-loop lag, DB pool saturation, retrieval pass rate, citation coverage, and evaluation regression panels. Alert rules use low-cardinality labels only.

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest -q tests/core/test_telemetry_export.py tests/test_compose.py && uv run ruff check src tests && uv run mypy && cd .. && docker compose -f deploy/compose.yaml --profile observability config --quiet`

Commit: `feat: add private openrag observability stack`

---

### Task 9: End-to-End Security, Scale, and Operations Acceptance

**Files:**
- Create: `frontend/e2e/rag-operations.spec.ts`
- Create: `backend/tests/security/test_operations_redaction.py`
- Create: `backend/tests/security/test_operations_isolation.py`
- Create: `backend/tests/load/test_operations_queries.py`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Proves the complete operations slice against the approved design and records measured limits without claiming universal accuracy.

- [ ] **Step 1: Add adversarial redaction and authorization tests**

Inject secrets into nested exception messages, headers, filenames, prompts, retrieved text, and memory, then assert none appear in PostgreSQL error rows, JSON logs, OTLP test exporter payloads, API responses, or dashboard DOM.

```python
@pytest.mark.parametrize("secret", ["Bearer top-secret", "sk-provider-secret", "private prompt"])
async def test_secret_never_reaches_operations_surfaces(secret, operations_harness) -> None:
    await operations_harness.raise_nested_error(secret)
    assert secret not in await operations_harness.persisted_error_text()
    assert secret not in operations_harness.captured_logs()
    assert secret not in operations_harness.captured_otlp()
```

- [ ] **Step 2: Add multi-tenant and superadmin browser tests**

Prove ordinary users and organization admins receive 403 for every operations/evaluation route. Prove platform superadmin filters do not mix organization/workspace counts and drill-down IDs cannot cross the selected scope.

```ts
test('operations navigation and data require platform superadmin', async ({ page }) => {
  await loginAsOrgAdmin(page);
  await page.goto('/admin/rag-operations');
  await expect(page).not.toHaveURL(/rag-operations/);
  const response = await page.request.get('/api/v1/admin/rag-operations/overview');
  expect(response.status()).toBe(403);
});
```

- [ ] **Step 3: Add bounded-query and 100-user validation**

Seed at least one million synthetic fact rows using PostgreSQL set-based inserts, run `EXPLAIN (ANALYZE, BUFFERS)` for overview/series/list queries, and require indexed plans with bounded pages. During the existing 100-user stream test, poll the dashboard and verify p95 API latency and DB pool saturation remain within documented limits.

- [ ] **Step 4: Run the complete acceptance matrix**

Run backend tests, Ruff, mypy, import-linter, migration upgrade/downgrade, frontend tests/type/lint/build, Playwright, Compose application and observability profiles, secret scan, dependency audit, 100-user load, and cross-tenant leakage tests. Record Docker-storage limitations honestly until the daemon can run container-backed checks.

- [ ] **Step 5: Update deployment/operations documentation and commit**

Document local and production OTLP endpoints, retention, Grafana access, alert routing, evaluation budgets, dashboard interpretation, backup/restore, and privacy exclusions.

Commit: `docs: complete rag operations runbook`

---

## Plan Self-Review

- **Spec coverage:** Tasks 1-5 cover product facts, safe errors, APIs, and the dashboard; Tasks 6-7 cover online/offline evaluation and regression; Task 8 covers OTEL and centralized logs/traces/metrics; Task 9 covers privacy, authorization, scale, load, and operational documentation.
- **No scope substitution:** This plan keeps the complete observability/evaluation requirement. Early commits are deployable increments, not a claim that later tasks are unnecessary.
- **Type consistency:** Durable run facts are the source for operations queries; evaluation tables remain separate; frontend endpoints exactly match Task 4 and Task 6 routes.
- **Privacy consistency:** Raw user/company/provider content is excluded from operations facts, errors, logs, traces, APIs, and dashboard. The only approved text exception is explicit golden-dataset case questions.
- **Execution mode:** Inline single-agent execution is already selected by the user's instruction. Use `superpowers:executing-plans`, not subagent-driven development.
