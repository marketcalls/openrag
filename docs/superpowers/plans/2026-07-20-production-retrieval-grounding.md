# Production Retrieval and Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every substantive OpenRAG answer is built only from current, approved, authorized, citation-ready evidence and refuses before generation when that evidence is insufficient.

**Architecture:** PostgreSQL remains authoritative for version lifecycle, workspace scope, document ACL policy, evidence identity, and citation provenance. Qdrant performs bounded dense+sparse candidate generation using tenant/workspace/current-generation filters; every candidate is then joined back to authoritative evidence in one bounded SQL query before prompt construction. Retrieval returns immutable citation-ready evidence, a deterministic sufficiency decision, and safe refusal reasons; chat persists only verified authority citations.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, PostgreSQL 16, Qdrant hybrid RRF, LiteLLM-backed embeddings, Pydantic v2, pytest, Alembic.

## Global Constraints

- OpenRAG is strict closed-book by default for substantive company-knowledge questions.
- Only current approved, provenance-ready, non-superseded, effective, non-expired versions may reach prompts.
- Every successful substantive answer must cite document name, version, section, and page/slide/sheet locator.
- Qdrant is derived storage; PostgreSQL revalidation is mandatory before prompt inclusion and citation persistence.
- Document text, prompts, credentials, raw provider errors, and reasoning never enter events, logs, traces, or safe error fields.
- Candidate breadth, SQL IDs, context size, and provider work are bounded.
- All completion and embedding traffic uses the configured LiteLLM path; no direct OpenAI SDK is introduced.
- Work is single-agent, TDD-first, committed and pushed in small checkpoints. `anything-llm/`, `openui/`, and `raghub/` remain untracked references.

---

### Task 1: Authoritative candidate revalidation

**Files:**
- Create: `backend/src/openrag/modules/retrieval/authority.py`
- Modify: `backend/src/openrag/modules/retrieval/service.py`
- Create: `backend/tests/modules/retrieval/test_authority_unit.py`
- Modify: `backend/tests/modules/retrieval/test_retrieve.py`

**Interfaces:**
- Consumes: bounded Qdrant payload identities `(document_version_id, evidence_span_id)` and `TenantContext`.
- Produces: `revalidate_candidates(session, context, workspace_id, candidates, now) -> list[AuthorizedEvidence]`.
- `AuthorizedEvidence` contains IDs, document name, version label, section path, locator, content hash, text, and retrieval scores required by later tasks.

- [ ] **Step 1: Write the failing lifecycle and tenant tests**

```python
async def test_revalidation_drops_superseded_obsolete_expired_and_cross_tenant_candidates():
    result = await revalidate_candidates(session, context, workspace.id, candidates, now)
    assert [item.document_version_id for item in result] == [current_approved.id]

async def test_revalidation_requires_exact_evidence_identity_and_content_hash():
    result = await revalidate_candidates(session, context, workspace.id, tampered_candidates, now)
    assert result == []
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `cd backend && uv run pytest -q tests/modules/retrieval/test_authority_unit.py`

Expected: collection fails because `retrieval.authority` does not exist.

- [ ] **Step 3: Implement the bounded authority query**

```python
@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    document_version_id: UUID
    evidence_span_id: UUID
    dense_score: float | None
    sparse_score: float | None
    fused_score: float

async def revalidate_candidates(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    candidates: Sequence[CandidateIdentity],
    now: datetime,
) -> list[AuthorizedEvidence]:
    if len(candidates) > MAX_CANDIDATES:
        raise ValueError("candidate_limit_exceeded")
    # One parameterized join across Document, DocumentVersion,
    # DocumentEvidenceSpan, and DocumentChunk; require exact org/workspace,
    # approved+ready+unsuperseded, effective/expiry dates, and source present.
    # Preserve Qdrant rank order only after matching exact evidence IDs/hash.
```

For non-null `Document.acl_policy`, deny unless the current policy schema can be evaluated from `TenantContext`; unknown ACL operators fail closed.

- [ ] **Step 4: Integrate post-validation before `RetrievedChunk` construction**

Parse only bounded identity/score metadata from Qdrant. Do not trust Qdrant document names, versions, sections, hashes, or text as authoritative; load those from PostgreSQL evidence/chunk rows.

- [ ] **Step 5: Verify GREEN and commit**

Run:

```bash
cd backend
uv run pytest -q tests/modules/retrieval/test_authority_unit.py
uv run pytest --collect-only -q tests/modules/retrieval/test_retrieve.py
uv run ruff check src tests
uv run mypy src
```

Commit: `feat: revalidate retrieval candidates against authority`

---

### Task 2: Citation-ready hybrid retrieval result

**Files:**
- Modify: `backend/src/openrag/modules/retrieval/service.py`
- Modify: `backend/src/openrag/modules/retrieval/schemas.py`
- Modify: `backend/tests/modules/retrieval/test_retrieve.py`
- Create: `backend/tests/modules/retrieval/test_result_contract.py`

**Interfaces:**
- Consumes: `AuthorizedEvidence` from Task 1.
- Produces: `RetrievedEvidence` and `RetrievalResult(evidence, decision)`; no legacy identifier synthesis is allowed for authority generations.

- [ ] **Step 1: Write the failing immutable result-contract tests**

```python
def test_retrieved_evidence_contains_complete_citation_snapshot():
    assert evidence.document_id
    assert evidence.document_version_id
    assert evidence.evidence_span_id
    assert evidence.document_name == "HSE Manual"
    assert evidence.version_label == "Rev 4"
    assert evidence.section_path == ("Emergency", "Evacuation")
    assert evidence.page_number == 17
    assert len(evidence.content_hash) == 64
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/modules/retrieval/test_result_contract.py`

Expected: `RetrievedEvidence` is missing.

- [ ] **Step 3: Replace the legacy chunk DTO for authority retrieval**

```python
@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    document_id: UUID
    document_version_id: UUID
    evidence_span_id: UUID
    document_name: str
    version_label: str
    section_path: tuple[str, ...]
    locator_kind: str
    locator_label: str
    page_number: int
    chunk_ref: str
    content_hash: str
    text: str
    dense_score: float | None
    sparse_score: float | None
    fused_score: float
    rerank_score: float | None
```

Keep the legacy collection behind an explicit compatibility adapter only until every workspace has an active authority generation. Authority-enabled workspaces must never fall back silently.

- [ ] **Step 4: Bound, deduplicate, and diversify final evidence**

Collapse duplicate content hashes, cap evidence per document and section, preserve fused rank, and enforce `1 <= top_k <= 32` plus a bounded candidate multiplier.

- [ ] **Step 5: Verify and commit**

Run:

```bash
cd backend
uv run pytest -q tests/modules/retrieval/test_result_contract.py
uv run pytest --collect-only -q tests/modules/retrieval/test_retrieve.py
uv run ruff check src tests
uv run mypy src
```

Commit: `feat: return citation ready hybrid evidence`

---

### Task 3: Deterministic evidence sufficiency

**Files:**
- Create: `backend/src/openrag/modules/retrieval/sufficiency.py`
- Modify: `backend/src/openrag/modules/retrieval/service.py`
- Create: `backend/tests/modules/retrieval/test_sufficiency.py`

**Interfaces:**
- Consumes: final `RetrievedEvidence`, workspace `min_score`, and bounded query text.
- Produces: `EvidenceDecision(status, reason_code, evidence)` where status is `sufficient`, `insufficient`, or `conflict`.

- [ ] **Step 1: Write failing policy tests**

```python
def test_missing_provenance_refuses_even_with_high_score():
    assert evaluate_evidence(query, [incomplete], policy).reason_code == "provenance_incomplete"

def test_below_threshold_refuses_before_generation():
    assert evaluate_evidence(query, [low_score], policy).status == "insufficient"

def test_conflicting_current_sources_return_conflict():
    assert evaluate_evidence(query, conflicting, policy).status == "conflict"
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/modules/retrieval/test_sufficiency.py`

Expected: module missing.

- [ ] **Step 3: Implement a deterministic baseline policy**

```python
class EvidenceStatus(StrEnum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    CONFLICT = "conflict"

@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    status: EvidenceStatus
    reason_code: str | None
    evidence: tuple[RetrievedEvidence, ...]
```

The baseline requires at least one complete citation snapshot and a calibrated score threshold. It may not claim semantic contradiction detection until a versioned evaluator exists; the conflict status is accepted only from deterministic structured comparisons in this slice.

- [ ] **Step 4: Prove refusal occurs before LiteLLM invocation**

Add a chat-stream test whose streamer raises if called; an insufficient decision must persist a refusal and emit no provider call.

- [ ] **Step 5: Verify and commit**

Run:

```bash
cd backend
uv run pytest -q tests/modules/retrieval/test_sufficiency.py tests/api/test_chat_stream.py -k 'insufficient or no_answer'
uv run ruff check src tests
uv run mypy src
```

Commit: `feat: refuse on insufficient retrieval evidence`

---

### Task 4: Complete authority citation persistence and marker verification

**Files:**
- Modify: `backend/src/openrag/modules/chat/events.py`
- Modify: `backend/src/openrag/modules/chat/prompting.py`
- Modify: `backend/src/openrag/modules/chat/service.py`
- Modify: `backend/src/openrag/modules/chat/schemas.py`
- Modify: `backend/tests/modules/chat/test_prompting.py`
- Modify: `backend/tests/api/test_chat_stream.py`
- Modify: `backend/tests/api/test_chat_history.py`

**Interfaces:**
- Consumes: citation-ready `RetrievedEvidence` and `EvidenceDecision`.
- Produces: SSE source/citation frames and `Citation` rows with complete authority snapshots.

- [ ] **Step 1: Write failing tests for complete citations and invalid drafts**

```python
async def test_grounded_answer_persists_exact_authority_citation_snapshot():
    assert citation.document_version_id == evidence.document_version_id
    assert citation.evidence_span_id == evidence.evidence_span_id
    assert citation.document_name == evidence.document_name
    assert citation.version_label == evidence.version_label
    assert citation.section_path == list(evidence.section_path)
    assert citation.page == evidence.page_number

async def test_answer_without_valid_markers_is_replaced_by_refusal():
    assert assistant.answer_status == "refused"
    assert assistant.refusal_reason == "citation_validation_failed"
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/api/test_chat_stream.py -k 'authority_citation or invalid_markers'`

Expected: authority-enabled persistence currently raises or uses incomplete references.

- [ ] **Step 3: Make source and citation frames version-aware**

Extend `SourceRef` and `CitationRef` with document version, evidence span, document name, version, section path, locator, content hash, and score fields. Keep response strings bounded and never include hidden source text in citation frames.

- [ ] **Step 4: Persist only revalidated, marker-referenced snapshots**

Requery all referenced evidence IDs in the assistant persistence transaction, require the same lifecycle/tenant/workspace/hash facts, and insert `Citation` rows only after the answer marker set exactly resolves to those snapshots. A failed check persists `answer_status='refused'` and `refusal_reason='citation_validation_failed'` with no citations.

- [ ] **Step 5: Verify and commit**

Run:

```bash
cd backend
uv run pytest --collect-only -q tests/api/test_chat_stream.py tests/api/test_chat_history.py
uv run pytest -q tests/modules/chat/test_prompting.py tests/modules/chat/test_events.py
uv run ruff check src tests
uv run mypy src
```

Commit: `feat: persist verified authority citations`

---

### Task 5: Regression, migration, and public checkpoint

**Files:**
- Modify only files required by discovered regressions.

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: verified retrieval/grounding slice on public `main`.

- [ ] **Step 1: Run complete static and architecture gates**

```bash
cd backend
uv run ruff check .
uv run mypy src
uv run lint-imports
uv run alembic heads
uv run alembic check
```

- [ ] **Step 2: Run all non-container unit suites and collect container suites**

```bash
cd backend
uv run pytest -q tests/modules/retrieval tests/modules/chat
uv run pytest --collect-only -q tests/api/test_chat_stream.py tests/isolation/test_chat_isolation.py
```

If Docker storage is healthy, run the real Postgres/Qdrant integration and isolation suites. If it is still corrupt, report the exact Docker error and do not claim those suites passed.

- [ ] **Step 3: Run frontend contract regression after OpenAPI changes**

```bash
cd frontend
corepack pnpm typecheck
corepack pnpm lint
corepack pnpm test
corepack pnpm build
```

- [ ] **Step 4: Verify the exact staged scope and push**

```bash
git diff --cached --check
git status --short
git push origin main
git rev-list --left-right --count origin/main...HEAD
```

Expected: `0 0`; the reference repositories remain untracked and unstaged.

---

## Plan Self-Review

- Spec coverage: covers the approved design's policy prefilter/post-validation, hybrid candidate contract, provenance completeness, deterministic sufficiency, refusal before generation, and immutable citation persistence. Cross-encoder reranking and Agno orchestration remain explicit follow-on plans because they are independently deployable subsystems.
- Placeholder scan: no TBD/TODO/future implementation placeholders are present; every deferred capability is named as a separate approved delivery-sequence item.
- Type consistency: `CandidateIdentity -> AuthorizedEvidence -> RetrievedEvidence -> EvidenceDecision -> Citation` is the single forward contract used by all tasks.
- Security: tenant/workspace/lifecycle/hash checks occur again after Qdrant and again before citation persistence; unknown ACL policy fails closed.
