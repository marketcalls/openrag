# Structured Analytics Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The repository owner selected inline, single-agent execution for this plan.

**Goal:** Add source-grounded, persisted, replayable analytical responses with safe KPI cards, charts, tables, explainers, follow-up prompts, accessible fallbacks, and exports.

**Architecture:** The normal grounded Markdown answer remains authoritative and must pass the existing citation contract first. For an `analytics` route only, a measured structured-output model may perform one bounded post-answer composition call over the validated answer and the same numbered evidence; a strict Pydantic contract validates every block and citation marker before an immutable tenant-scoped artifact is persisted. The durable event stream carries a bounded validated artifact, and historical chat reads return the same artifact for the frontend's closed component registry.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, PostgreSQL JSONB, Alembic, Agno with in-process LiteLLM, React 18, TypeScript, Tailwind CSS, Vitest, Testing Library.

## Global Constraints

- LiteLLM is used only as an in-process Python library; no LiteLLM Proxy and no direct OpenAI SDK.
- An artifact is supplemental presentation. It cannot change, weaken, or replace the validated grounded answer.
- No artifact is produced for direct, conversation, clarify, refused, no-answer, or uncited responses.
- Every KPI and block has at least one source marker, and every marker must exist in the persisted citation snapshot.
- The contract accepts no HTML, JavaScript, URLs, executable expressions, arbitrary CSS, SVG, Vega, Mermaid, iframe, or model-selected React component names.
- `analytics.v1` is the only accepted schema version; unknown kinds and fields fail closed.
- A serialized artifact is at most 49,152 UTF-8 bytes; at most 8 KPIs, 12 blocks, 50 chart categories, 8 series, 200 table rows, 12 columns, and 5 follow-ups.
- Persisted artifacts are immutable, tenant-scoped, content-hashed, cascade with the owning message/chat, and never appear in logs or traces.
- Durable public events remain at most 65,536 bytes and expose only validated user-facing artifact data.
- Frontend rendering uses the closed OpenRAG registry and existing sanitized Markdown renderer; unsafe runtime payloads render a safe fallback, never partial HTML.
- Reference repositories `anything-llm/`, `openui/`, and `raghub/` remain read-only and untracked.

---

### Task 1: Strict analytics contract and immutable persistence

**Files:**
- Create: `backend/src/openrag/modules/artifacts/__init__.py`
- Create: `backend/src/openrag/modules/artifacts/schemas.py`
- Create: `backend/src/openrag/modules/artifacts/models.py`
- Create: `backend/src/openrag/modules/artifacts/service.py`
- Create: `backend/tests/modules/artifacts/test_schemas.py`
- Create: `backend/tests/modules/artifacts/test_service.py`
- Create: `backend/alembic/versions/<revision>_add_message_analytics_artifacts.py`
- Modify: `backend/src/openrag/db_models.py`
- Modify: `backend/tests/test_migrations.py`

**Interfaces:**
- Produces: `AnalyticsResponseV1`, `AnalyticsKpiV1`, `AnalyticsChartBlockV1`, `AnalyticsTableBlockV1`, `AnalyticsExplainerBlockV1`, `MessageArtifact`, `serialize_analytics_artifact()`, `persist_analytics_artifact()`, and `list_message_artifacts()`.
- Persistence key: one `kind="analytics"` artifact per `(org_id, workspace_id, message_id)`.

- [ ] **Step 1: Write failing schema tests**

```python
def test_analytics_v1_accepts_closed_bounded_blocks_and_normalizes_markers() -> None:
    artifact = AnalyticsResponseV1.model_validate(valid_payload())
    assert artifact.schema_version == "analytics.v1"
    assert artifact.kpis[0].source_markers == [1]


@pytest.mark.parametrize("mutation", unsafe_mutations())
def test_analytics_v1_rejects_unknown_executable_or_unbounded_payloads(mutation) -> None:
    with pytest.raises(ValidationError):
        AnalyticsResponseV1.model_validate(mutation(valid_payload()))
```

- [ ] **Step 2: Run the schema tests and confirm import failure**

Run: `cd backend && .venv/bin/pytest tests/modules/artifacts/test_schemas.py -q`

Expected: FAIL because `openrag.modules.artifacts.schemas` does not exist.

- [ ] **Step 3: Implement the strict discriminated union**

```python
AnalyticsScalar = str | int | float | None

class AnalyticsKpiV1(StrictArtifactModel):
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=80)
    detail: str | None = Field(default=None, max_length=200)
    trend: Literal["up", "down", "flat", "none"] = "none"
    source_markers: list[int] = Field(min_length=1, max_length=16)

class AnalyticsChartBlockV1(StrictArtifactModel):
    kind: Literal["bar_chart", "line_chart"]
    title: str = Field(min_length=1, max_length=160)
    x_label: str = Field(min_length=1, max_length=80)
    y_label: str = Field(min_length=1, max_length=80)
    categories: list[str] = Field(min_length=1, max_length=50)
    series: list[AnalyticsSeriesV1] = Field(min_length=1, max_length=8)
    source_markers: list[int] = Field(min_length=1, max_length=16)

class AnalyticsTableBlockV1(StrictArtifactModel):
    kind: Literal["table"]
    title: str = Field(min_length=1, max_length=160)
    columns: list[AnalyticsColumnV1] = Field(min_length=1, max_length=12)
    rows: list[dict[str, AnalyticsScalar]] = Field(max_length=200)
    source_markers: list[int] = Field(min_length=1, max_length=16)

class AnalyticsExplainerBlockV1(StrictArtifactModel):
    kind: Literal["explainer"]
    title: str = Field(min_length=1, max_length=160)
    body_markdown: str = Field(min_length=1, max_length=8_000)
    source_markers: list[int] = Field(min_length=1, max_length=16)

class AnalyticsResponseV1(StrictArtifactModel):
    schema_version: Literal["analytics.v1"]
    title: str = Field(min_length=1, max_length=160)
    subtitle: str | None = Field(default=None, max_length=240)
    kpis: list[AnalyticsKpiV1] = Field(max_length=8)
    blocks: list[AnalyticsBlockV1] = Field(min_length=1, max_length=12)
    suggested_followups: list[str] = Field(max_length=5)
```

Add model validators that reject duplicate/invalid markers, non-finite numbers, chart series/category length mismatches, duplicate column keys, row keys outside the declared columns, empty analytical content, control characters, and serialized values over 49,152 bytes.

- [ ] **Step 4: Add persistence tests before the model**

```python
async def test_artifact_persistence_is_tenant_scoped_idempotent_and_immutable(session, seeded_message):
    first = await persist_analytics_artifact(session, identity, seeded_message.id, artifact)
    second = await persist_analytics_artifact(session, identity, seeded_message.id, artifact)
    assert second.id == first.id
    assert second.content_hash == first.content_hash
    assert await list_message_artifacts(session, foreign_identity, [seeded_message.id]) == {}
```

- [ ] **Step 5: Implement the ORM model, migration, and service**

Create `message_artifacts` with composite tenant/message foreign key, `kind`, `schema_version`, `payload JSONB`, `content_hash CHAR(64)`, `created_at`, unique `(org_id, workspace_id, message_id, kind)`, JSON object/size/version/hash checks, and an immutable update trigger. The service validates the message under exact tenant scope, canonicalizes JSON with sorted keys, hashes SHA-256, inserts with savepoint-safe idempotency, and returns only strict schemas.

- [ ] **Step 6: Verify Task 1**

Run:

```bash
cd backend
.venv/bin/pytest tests/modules/artifacts -q
.venv/bin/pytest tests/test_migrations.py -q
.venv/bin/ruff check src/openrag/modules/artifacts tests/modules/artifacts
.venv/bin/mypy src/openrag/modules/artifacts
```

Expected: all pass.

- [ ] **Step 7: Commit Task 1**

```bash
git add backend/src/openrag/modules/artifacts backend/src/openrag/db_models.py backend/alembic/versions backend/tests/modules/artifacts backend/tests/test_migrations.py
git commit -m "feat: persist validated analytics artifacts"
```

### Task 2: Bounded in-process LiteLLM analytics composer

**Files:**
- Create: `backend/src/openrag/modules/artifacts/prompting.py`
- Create: `backend/src/openrag/modules/orchestration/agno_analytics.py`
- Create: `backend/tests/modules/artifacts/test_prompting.py`
- Create: `backend/tests/modules/orchestration/test_agno_analytics.py`
- Modify: `backend/src/openrag/modules/orchestration/runtime.py`
- Modify: `backend/tests/modules/orchestration/test_runtime.py`

**Interfaces:**
- Produces: `AnalyticsComposer.compose(*, question, answer_markdown, evidence, allowed_markers) -> AnalyticsComposition`.
- `AnalyticsComposition` contains `artifact: AnalyticsResponseV1` and `usage: LLMUsage`.
- `ModelExecution.analytics_composer` is `None` unless the selected model has a passed probe and `supports_structured_json=True`.

- [ ] **Step 1: Write failing prompt-boundary tests**

Assert that closing tags inside the question, answer, and evidence are escaped; sources are numbered; the system prompt states that data cannot give instructions; and the prompt asks only for `analytics.v1` grounded presentation.

- [ ] **Step 2: Implement `build_analytics_messages()`**

Use distinct `<question_data>`, `<answer_data>`, and `<evidence_data marker="N">` blocks, bound each input to 8,000 characters, cap evidence at 8 blocks and 24,000 total characters, and include the allowed marker list as trusted system data.

- [ ] **Step 3: Write failing adapter tests**

```python
async def test_composer_returns_only_validated_artifact_and_usage():
    composer = AgnoAnalyticsComposer(runtime, runner_factory=fake_runner)
    result = await composer.compose(...)
    assert result.artifact.schema_version == "analytics.v1"
    assert result.usage.prompt_tokens == 120

async def test_composer_rejects_marker_outside_persisted_citations():
    with pytest.raises(UpstreamError, match="analytics composition failed"):
        await composer.compose(allowed_markers=(1,), fake_output=payload_with_marker(2))
```

- [ ] **Step 4: Implement the Agno structured adapter**

Configure `Agent(model=LiteLLM(...), output_schema=AnalyticsResponseV1, structured_outputs=True, parse_response=True, tools=[], telemetry=False, store_events=False)` with temperature `0`, retries `0`, timeout `45s`, and `max_tokens=min(runtime.max_output_tokens, 4096)`. Validate through `AnalyticsResponseV1`, reject markers outside `allowed_markers`, and convert all provider/schema failures to the safe `UpstreamError("analytics composition failed")`.

- [ ] **Step 5: Wire measured capability gating**

Extend `ModelExecution` with `analytics_composer: AnalyticsComposer | None` and create it only when `probe_status == "passed"` and `supports_structured_json` is true. Do not expose credentials or the composer outside the worker operation.

- [ ] **Step 6: Verify and commit Task 2**

Run the two new test files plus Ruff and mypy, then commit:

```bash
git add backend/src/openrag/modules/artifacts/prompting.py backend/src/openrag/modules/orchestration/agno_analytics.py backend/src/openrag/modules/orchestration/runtime.py backend/tests/modules/artifacts/test_prompting.py backend/tests/modules/orchestration
git commit -m "feat: compose grounded analytics safely"
```

### Task 3: Grounded persistence, historical reads, and durable replay

**Files:**
- Modify: `backend/src/openrag/modules/chat/events.py`
- Modify: `backend/src/openrag/modules/chat/schemas.py`
- Modify: `backend/src/openrag/modules/chat/service.py`
- Modify: `backend/src/openrag/modules/runs/reply_bridge.py`
- Modify: `backend/src/openrag/modules/runs/runner.py`
- Modify: `backend/tests/modules/chat/test_service.py`
- Modify: `backend/tests/modules/runs/test_reply_bridge.py`
- Modify: `backend/tests/api/test_chat.py`

**Interfaces:**
- Produces legacy internal `SSEEvent("analytics_artifact", {"artifact": ...})`.
- Produces durable `artifact.created` payload `{message_id, artifact}` before `message.completed`.
- `MessageNode.artifacts` is `list[MessageArtifactOut]`, empty for all existing messages.

- [ ] **Step 1: Write failing chat-service tests**

Cover analytics route + valid citations + capable composer produces and persists exactly one artifact; RAG/direct/no-answer/refusal/unmeasured-model/composer-error paths produce none; an invalid artifact never changes the grounded answer; and regeneration creates a distinct artifact on the new sibling message.

- [ ] **Step 2: Integrate composition after grounding succeeds**

After `_validate_strict_draft()` returns persisted citations, call the composer only when the deterministic route is `ANALYTICS`, citations are non-empty, and the validated answer is not refused. Pass only the validated answer, bounded evidence, and persisted marker set. Persist the message and artifact atomically; on composer failure persist the grounded message without an artifact and record only the safe operational error code.

- [ ] **Step 3: Write failing durable bridge tests**

Assert `analytics_artifact` becomes one idempotent `artifact.created` event, invalid payloads fail the run, payloads above the event limit fail closed, and replay order is `artifact.created`, `message.completed`, `usage.updated`, `run.completed`.

- [ ] **Step 4: Add historical artifact reads**

Load artifacts for all message IDs in one tenant-scoped query and attach strict `MessageArtifactOut` values in `build_tree()`. Do not issue one query per message.

- [ ] **Step 5: Verify and commit Task 3**

Run focused chat, run bridge, API, Ruff, mypy, and migration tests, then commit:

```bash
git add backend/src/openrag/modules/chat backend/src/openrag/modules/runs backend/tests/modules/chat backend/tests/modules/runs backend/tests/api/test_chat.py
git commit -m "feat: replay analytical chat artifacts"
```

### Task 4: Closed frontend registry and live/historical rendering

**Files:**
- Modify: `frontend/src/api/schema.d.ts` through OpenAPI generation
- Modify: `frontend/src/api/types.ts`
- Create: `frontend/src/features/chat/analytics/contract.ts`
- Create: `frontend/src/features/chat/analytics/analytics-artifact.tsx`
- Create: `frontend/src/features/chat/analytics/chart-block.tsx`
- Create: `frontend/src/features/chat/analytics/table-block.tsx`
- Create: `frontend/src/features/chat/analytics/export.ts`
- Create: `frontend/src/features/chat/analytics/analytics-artifact.test.tsx`
- Create: `frontend/src/features/chat/analytics/contract.test.ts`
- Modify: `frontend/src/features/chat/durable-stream.ts`
- Modify: `frontend/src/features/chat/stream.ts`
- Modify: `frontend/src/features/chat/use-chat-stream.ts`
- Modify: `frontend/src/features/chat/assistant-message.tsx`
- Modify: `frontend/src/features/chat/streaming-message.tsx`
- Modify: `frontend/src/features/chat/chat-page.tsx`

**Interfaces:**
- Produces `parseAnalyticsArtifact(value: unknown): AnalyticsResponseV1 | null`.
- `ChatSseEvent` gains `{type: "artifact"; artifact: AnalyticsResponseV1}`.
- `ChatStreamState` gains `artifact: AnalyticsResponseV1 | null`.

- [ ] **Step 1: Write failing runtime-contract tests**

Test every allowed block, unknown keys/kinds, non-finite numbers, mismatched chart shapes, table schema mismatch, unsafe strings, excessive arrays, invalid markers, and oversized JSON. Invalid values return `null` without throwing.

- [ ] **Step 2: Implement the dependency-free runtime parser**

Use explicit object/array/scalar guards and exact-key checks rather than TypeScript casts. Preserve only the closed contract. Never render a field the parser has not validated.

- [ ] **Step 3: Write failing component tests**

```tsx
expect(screen.getByRole('heading', { name: 'Revenue dashboard' })).toBeVisible();
expect(screen.getByRole('img', { name: 'Monthly revenue' })).toBeVisible();
expect(screen.getByRole('table', { name: 'Revenue summary' })).toBeVisible();
expect(screen.getByRole('button', { name: 'Ask: Break this down by product line' })).toBeVisible();
expect(screen.getByRole('button', { name: 'Export Revenue summary as CSV' })).toBeVisible();
```

- [ ] **Step 4: Implement accessible renderers**

Render KPI cards, SVG bar/line charts with titles/descriptions and a screen-reader table, semantic HTML tables with sticky headers, explainers through the existing sanitized Markdown component, source-marker chips through the existing citation context, and follow-up buttons that populate/send the chat input. Never use `dangerouslySetInnerHTML`.

- [ ] **Step 5: Implement deterministic exports**

CSV export quotes cells according to RFC 4180, prefixes spreadsheet-formula-leading strings (`=`, `+`, `-`, `@`, tab, carriage return) with an apostrophe, uses a server-independent Blob URL, and revokes the URL. JSON export contains only the validated `analytics.v1` object.

- [ ] **Step 6: Wire live and historical artifacts**

The durable stream parser accepts `artifact.created` only through `parseAnalyticsArtifact`; the reducer stores it; streaming and historical assistant messages render the same component; interrupted/uncommitted runs clear provisional artifacts.

- [ ] **Step 7: Verify and commit Task 4**

Run focused tests, the full frontend suite, ESLint, and production build, then commit:

```bash
git add frontend/src/api frontend/src/features/chat
git commit -m "feat: render analytical chat artifacts"
```

### Task 5: Security, accessibility, documentation, and live acceptance

**Files:**
- Create: `backend/tests/security/test_analytics_artifacts.py`
- Create: `frontend/e2e/analytics-artifact.spec.ts`
- Modify: `backend/tests/test_tenant_isolation.py`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-07-19-openrag-production-agentic-rag-design.md`

**Interfaces:**
- Produces repeatable security and browser acceptance evidence for the entire slice.

- [ ] **Step 1: Add adversarial backend tests**

Cover cross-tenant artifact reads, prompt injection requesting HTML/script/tool execution, invalid citation markers, NaN/Infinity, JSON bombs, oversized strings/rows, duplicate keys, control characters, credential/log redaction, and concurrent idempotent persistence.

- [ ] **Step 2: Add browser acceptance**

Exercise a live analytics request, durable reconnect/replay, historical reload, keyboard navigation, dark mode, mobile viewport, chart screen-reader table, CSV formula defense, refusal without artifact, and deletion cascade.

- [ ] **Step 3: Document operator behavior**

Document capability gating, bounded extra model cost, fallback behavior, artifact limits, supported block types, exports, content-free observability, and the rule that artifact generation never upgrades insufficient evidence into an answer.

- [ ] **Step 4: Run the release gate**

```bash
cd backend
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src
cd ../frontend
corepack pnpm test -- --run
corepack pnpm lint
corepack pnpm build
corepack pnpm exec playwright test e2e/analytics-artifact.spec.ts
cd ..
./scripts/smoke-compose.sh
```

Expected: every command passes; the Compose command requires a healthy Docker engine and at least 10 GiB free disk space.

- [ ] **Step 5: Commit and push the completed slice**

```bash
git add README.md CLAUDE.md docs backend/tests frontend/e2e
git commit -m "docs: operate safe analytics artifacts"
git push origin main
```

## Plan Self-Review

- Spec coverage: contract, generation, persistence, durable replay, historical reads, charts, tables, KPI cards, explainers, follow-ups, accessibility, exports, tenant isolation, prompt injection, observability behavior, and live acceptance each map to a task.
- Deliberate exclusions: arbitrary plugins, executable chart grammars, HTML, model-selected components, editable artifacts, and image export are excluded because they expand the attack surface without being required for the approved outcome.
- Type consistency: `AnalyticsResponseV1`, `AnalyticsComposition`, `analytics_artifact`, `artifact.created`, and the frontend `artifact` event retain the same names and version across tasks.
- Placeholder scan: the plan contains no deferred implementation marker; the migration revision filename is generated by Alembic at execution and then referenced by its concrete name in the commit.
