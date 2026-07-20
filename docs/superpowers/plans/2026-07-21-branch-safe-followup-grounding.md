# Branch-Safe Follow-up Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ambiguous document follow-ups reuse the nearest branch-local cited evidence safely instead of producing false confidence-threshold refusals.

**Architecture:** Port RAGHub's nearest-ancestor citation backfill into OpenRAG behind a typed retrieval boundary. The chat layer owns branch selection and merge priority; the retrieval layer owns tenant filters, legacy chunk rehydration, and current-authority revalidation.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL, Qdrant, pytest, LiteLLM streaming.

## Global Constraints

- Use only LiteLLM for model execution; do not add direct provider SDK calls.
- Backfill only from the nearest previous assistant ancestor on the active branch.
- Revalidate every historic citation against the current organization, workspace, document lifecycle, ACL, and content identity before prompt use.
- Preserve strict grounded-answer release gates and citation persistence.
- Keep the backfill bounded to 32 historic identities and the current `top_k`.
- Do not modify or commit the `anything-llm/`, `openui/`, or `raghub/` reference trees.
- Execute this plan inline with a single agent, per the user's standing instruction.

---

### Task 1: Retrieval-owned citation rehydration

**Files:**
- Modify: `backend/src/openrag/modules/retrieval/service.py`
- Modify: `backend/tests/modules/retrieval/test_retrieve.py`
- Modify: `backend/tests/modules/retrieval/test_result_contract.py`

**Interfaces:**
- Produces: `CitationEvidenceIdentity`
- Produces: `backfill_citation_evidence(session, context, workspace_id, identities, top_k=8) -> RetrievalResult`

- [ ] **Step 1: Write failing legacy backfill tests**

Add tests that seed two tenants, persist exact legacy chunk references, and assert that only well-formed references owned by the caller's organization/workspace are returned in reference order with score `0.0`. Assert malformed, duplicate, cross-tenant, non-approved, and over-limit identities are rejected or dropped.

```python
identity = CitationEvidenceIdentity(
    document_id=UUID(document_id),
    document_version_id=UUID(document_id),
    evidence_span_id=None,
    chunk_ref=f"{document_id}:1:0",
    content_hash=None,
)
result = await backfill_citation_evidence(
    session, context, workspace.id, [identity], top_k=8,
)
assert result.no_answer is False
assert [(item.text, item.score) for item in result.chunks] == [
    ("invoice amount is 590000", 0.0),
]
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/modules/retrieval/test_retrieve.py tests/modules/retrieval/test_result_contract.py -q`

Expected: FAIL because `CitationEvidenceIdentity` and `backfill_citation_evidence` do not exist.

- [ ] **Step 3: Implement the bounded identity and legacy rehydration path**

Add a frozen identity dataclass with bounded `chunk_ref` and optional authority fields. Parse legacy references as exactly `UUID:positive_page:nonnegative_chunk_index`, deduplicate them, check legacy document eligibility in PostgreSQL, then resolve Qdrant payloads under `_tenant_filter(org_id=..., workspace_id=..., document_id=...)`. Return at most `top_k` chunks and mark them score `0.0`.

```python
@dataclass(frozen=True, slots=True)
class CitationEvidenceIdentity:
    document_id: UUID
    document_version_id: UUID
    evidence_span_id: UUID | None
    chunk_ref: str
    content_hash: str | None

    def __post_init__(self) -> None:
        if not 1 <= len(self.chunk_ref) <= 500:
            raise ValueError("citation_chunk_ref_invalid")


```

Implement `backfill_citation_evidence(session: AsyncSession, context: TenantContext, workspace_id: UUID, identities: Sequence[CitationEvidenceIdentity], top_k: int = 8) -> RetrievalResult` with the exact validation and lookup behavior above.

- [ ] **Step 4: Add authority rehydration tests and implementation**

Construct `CandidateIdentity` values only when `evidence_span_id` and a canonical SHA-256 `content_hash` are present. Reuse `revalidate_candidates` so stale, superseded, deleted, ACL-restricted, or hash-mismatched evidence drops. Convert authorized rows to `RetrievedEvidence` with similarity fields cleared and `fused_score=0.0` before diversity selection.

```python
authorized = await revalidate_candidates(
    session, context, workspace_id, candidates, now=datetime.now(UTC),
)
evidence = [
    replace(_retrieved_evidence(item), dense_score=None, sparse_score=None, fused_score=0.0)
    for item in authorized
]
```

- [ ] **Step 5: Run retrieval tests and commit**

Run: `cd backend && .venv/bin/pytest tests/modules/retrieval/test_retrieve.py tests/modules/retrieval/test_result_contract.py tests/modules/retrieval/test_authority_unit.py -q`

Expected: PASS.

Commit only these files with: `git commit -m "feat: rehydrate prior citation evidence safely"`

---

### Task 2: Branch-local follow-up rescue in chat streaming

**Files:**
- Modify: `backend/src/openrag/modules/chat/service.py`
- Create: `backend/tests/api/test_chat_backfill.py`

**Interfaces:**
- Consumes: `CitationEvidenceIdentity` and `backfill_citation_evidence(session, context, workspace_id, identities, top_k)`
- Produces: `CitationBackfiller` protocol
- Produces: `_merge_retrieval_with_backfill(primary, backfill, top_k) -> RetrievalResult`

- [ ] **Step 1: Write the failing end-to-end chat regression**

Use a sequence retriever whose first result is grounded and second result is `no_answer=True`. Send `tell me more about the docs`, then `extract structure info`. Inject a fake backfiller that records the cited identity and returns the prior invoice chunk. Assert the second response streams tokens, sources, citations, and `done.no_answer == false`.

```python
assert backfiller.calls[0][0].chunk_ref == f"{document.id}:1:0"
assert next(data for event, data in events if event == "done")["no_answer"] is False
assert NO_ANSWER_TEXT not in "".join(
    data["delta"] for event, data in events if event == "token"
)
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && .venv/bin/pytest tests/api/test_chat_backfill.py -q`

Expected: FAIL because chat streaming does not request citation backfill.

- [ ] **Step 3: Implement nearest-ancestor selection**

Walk `path_to_root(all_messages, user_message)` in reverse. Stop at the first assistant message and return its ordered citations, even if the list is empty. Convert snapshots to `CitationEvidenceIdentity`; never read evidence text from message content.

```python
for ancestor in reversed(path_to_root(messages, user_message)):
    if ancestor.role == ROLE_ASSISTANT:
        citations = (await session.execute(
            select(Citation)
            .where(Citation.message_id == ancestor.id)
            .order_by(Citation.marker)
        )).scalars()
        return [_citation_identity(item) for item in citations]
return []
```

- [ ] **Step 4: Implement bounded merge and refusal recomputation**

After existing agent gathering, invoke backfill only when fresh retrieval is insufficient or under-filled. If fresh retrieval is insufficient, order backfill first; otherwise order fresh evidence first. Deduplicate legacy chunks by `(document_id, page, chunk_index)` and authority evidence by `evidence_span_id`, cap to `top_k`, and set `no_answer` false only when a backfilled item survives the final merge.

- [ ] **Step 5: Add branch, refusal, and deduplication tests**

Cover an intervening uncited assistant response, sibling branches, duplicate citation identities, sufficient fresh retrieval, and the `top_k` bound.

- [ ] **Step 6: Run chat tests and commit**

Run: `cd backend && .venv/bin/pytest tests/api/test_chat_backfill.py tests/api/test_chat_stream.py tests/modules/chat -q`

Expected: PASS.

Commit only the chat implementation and tests with: `git commit -m "fix: preserve grounding across document follow-ups"`

---

### Task 3: Runtime wiring and conversation-meta routing

**Files:**
- Modify: `backend/src/openrag/api/app.py`
- Modify: `backend/src/openrag/api/routes/chats.py`
- Modify: `backend/src/openrag/modules/runs/runner.py`
- Modify: `backend/src/openrag/modules/orchestration/routing.py`
- Modify: `backend/tests/modules/orchestration/test_routing.py`
- Modify: `backend/tests/api/test_chat_backfill.py`

**Interfaces:**
- Consumes: `CitationBackfiller`
- Preserves: both direct API streaming and durable workers use identical backfill behavior

- [ ] **Step 1: Write failing wiring and routing tests**

Assert `what is my prev question?` selects `QueryRoute.CONVERSATION`. Assert a fake backfiller injected through `create_app` is called by the direct streaming route.

```python
decision = decide_route(
    "what is my prev question?",
    history=[("user", "extract structure info")],
)
assert decision.route is QueryRoute.CONVERSATION
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && .venv/bin/pytest tests/modules/orchestration/test_routing.py tests/api/test_chat_backfill.py -q`

Expected: FAIL because `prev` is not recognized and no backfiller is wired.

- [ ] **Step 3: Wire one production backfiller everywhere**

Add optional `citation_backfiller` injection to `create_app`, default it to `backfill_citation_evidence`, expose it on app state, and pass it from chat routes. Pass the same production function from the durable run worker. Pass `retrieval_top_k` into the initial retriever call so under-fill decisions use the actual requested bound.

- [ ] **Step 4: Expand only the explicit thread-meta grammar**

Update the full-match pattern to accept `what was`, `what is`, or `what's`, and `previous`, `prev`, or `last`. Do not widen greeting bypasses or allow a substring match.

```python
r"what (?:was|is|'s) my (?:previous|prev|last) question"
```

- [ ] **Step 5: Run focused tests and commit**

Run: `cd backend && .venv/bin/pytest tests/modules/orchestration/test_routing.py tests/api/test_chat_backfill.py tests/api/test_chat_stream.py tests/modules/runs -q`

Expected: PASS.

Commit only runtime/routing files with: `git commit -m "fix: wire contextual evidence into every chat runtime"`

---

### Task 4: Full verification, live smoke, and publication

**Files:**
- Modify only if verification exposes a directly related defect.

- [ ] **Step 1: Run backend quality gates**

Run:

```bash
cd backend
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/pytest -q
```

Expected: all commands exit `0` with no test failures.

- [ ] **Step 2: Restart affected API and run workers**

Restart only the host API and `runs` worker using the existing local commands. Verify `GET /healthz` and `GET /readyz` return `200`.

- [ ] **Step 3: Run a live invoice follow-up**

Create a new chat in the existing invoice workspace. Ask `tell me more about the docs`, wait for a grounded answer, then ask `extract structure info`. Verify SSE contains `route_selected`, `retrieval_started`, `sources`, streamed `token` events, `citations`, and `done` with `no_answer=false`.

- [ ] **Step 4: Verify persistence and observability**

Query `messages`, `citations`, `agent_runs`, `rag_run_facts`, and `run_context_ledgers`. The follow-up must have a non-refused assistant message, at least one citation, `outcome='grounded'`, and bounded retrieval/history counts.

- [ ] **Step 5: Push intentionally scoped commits**

Run `git status --short`, confirm unrelated pre-existing changes remain unstaged, then `git push origin main`. Report the commit hashes and live verification evidence.
