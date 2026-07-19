# Document Authority and Grounded Citation Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce authoritative logical documents and immutable versions, migrate legacy content without a silent search outage, retrieve only current approved evidence, and release substantive answers only as progressively verified claim records with exact document/version/section/page citations.

**Architecture:** PostgreSQL owns document/version authority, provenance, lifecycle revisions, projection watermarks, grounding policies, and citation snapshots. Redis Streams carries transactional-outbox events to idempotent Qdrant projection consumers; Qdrant is a derived candidate index filtered by a projected current marker and every candidate is batch-revalidated against PostgreSQL. Chat runners receive immutable IDs plus a session factory—never a request `AsyncSession`—and emit only complete claim records that pass structural, numeric, and calibrated semantic-entailment checks.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL/Alembic, Celery, Redis Streams, MinIO-compatible object storage, Qdrant, in-process LiteLLM for bounded entailment verification, Pydantic v2, structlog, React 18, TypeScript, TanStack Query, Tailwind CSS, pytest/Testcontainers, Vitest, Playwright.

## Global Constraints

- Product copy is **OpenRAG**; never introduce RAGHub.
- `anything-llm/` and `openui/` remain read-only, untracked benchmarks.
- Use TDD. Every task records RED, GREEN, focused regression, and independent review evidence before the next task starts.
- Substantive company answers are closed-book and use only current, approved, effective, non-expired, non-superseded, non-obsolete, authorized versions.
- Every released material claim cites document name, version, bounded section path, and one exact positive page/slide/sheet ordinal. A multi-page chunk is never cited as though all text came from one page.
- PostgreSQL is authoritative. Qdrant payload state, hashes, names, and scores never authorize or establish provenance.
- No SQL transaction or request-scoped session remains open across object storage, Qdrant, embedding, LiteLLM, Redis, OCR, heartbeat, or SSE waits.
- Documents, OCR, vector text, chat history, metadata, model output, and tool output are untrusted data rather than instructions.
- Logs/events contain identifiers, states, revisions, counts, bounded scores, reason codes, and durations only—no query, document text, snippet, prompt, credential, token, or model reasoning.
- Exact capabilities remain `document.read`, `document.upload`, and `document.approve`; frontend checks are UX hints only.
- Version `sequence` is database authority and never accepted from clients. Display labels are NFKC-normalized, whitespace-collapsed, case-folded into an immutable `version_key`, and unique per logical document.
- Section paths are JSON arrays with 1–8 string segments, each 1–255 characters, and at most 2,048 encoded bytes. Claim marker arrays contain at most 64 positive integers and at most 1,024 encoded bytes.
- The 95–98% accuracy figure is a versioned golden-dataset target, not a universal claim.
- Never read, print, return, log, or commit the stored OpenAI key. Provider tests use injected fakes unless an explicit, redacted, cost-bounded live gate is separately authorized.

## Rollout and compatibility contract

This is an expand/rebuild/cutover release, not an in-place flag day:

1. Treat Task 2 as a coordinated schema/runtime compatibility boundary. Drain
   pre-Task-2 chat writers (or remove them from the load balancer), apply the
   expand migration, deploy the scope-aware atomic legacy writer, and only then
   resume chat writes. Reads and legacy retrieval may continue throughout, but
   no writer that omits message scope or citation snapshots may run against the
   contracted schema. Do not describe the migration by itself as compatible
   with the old writer.
2. Create a new physical `openrag_authority_v1_<generation>` Qdrant collection and `openrag_authority_active_v1` alias. The existing `openrag_chunks` collection is legacy and read-only after this deployment.
3. Provision the physical authority collection, dense/sparse vectors, and every exact payload index before any authority-upsert stage can be claimed.
4. Deploy compatibility workers and a restart-safe paginated scanner that rebuild PostgreSQL page-local provenance and authority-collection points for every legacy indexed version.
5. Project the current-approved marker, persist its applied lifecycle revision, and activate an unexpired passed grounding policy for the workspace.
6. Request a readiness evaluation through the transactional outbox. A replay-safe worker verifies collection schema/indexes, counts, exact text hashes, page/section completeness, projection watermarks, and the active policy snapshot outside SQL, then stores a signed/digested result.
7. Enable `Workspace.document_authority_enabled` only by consuming a passed, signed, unexpired readiness generation whose lifecycle and grounding-policy snapshots are still active. The activation transaction refuses partial/stale readiness.
8. Branch exclusively on that DB flag: disabled workspaces query only read-only `openrag_chunks`; enabled workspaces query only `openrag_authority_active_v1`. Never merge both collections, so duplicate results and review-version leakage cannot cross the cutover.

The old path is temporary deployment compatibility, not acceptable final state. Final release verification requires authority enabled for every production workspace. Downgrade is fail-closed after governed data/events exist; application rollback keeps the expanded schema until a reviewed forward migration exists.

## Authorization and oracle contract

- Workspace collection routes with a same-organization workspace ID return `403` when membership/capability is missing; a foreign-organization workspace ID returns the existing non-disclosing workspace denial.
- Object-ID reads/mutations first establish an authorized workspace object scope. Foreign organization, foreign workspace, or non-member object IDs return `404`.
- Within an accessible workspace, a caller lacking the exact mutation capability receives `403` without state change.
- `document.read`: logical document/version/provenance/history reads and cited evidence.
- `document.upload`: create logical documents/versions, edit logical metadata,
  retry failed processing, and physically delete only never-approved
  draft/failed content or rejected content with no retained governance decision.
  Once a decision record exists, external source/provenance may be purged under
  the deletion workflow but the rejected version metadata remains as a
  governance tombstone.
- `document.approve`: approve, reject, obsolete, and supersede through approval.
- `rag.evaluate`: run verifier calibration and activate a passing grounding policy/cutover readiness report.
- `model.configure`: bind an approved verifier model to a grounding policy. Binding and activation together require both `model.configure` and `rag.evaluate`.

## Lock order and race contract

All mutations use this order: workspace authorization read → logical `Document FOR UPDATE` → affected `DocumentVersion` rows ordered by UUID `FOR UPDATE` → projection/policy row → message → citations. Never lock versions before the logical document. Race suites cover two successor approvals, approve-vs-obsolete, approve-vs-reject, retry-vs-delete, lifecycle event reordering, authorization revocation during generation, supersession after retrieval, and projection lag at final release.

## File boundaries

- `documents/lifecycle.py`: enums, normalization, bounded section validation, pure transitions.
- `documents/models.py`: document/version/block/chunk/evidence-span/projection authority.
- `documents/service.py`: capability/object-checked lifecycle commands.
- `documents/policy.py`: current-version eligibility and final DB revalidation.
- `documents/rebuild.py`: restart-safe legacy discovery and provenance rebuild.
- `documents/readiness.py`: asynchronous signed readiness evaluation and cutover.
- `events/dispatcher.py`: outbox → Redis Streams dispatcher and idempotent consumer helpers.
- `grounding/evidence.py`: sufficiency/conflict/projection-lag decisions.
- `grounding/entailment.py`: calibrated fail-closed semantic verifier.
- `grounding/claims.py`: typed claim-record parser and structural/numeric validation.
- `grounding/citations.py`: immutable snapshot construction and final-release transaction.
- `chat/runner.py`: session-free progressive verified-claim orchestration.

---

### Task 1: Define bounded authority types and tenant-safe ORM structure

**Files:**
- Create: `backend/src/openrag/modules/documents/lifecycle.py`
- Modify: `backend/src/openrag/modules/documents/models.py`
- Modify: `backend/src/openrag/modules/chat/models.py`
- Create: `backend/src/openrag/modules/grounding/models.py`
- Modify: `backend/src/openrag/modules/events/models.py`
- Modify: `backend/src/openrag/modules/models/models.py`
- Test: `backend/tests/modules/documents/test_lifecycle.py`
- Test: `backend/tests/modules/documents/test_models.py`
- Test: `backend/tests/modules/chat/test_models.py`
- Test: `backend/tests/modules/grounding/test_models.py`

**Interfaces:**
- Produces `DocumentVersionState`, `ProvenanceState`, `AnswerStatus`, `RefusalReason`, `normalize_version_label`, `validate_section_path`, and `ensure_transition`.
- Produces `DocumentVersion`, `DocumentBlock`, `DocumentChunk`, `DocumentChunkBlock`, `DocumentEvidenceSpan`, `DocumentVersionProjection`, `DocumentAuthorityReadiness`, `IngestStageAttempt`, `LegacyRebuildScanCheckpoint`, `GroundingPolicy`, and `GroundingCalibrationRun`.

- [ ] **Step 1: Write failing pure normalization and transition tests**

```python
def test_version_key_is_nfkc_casefolded_and_sequence_is_not_client_data() -> None:
    assert normalize_version_label("  REV  ７ ") == ("REV 7", "rev 7")


def test_section_path_is_bounded() -> None:
    assert validate_section_path(["Emergency", "Evacuation"]) == (
        "Emergency", "Evacuation"
    )
    with pytest.raises(ValueError, match="at most 8"):
        validate_section_path([str(index) for index in range(9)])


def test_processing_cannot_skip_review() -> None:
    with pytest.raises(InvalidDocumentTransition):
        ensure_transition("processing", "approved")
```

Run: `cd backend && uv run pytest tests/modules/documents/test_lifecycle.py -q`

Expected: FAIL because the authority types do not exist.

- [ ] **Step 2: Add exact enums and normalization**

```python
class DocumentVersionState(StrEnum):
    DRAFT = "draft"
    PROCESSING = "processing"
    REVIEW = "review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    OBSOLETE = "obsolete"
    FAILED = "failed"


class ProvenanceState(StrEnum):
    NONE = "none"
    LEGACY_PENDING = "legacy_pending"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"


class RefusalReason(StrEnum):
    NO_ELIGIBLE_DOCUMENTS = "no_eligible_documents"
    NO_CANDIDATES = "no_candidates"
    BELOW_THRESHOLD = "below_threshold"
    INCOMPLETE_PROVENANCE = "incomplete_provenance"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    INDEX_PROJECTION_LAG = "index_projection_lag"
    ENTAILMENT_FAILED = "entailment_failed"
    CITATION_VALIDATION_FAILED = "citation_validation_failed"
```

Allowed lifecycle edges are draft→processing, processing→review/failed, failed→processing, review→approved/rejected, and approved→superseded/obsolete. Terminal states have no other edges.

- [ ] **Step 3: Write failing composite-integrity tests**

```python
async def test_chunk_block_membership_cannot_cross_version(session):
    first, second = await seed_two_versions(session)
    chunk = await seed_chunk(session, first)
    block = await seed_block(session, second)
    session.add(DocumentChunkBlock(
        org_id=first.org_id, document_version_id=first.id,
        chunk_id=chunk.id, block_id=block.id, position=0,
    ))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_parent_chunk_and_supersession_stay_in_same_version_or_document(session):
    first, second = await seed_two_versions(session)
    foreign_parent = await seed_chunk(session, second)
    session.add(make_chunk(first, parent_chunk_id=foreign_parent.id))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    first_document, second_document = await seed_two_documents(session)
    approved = await seed_version(session, first_document, state="approved")
    foreign_successor = await seed_version(session, second_document, state="review")
    approved.state = "superseded"
    approved.superseded_by_id = foreign_successor.id
    with pytest.raises(IntegrityError):
        await session.commit()
```

Run: `cd backend && uv run pytest tests/modules/documents/test_models.py tests/modules/chat/test_models.py -q`

Expected: FAIL before composite parents/foreign keys exist.

- [ ] **Step 4: Define exact parent uniques and same-tenant/version links**

Add these named parent keys and composite foreign keys:

```text
workspaces UNIQUE(org_id,id)
documents UNIQUE(org_id,workspace_id,id), FK(org_id,workspace_id)->workspaces
document_versions UNIQUE(org_id,id), UNIQUE(org_id,document_id,id), UNIQUE(document_id,id),
  UNIQUE(org_id,workspace_id,document_id,id),
  FK(org_id,workspace_id,document_id)->documents(org_id,workspace_id,id),
  FK(document_id,superseded_by_id)->document_versions(document_id,id)
document_blocks UNIQUE(org_id,document_version_id,id), FK(org_id,document_version_id)->versions
document_chunks UNIQUE(org_id,document_version_id,id), FK(org_id,document_version_id)->versions,
  FK(org_id,document_version_id,parent_chunk_id)->chunks(org_id,document_version_id,id)
document_evidence_spans UNIQUE(org_id,document_version_id,id),
  FK(org_id,document_version_id,chunk_id)->chunks(org_id,document_version_id,id)
document_chunk_blocks FK(org_id,document_version_id,chunk_id)->chunks and
  FK(org_id,document_version_id,block_id)->blocks
document_version_projections FK(org_id,document_version_id)->versions
ingest_jobs FK(org_id,document_id,document_version_id)->versions(org_id,document_id,id)
chats UNIQUE(org_id,workspace_id,id), FK(org_id,workspace_id)->workspaces
messages UNIQUE(org_id,workspace_id,id), UNIQUE(chat_id,id),
  FK(org_id,workspace_id,chat_id)->chats(org_id,workspace_id,id),
  FK(chat_id,parent_message_id)->messages(chat_id,id)
citations FK(org_id,workspace_id,message_id)->messages(org_id,workspace_id,id),
  FK(org_id,workspace_id,document_id)->documents(org_id,workspace_id,id),
  FK(org_id,document_id,document_version_id)->versions(org_id,document_id,id),
  FK(org_id,document_version_id,evidence_span_id)->spans(org_id,document_version_id,id)
documents FK(org_id,owner_id)->users(org_id,id), FK(org_id,created_by)->users(org_id,id)
document_versions FK(org_id,created_by)->users(org_id,id),
  FK(org_id,approved_by)->users(org_id,id), FK(org_id,rejected_by)->users(org_id,id),
  FK(org_id,obsolete_by)->users(org_id,id)
document_version_decision_records
  FK(org_id,workspace_id,document_id,document_version_id)->
    document_versions(org_id,workspace_id,document_id,id),
  FK(org_id,actor_id)->users(org_id,id),
  UNIQUE(org_id,document_version_id,lifecycle_revision)
```

`DocumentVersion` has database-authoritative `sequence`, immutable display `version_label`, immutable normalized `version_key`, `lifecycle_revision >= 1`, and `provenance_state`. Unique keys are `(document_id,sequence)`, `(document_id,version_key)`, and `(document_id,content_hash)`.

`DocumentEvidenceSpan` is page-local: one exact positive `page_number`, `locator_kind`, `locator_label`, bounded `section_path`, normalized-text SHA-256, parent chunk, ordinal, token count, and artifact byte range. Qdrant points index spans, not ambiguous multi-page chunk text.

`DocumentAuthorityReadiness` is an expiring workspace generation with controlled state transitions and immutable terminal results: generation UUID, org/workspace, idempotency/request digest, physical authority collection, alias, schema version, current-version count, ready/projected/point counts, payload-index digest, provenance digest, lifecycle-revision digest, active grounding-policy/calibration/model/preset/binding/credential fingerprints, canonical readiness digest, HMAC signature, status `building|passed|stale|failed|activated`, lease owner/expiry/attempt count, checked/expiry/activated timestamps, and activating actor. Unique `(org_id,workspace_id,generation_id)` and tenant actor FKs apply. The signature key is derived from the server KEK with a distinct HKDF context and is never stored in the row.

`LegacyRebuildScanCheckpoint` stores one tenant/workspace scanner cursor, pass number, cumulative scanned/emitted/skipped counters, and timestamps. Its composite tenant/workspace foreign keys and unique workspace key let CLI and beat scanners resume bounded keyset passes without cross-tenant progress mutation.

`GroundingPolicy` stores immutable policy version, verifier model ID/binding revision/credential fingerprint, entailment threshold `[0,1]`, calibration dataset version/hash, sample count, measured false-support/false-refusal rates, status `draft|passed|active|retired`, `effective_at`, `expires_at`, and timestamps. A workspace references at most one active unexpired policy.

`GroundingCalibrationRun` is the durable asynchronous request/result record keyed by tenant/workspace/policy/run generation. It stores idempotency digest, requested binding/preset/credential fingerprints, state `queued|running|passed|failed`, lease/checkpoint/attempt fields, safe aggregate result fields, and timestamps; it never stores prompts, evidence text, credentials, or provider responses.

`DocumentVersionDecisionRecord` is immutable tenant-scoped governance history:
exact org/workspace/document/version identity, positive lifecycle revision,
decision `approved|rejected|obsolete|superseded`, same-tenant actor, optional
reason whose trimmed length is 1–500, and creation timestamp. One decision may
exist per version lifecycle revision. Database UPDATE and DELETE are forbidden,
the version foreign key is restrictive rather than cascading, and downgrade
fails closed while any decision record exists.

`Model` gains bounded server-validated capability booleans `supports_chat_completion`, `supports_structured_json`, and `supports_verifier`, plus immutable `provider_preset_version`. These are not accepted as arbitrary client truth: model validation sets them from the curated provider preset and a bounded live capability check.

Add RED database tests that deliberately assign owner/creator/approver/rejector/obsolete actors from another organization and assert each composite actor FK fails. Nullable actors remain nullable, but every non-null actor is tenant-bound.

Run: `cd backend && uv run pytest tests/modules/documents/test_lifecycle.py tests/modules/documents/test_models.py tests/modules/chat/test_models.py tests/modules/grounding/test_models.py -q`

Expected: PASS at model metadata/unit level; database DDL is exercised after Task 2.

- [ ] **Step 5: Commit**

```bash
git add backend/src/openrag/modules/documents/lifecycle.py \
  backend/src/openrag/modules/documents/models.py backend/src/openrag/modules/chat/models.py \
  backend/src/openrag/modules/grounding/models.py backend/src/openrag/modules/events/models.py \
  backend/src/openrag/modules/models/models.py \
  backend/tests/modules/documents backend/tests/modules/chat/test_models.py \
  backend/tests/modules/grounding/test_models.py
git commit -m "feat: model tenant safe document authority"
```

### Task 2: Add the expand/backfill migration, compatibility writer, and database invariants

**Files:**
- Create: `backend/migrations/versions/9d2c7a4e1f60_document_authority_and_citations.py`
- Modify: `backend/src/openrag/modules/chat/schemas.py`
- Modify: `backend/src/openrag/modules/chat/service.py`
- Modify: `backend/tests/test_migrations.py`
- Modify: `backend/tests/modules/chat/test_tree_service.py`
- Modify: `backend/tests/api/test_chat_history.py`
- Create: `backend/tests/integration/test_document_authority_migration_boundary.py`

**Interfaces:**
- Migration revision `9d2c7a4e1f60`; `down_revision = "6c4a2f8b9d10"`.
- Expands legacy tables without requiring MinIO, Qdrant, Redis, or a model provider.
- Produces a post-upgrade compatibility writer that always supplies message
  tenant scope and atomically persists an assistant plus any exact
  `legacy_unverified` display snapshots while document authority is disabled.
- Task 2 is a coordinated migration/runtime boundary, not a migration-only
  deploy. Pre-Task-2 chat writers must be drained before the contracted
  non-null scope and citation checks become reachable.

- [ ] **Step 1: Write migration-head, backfill, trigger, and downgrade-preflight RED tests**

```python
def test_authority_revision_is_the_single_head(config):
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["9d2c7a4e1f60"]
    assert script.get_revision("9d2c7a4e1f60").down_revision == "6c4a2f8b9d10"


def test_upgrade_marks_legacy_index_for_rebuild_without_enabling_cutover(authority_db):
    config, engine, ids = authority_db
    command.upgrade(config, "9d2c7a4e1f60")
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT id, document_id, state, provenance_state FROM document_versions"
        )).mappings().one()
        assert row == {
            "id": ids.document_id, "document_id": ids.document_id,
            "state": "approved", "provenance_state": "legacy_pending",
        }
        assert connection.execute(text(
            "SELECT document_authority_enabled FROM workspaces"
        )).scalar_one() is False
```

Also test: orphan citation abort; JSONB section-path checks; cross-version parent/member/citation failure; `document_versions UNIQUE(org_id,id)`; every cross-organization owner/creator/decision actor rejected; readiness generation tenant/signature fields; version identity update trigger; two approved rows rejected; and downgrade refusal when any workspace is enabled, document has multiple versions, verified citation/grounded message exists, provenance is non-legacy, readiness generation exists, or related outbox/inbox event exists.

Use four pre-upgrade document fixtures (`indexed`, `failed`, `queued`, and
`processing`) and assert the exact sequence-1 mapping after upgrade. For every
fixture, assert `version_label='Legacy 1'`, `version_key='legacy 1'`, and that
the legacy filename, MIME type, byte size, canonical content hash, and storage
key were copied into the version-owned source fields. Assert an available
positive page count is copied exactly and an unavailable page count remains
null; do not fabricate a page count. Assert all five unknown legacy processing
profile versions equal the bounded compatibility sentinels
`legacy/parser-v1`, `legacy/ocr-unknown-v1`, `legacy/chunking-v1`,
`legacy/embedding-v1`, and `legacy/index-v1`. Then null every compatibility source mirror on
the indexed logical `Document` and prove a Task-6-style rebuild-source lookup
still returns the object identity exclusively from `DocumentVersion`.

Exercise the complete deferred answer/citation trigger matrix in this migration
task. An authority citation may attach only to an assistant parent with
`answer_status IN ('grounded','cited_conflict')`, and such a parent succeeds only
when at least one valid authority citation exists by transaction end. A
`legacy_unverified` citation is a mutually exclusive transition branch and is
accepted only when all of these facts hold simultaneously:

```text
workspace.document_authority_enabled = false
parent role = assistant
parent answer_status IS NULL
parent refusal_reason IS NULL
org_id/workspace_id/message_id/document_id/document_version_id are populated and
  resolve to that same tenant, parent, logical document, and version
referenced version sequence = 1
referenced version_label = "Legacy 1"
referenced version_key = "legacy 1"
verification_state = "legacy_unverified"
section_path = ["Legacy import"]
section_label = "Legacy import"
content_hash = "legacy-unverified"
claim_ids = []
claim_id IS NULL
evidence_span_id IS NULL
document_name, version_label, page, locator_kind, and locator_label form a
  complete bounded display snapshot; the snapshot version is "Legacy 1" and
  the positive page/locator reproduce the old citation
grounding policy/version, verifier model, prompt contract, provider preset,
  binding revision, credential fingerprint, and authority component scores are
  all null
```

Parameterize PostgreSQL tests that change each fact independently. Reject a
legacy citation on a user parent, a refused parent, a grounded/cited-conflict
authority parent, any other version, a partial display snapshot, a mismatched
tenant/document/version, or any insert after authority activation. Existing
legacy rows survive activation but remain immutable and display-only. They
never satisfy grounded/cited-conflict citation cardinality, never become
evidence, and are excluded from every retrieval, claim-verification, and memory
seed path. Prove that updating a historical assistant from null status to
grounded/cited-conflict while it has only legacy citations fails.

Add a real two-transaction activation race test. The legacy citation
transaction must take a locking read on the same workspace row before it
evaluates `document_authority_enabled`; the activation transaction must use a
compatible workspace lock. Prove the only two serial outcomes are: the complete
assistant and citation commit before activation, or activation wins and the
legacy assistant plus citation transaction rolls back completely. A stale
unlocked flag read that permits a legacy citation to commit after activation is
forbidden.

Also test `Message UPDATE` into or out of grounded/refused state revalidates the
final state; authority `Citation INSERT` rejects user, null-status legacy, or
refused parents and tenant/version/span mismatch; `Citation UPDATE` rejects
immutable snapshot mutation or moving a citation; direct `Citation DELETE` of
the last authority citation from a surviving grounded/conflict message fails at
constraint time; deleting the parent message or chat may cascade all citations
because no grounded parent survives at constraint time. Add transaction-level
tests for every branch here rather than deferring database-trigger coverage to
a later service task.

Run: `cd backend && uv run pytest tests/test_migrations.py -k authority -q && uv run pytest tests/integration/test_document_authority_migration_boundary.py tests/modules/chat/test_tree_service.py tests/api/test_chat_history.py -q`

Expected: FAIL because the migration does not exist.

- [ ] **Step 2: Implement locked expand/backfill**

Lock `documents`, `ingest_jobs`, `chats`, `messages`, `citations`, `outbox_events`, and `inbox_events` in `SHARE ROW EXCLUSIVE` mode. Reject orphan citations before mutation. Add nullable columns/tables, backfill, validate, then set required non-null columns.

Legacy mapping is explicit:

```text
legacy document id == first document_version id
indexed -> state approved + provenance legacy_pending
failed -> state failed + provenance none
queued/processing -> state processing + provenance none
version_label "Legacy 1", version_key "legacy 1", sequence 1
copy legacy filename, MIME, size, canonical content hash, storage key, and
  available positive page count into version-owned source fields
legacy parser/OCR/chunking/embedding/index profile versions ->
  "legacy/parser-v1", "legacy/ocr-unknown-v1", "legacy/chunking-v1",
  "legacy/embedding-v1", "legacy/index-v1"
legacy citation -> verification_state legacy_unverified, section ["Legacy import"],
  section label "Legacy import", exact old positive page/locator, null
  evidence_span_id, content_hash "legacy-unverified", empty claim_ids, complete
  document/version/section/page display snapshot
legacy assistant message -> answer_status null (historical), not falsely grounded
workspace.document_authority_enabled -> false
```

The version provenance constraint has exactly two mutually exclusive shapes.
New authority versions require complete source identity and all five explicit
processing profiles (`none/v1` is the native non-OCR sentinel). Their page count
may be null only while the version is `draft`, `processing`, or `failed` and
provenance is not ready; `review`, `approved`, `rejected`, `superseded`, and
`obsolete` require a positive page count. Parsing may set page count exactly
once from null to a positive value while processing/building; after that it is
immutable. The bounded legacy shape still requires source filename, MIME, size,
canonical content hash, storage key, and all five exact `legacy/...` profile
sentinels above, but permits a null page count only for sequence 1 with
`Legacy 1` / `legacy 1` and exactly one of:

```text
(state=approved,   provenance_state=legacy_pending)
(state=failed,     provenance_state=none)
(state=processing, provenance_state=none)
```

Never create a legacy version without rebuildable source identity. Do not use
`Legacy import` as version identity; that string belongs only to the legacy
citation display section. Use only the declared bounded legacy profile
sentinels and do not invent page counts during backfill.

Retain old document source columns and `ingest_jobs.document_id` as nullable compatibility mirrors. Drop the obsolete workspace-wide content-hash unique constraint; version-level uniqueness replaces it.

Add `outbox_events.lease_owner` and `lease_expires_at` as nullable indexed dispatcher-claim fields, and add the composite chat/message/citation parent keys enumerated in Task 1. Backfill message org/workspace from its chat before setting non-null. Backfill ingest-job org/document/version from the legacy document before adding its composite version foreign key.

Add the three model capability booleans defaulting false and nullable `provider_preset_version`; no legacy model is silently verifier-capable. Add `ingest_stage_attempts`, `legacy_rebuild_scan_checkpoints`, grounding-policy binding, `grounding_calibration_runs`, version projection, and readiness-generation tables/constraints from Task 1—including request digests, leases, attempts, policy/model snapshots, and terminal-state immutability—so later worker/API tasks require no unplanned DDL. Add an append-only tenant-scoped document-version decision record so bounded approval/rejection/obsoletion reasons are preserved as governance data rather than discarded or copied into operational audit/event payloads.

- [ ] **Step 3: Add database immutability and state triggers**

Create named PostgreSQL functions/triggers that:

- reject updates to version org/document/sequence/label/key/source filename/MIME/size/hash/storage key/revision/effective/expiry fields; permit only the controlled null-to-positive page-count write described above and freeze it thereafter;
- reject UPDATE or DELETE of document-version decision records;
- reject updates to citation snapshots;
- validate JSONB section/claim arrays by type, element count/length, and `pg_column_size`;
- on `Message INSERT OR UPDATE`, require user messages to have null answer/refusal state, refused messages to have a refusal reason and zero citations, and grounded/conflict assistant messages to have at least one authority citation at transaction end; explicitly exclude `legacy_unverified` rows from this count;
- on authority `Citation INSERT OR UPDATE`, require a real span, canonical SHA-256, nonempty claims, and complete name/version/section/page snapshot; reject attachment to user/null-status/refused messages, reject any org/workspace/document/version/span mismatch, and reject mutation or re-parenting of an existing immutable snapshot;
- on legacy `Citation INSERT`, allow exactly the disabled-workspace, null-status assistant, exact `Legacy 1`, `Legacy import`, sentinel-hash, empty-claims, null-span, complete-display shape above; reject this branch after activation and reject every hybrid of legacy and authority fields;
- before evaluating that legacy branch, take a locking read on the referenced
  workspace row in the global workspace-before-message/citation lock order;
  direct SQL writers must serialize with authority activation rather than rely
  on a stale `READ COMMITTED` flag read;
- on `Citation DELETE`, defer the parent-state check to transaction end: deleting the last citation while the grounded/conflict message survives fails, while a database cascade caused by deleting the parent message/chat passes because the checked parent no longer exists;
- verify current eligibility at citation insert and recheck final message/citation cardinality after every affected insert/update/delete path.

Source/provenance rebuild uses delete/reinsert only while a version is processing/building; triggers reject provenance mutation after `provenance_state='ready'`.

- [ ] **Step 4: Make the post-upgrade legacy chat writer atomic and scope-aware**

`add_message()` always copies `chat.org_id` and `chat.workspace_id` into the new
message. Refactor assistant creation around an internal uncommitted message
builder: public standalone message creation may commit normally, but
`_persist_assistant()` must lock the workspace first, create/flush the assistant,
resolve each citation's same-workspace exact `Legacy 1` version and authoritative
display fields, insert the complete `legacy_unverified` snapshots, and commit
the assistant plus citations once. A failed citation or an activation racing the
write rolls back both the assistant and all citations; there is no intermediate
assistant-only commit.

While the locked workspace flag is false, history serializes legacy citations
as unverified display sources with document name, `Legacy 1`, `Legacy import`,
and the exact positive page/locator. It never exposes the sentinel hash or
internal object key. When the flag is true, this compatibility writer rejects
legacy citation persistence rather than silently downgrading an authority
answer. Task 12 later replaces authority-answer persistence; Task 2 does not
pretend that a legacy citation is grounded evidence.

Add a migration-boundary integration test that upgrades a legacy database,
calls the real message/assistant persistence path while authority is disabled,
and proves message scope, exact snapshot values, unverified history display,
and one-transaction rollback under forced citation failure. Activate the
workspace and prove the same legacy insert fails with no partial assistant.
Also prove a parent with only a legacy citation cannot be changed to grounded or
cited-conflict and no retrieval/memory/authority-evidence selector consumes the
legacy row.

- [ ] **Step 5: Implement fail-closed downgrade**

Before any downgrade mutation, abort if authority is enabled, multiple versions exist, any new verified citation or grounded/conflict message exists, any non-legacy provenance/span exists, or any `document.version.%` outbox/inbox record exists. This event preflight prevents dropping durable work that may already be in Redis/DLQ. When compatible, copy the sole version back into legacy columns and remove new objects in reverse dependency order.

Run: `cd backend && uv run pytest tests/test_migrations.py -k authority -q && uv run pytest tests/integration/test_document_authority_migration_boundary.py tests/modules/chat/test_tree_service.py tests/api/test_chat_history.py -q && uv run alembic current --check-heads && uv run alembic check`

Expected: PASS; Alembic reports `9d2c7a4e1f60 (head)` and no model drift.

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/versions/9d2c7a4e1f60_document_authority_and_citations.py \
  backend/src/openrag/modules/chat/schemas.py backend/src/openrag/modules/chat/service.py \
  backend/tests/test_migrations.py backend/tests/modules/chat/test_tree_service.py \
  backend/tests/api/test_chat_history.py \
  backend/tests/integration/test_document_authority_migration_boundary.py
git commit -m "feat: migrate authoritative document history"
```

### Task 3: Implement locked lifecycle services and exact oracle behavior

**Files:**
- Create: `backend/src/openrag/modules/documents/events.py`
- Modify: `backend/src/openrag/modules/documents/service.py`
- Modify: `backend/src/openrag/modules/documents/ingest.py`
- Modify: `backend/src/openrag/api/routes/documents.py`
- Modify: `backend/src/openrag/worker/tasks.py`
- Test: `backend/tests/modules/documents/test_service.py`
- Create: `backend/tests/isolation/test_document_version_isolation.py`
- Modify: `backend/tests/modules/documents/test_ingest.py`
- Modify: `backend/tests/api/test_documents_routes.py`

**Interfaces:**
- Produces `PreparedUpload`, `authorize_upload_scope`, `create_document_record`, `create_version_record`, `approve_version`, `reject_version`, `obsolete_version`, `retry_version`, `request_document_deletion`, and checked read/list functions.
- Produces content-free `DocumentVersionEventV1` with lifecycle revision.

- [ ] **Step 1: Write RED service, oracle, capability, and race tests**

Test the authorization matrix above and explicit races: two successors approved concurrently; approve-vs-obsolete; approve-vs-reject for the same review candidate; and retry-vs-delete. Each contender captures the same pre-lock lifecycle/incumbent revision snapshot. Assert one legal result, one `ConflictError`, consistent supersession, monotonic revisions, deterministic lock order, and no partial decision/audit/outbox rows.

```python
async def test_accessible_object_without_approve_is_403(session):
    context, version = await seed_accessible_review_version(session, can_approve=False)
    with pytest.raises(WorkspaceAccessDenied):
        await approve_version(session, context, version.id, reason=None)


async def test_foreign_workspace_object_is_404(session):
    context, foreign_version = await seed_foreign_version(session)
    with pytest.raises(NotFoundError):
        await approve_version(session, context, foreign_version.id, reason=None)
```

Run: `cd backend && uv run pytest tests/modules/documents/test_service.py tests/isolation/test_document_version_isolation.py -q`

Expected: FAIL on missing services.

- [ ] **Step 2: Implement short transaction commands**

Use the global lock order. Commands capture candidate lifecycle revision and the current-approved ID/revision before waiting for the document lock, then reject drift after locking; serialization alone must not turn two stale competing commands into two valid sequential decisions. Approval requires a locked `review` candidate, ready provenance, a positive page count, `effective_at <= now` when present, and non-expired state. It increments both changed versions’ lifecycle revisions, supersedes the old approved row, approves the candidate, writes immutable decision/audit/outbox rows, and commits. Sequence is allocated as `max(sequence)+1` under the document lock; clients never provide it. Normalize/reject duplicate/confusable version labels before object I/O.

Upload flow is authorize in a short transaction → release session transaction → object write to `{org}/{workspace}/{document}/{version}/source` → reauthorize and create a processing record with unknown page count in a new short transaction → compensate object deletion after rollback on failure. Tests assert `session.in_transaction()` is false at storage calls. Page count is populated only by the parser through the controlled one-way write before review.

Replace the legacy document-delete escape hatch in this task. Object-ID deletion requires `document.upload`, marks only never-approved draft/rejected/failed content as deleting in a committed short transaction, performs Qdrant/object cleanup without an open SQL transaction, and then removes rows in a second locked transaction. Retries resume the deleting marker; governed/processing content is never physically removed, and external cleanup can never run before authorization/state is durably captured. A rejected version with an append-only decision record is governance history: purge its external object, vector projection, and derived provenance, but retain a metadata tombstone because the restrictive decision FK must never be bypassed or cascaded. Before implementing that path, Task 3 must add an explicit bounded deletion/tombstone marker (for example `source_deleted_at`) through a reviewed forward migration; it must not overload lifecycle state or fabricate source identity.

- [ ] **Step 3: Add durable content-free events**

```python
class DocumentVersionEventV1(BaseModel):
    schema_version: Literal[1] = 1
    org_id: UUID
    workspace_id: UUID
    document_id: UUID
    document_version_id: UUID
    previous_state: DocumentVersionState
    new_state: DocumentVersionState
    lifecycle_revision: int = Field(gt=0)
    actor_id: UUID
    occurred_at: datetime
```

Deduplicate by `document-version:{version_id}:{lifecycle_revision}`. Events contain no name, filename, reason text, source hash, or content. Audit actions use identifiers only.

Bounded decision reasons are written only to append-only governance decision records. They are never copied into audit records, outbox events, logs, or operational errors.

This task introduces lifecycle event types only. It deliberately leaves the existing ingestion scheduling path unchanged so an intermediate deployment cannot create both a legacy task and a dormant start command. Task 4 installs behavior-preserving event infrastructure; Task 6 atomically adds the start consumer and runnable executor, inserts ingestion-request outbox commands, and removes direct enqueue in one green commit.

Run: `cd backend && uv run pytest tests/modules/documents/test_service.py tests/isolation/test_document_version_isolation.py -q`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/openrag/modules/documents/events.py \
  backend/src/openrag/modules/documents/service.py backend/src/openrag/modules/documents/ingest.py \
  backend/src/openrag/api/routes/documents.py backend/src/openrag/worker/tasks.py \
  backend/tests/modules/documents/test_service.py backend/tests/modules/documents/test_ingest.py \
  backend/tests/api/test_documents_routes.py backend/tests/isolation/test_document_version_isolation.py
git commit -m "feat: govern document lifecycle transitions"
```

### Task 4: Install event infrastructure without changing ingestion behavior

**Files:**
- Create: `backend/migrations/versions/<revision>_harden_transactional_outbox.py`
- Modify: `backend/src/openrag/modules/events/models.py`
- Modify: `backend/src/openrag/modules/documents/schemas.py`
- Modify: `backend/src/openrag/api/routes/documents.py`
- Create: `backend/src/openrag/modules/events/dispatcher.py`
- Create: `backend/src/openrag/modules/events/envelopes.py`
- Create: `backend/src/openrag/modules/events/streams.py`
- Modify: `backend/src/openrag/worker/tasks.py`
- Modify: `backend/src/openrag/worker/celery_app.py`
- Modify: `backend/src/openrag/core/config.py`
- Modify: `deploy/compose.yaml`
- Modify: `backend/tests/api/test_documents_routes.py`
- Create: `backend/tests/api/test_document_versions.py`
- Create: `backend/tests/modules/events/test_dispatcher.py`
- Create: `backend/tests/modules/events/test_envelopes.py`
- Create: `backend/tests/integration/test_event_streams.py`
- Create: `backend/tests/integration/test_upload_behavior_preserved.py`
- Modify: `backend/tests/worker/test_celery.py`

**Interfaces:**
- Produces safe `DocumentOut`, `DocumentDetailOut`, `DocumentVersionOut`, `DocumentPatch`, and `DocumentVersionDecision`.
- Routes: document list/detail/patch, version list/detail, and approve/reject/obsolete. New nonlegacy version upload/retry routes are enabled only in Task 6 with the runnable version-aware pipeline.
- Produces generic bounded event-envelope parsing, transactional-outbox dispatch, typed stream routing, and idempotent Redis stream/group provisioning.
- Is behavior-preserving for ingestion: the existing direct ingestion path remains enabled and no ingestion/rebuild start command is emitted or consumed in this task.

- [ ] **Step 1: Write RED API, dispatcher, envelope, and behavior-preservation tests**

Cover all available routes, label normalization, client sequence rejection, bounded metadata, safe error codes, exact 404/403 behavior, transition `409`, upload compensation, and absence of storage keys/hashes/internal actor data. Assert approved/superseded content has no physical-delete action and nonlegacy version upload/retry remains unavailable until Task 6.

Dispatcher tests cover bounded/versioned envelope parsing, unknown/malformed schema rejection, `SKIP LOCKED` batch claims, `lease_expires_at`, XADD outside SQL, crash after publish before mark, lease reclaim/duplicate publish, typed event-to-stream routing, payload redaction, and idempotent `XGROUP CREATE openrag:events:document-commands:v1 openrag-document-start-v1 0 MKSTREAM` plus the equivalent lifecycle command at worker startup.

Before dispatcher code, add a forward migration that makes Outbox terminal and
operational state truthful: bounded event/aggregate/dedupe/lease fields,
`attempts >= 0`, `dead_lettered_at`, a bounded safe error code, optional published
stream/message identifiers, and a partial claim index for unpublished,
non-dead-lettered rows. A dead-lettered event is not marked normally published.
The envelope and every registered payload are strict, frozen, versioned Pydantic
contracts with timezone-aware UTC timestamps, `extra='forbid'`, deterministic
encoding, and a 16 KiB maximum. Task 4 registers only the Task 3 lifecycle event;
future ingestion/rebuild command types and their stream are provisioned but are
not routable, emitted, or consumed until Task 6.

The dispatcher claims at most 100 rows using database time and `FOR UPDATE SKIP
LOCKED`, assigns a unique per-batch lease token, copies detached snapshots, and
commits before validation or Redis I/O. XADD occurs with no SQL transaction.
Successful rows are marked only by a conditional update matching the row ID and
lease token. Redis outages leave valid events unpublished and reclaimable; only
permanent contract failures enter a content-free operational DLQ. Crash after
XADD and before the mark intentionally permits duplicate delivery, so consumers
deduplicate by `(consumer,event_id)` and compare lifecycle revisions rather than
trusting stream order.

Use a separate `event_redis_url`, a dedicated `events` worker/queue, and an
explicit periodic dispatcher (Celery beat or a dedicated async service).
Provision streams/groups from the event component, not every legacy ingestion
worker. Event readiness checks PostgreSQL, event Redis, and required groups;
API readiness remains independent during this compatibility task. Production
Redis enables AOF with persistent storage before any Outbox row may be treated
as durably published. Do not trim command/lifecycle streams while pending
consumer entries may exist; retention/pruning requires a later acknowledged
watermark policy.

Add real concurrency/crash tests for disjoint claimers, expired lease reclaim,
lease theft, partial publish success, crash before/after XADD, duplicate publish,
safe DLQ redaction, Inbox atomicity, ACK only after commit and outside SQL,
idempotent/concurrent group creation, component-specific readiness, and event
Redis failure while the separate Celery broker and direct legacy ingestion path
remain healthy. Sentinel document text, prompts, object keys, hashes, credentials,
and raw exception messages must appear in neither logs nor DLQ fields.

The behavior-preservation integration test uploads and retries the exact Legacy-1 path through HTTP, proves the existing direct ingestion worker is invoked exactly once per attempt and still reaches the legacy expected terminal state, and asserts zero `document.version.ingestion_requested.v1`/`rebuild_requested.v1` outbox records, zero command-stream entries, and zero queued `IngestStageAttempt` rows. It also proves nonlegacy version ingestion cannot be invoked before Task 6. This prevents a half-migrated duplicate path at the Task 4 deployment boundary.

Run: `cd backend && uv run pytest tests/api/test_documents_routes.py tests/api/test_document_versions.py tests/modules/events/test_dispatcher.py tests/modules/events/test_envelopes.py tests/integration/test_upload_behavior_preserved.py tests/worker/test_celery.py -q`

Expected: FAIL because the generic dispatcher/envelope/provisioner and governance contracts do not exist; the existing direct-ingestion regression remains green.

- [ ] **Step 2: Implement generic outbox dispatch and Redis provisioning**

`events.dispatch_outbox` claims at most 100 unpublished/unleased rows in a short transaction, sets `lease_owner/lease_expires_at`, commits, performs `XADD` outside SQL, then conditionally marks `published_at` in a second short transaction. Duplicate publish after a crash is expected. A typed registry maps event schema names to bounded stream names; unknown schemas fail to a content-free operational DLQ without leaking event data.

At worker startup, `ensure_event_streams` idempotently creates `openrag:events:document-commands:v1`/`openrag-document-start-v1` and `openrag:events:document-lifecycle:v1`/`openrag-document-lifecycle-v1` with `MKSTREAM`; concurrent startups tolerate `BUSYGROUP`. Redis unavailability fails the event-dispatch worker readiness but does not replace or disable the still-working direct ingestion path. This task installs no document-start consumer.

- [ ] **Step 3: Implement schemas and routes without an enqueue cutover**

`DocumentVersionDecision.reason` is optional and ≤500 characters. `DocumentPatch` bounds name 255, department/type 120, and external identifier 255. `DocumentVersionOut` exposes sequence, display label, state, provenance/readiness, safe error code, dates, page count, and lifecycle revision; it excludes object keys, hashes, projection internals, and raw parser errors.

Routes use the existing service/direct-ingestion orchestration unchanged only for initial Legacy-1 upload/retry. They do not expose new nonlegacy version ingestion yet. Do not write an ingestion/rebuild start outbox event, do not add a start consumer, and do not remove or bypass the working direct enqueue. Task 6 atomically exposes nonlegacy version upload/retry and performs the behavior change only after page-local persistence, authority storage, start consumption, and runnable version-aware stage execution all exist in the same commit.

Run: `cd backend && uv run pytest tests/api/test_documents_routes.py tests/api/test_document_versions.py tests/modules/events/test_dispatcher.py tests/modules/events/test_envelopes.py tests/integration/test_upload_behavior_preserved.py tests/worker/test_celery.py -q`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/openrag/modules/documents/schemas.py backend/src/openrag/api/routes/documents.py \
  backend/src/openrag/modules/events/dispatcher.py backend/src/openrag/modules/events/envelopes.py \
  backend/src/openrag/worker/tasks.py backend/src/openrag/core/config.py \
  backend/tests/api/test_documents_routes.py backend/tests/api/test_document_versions.py \
  backend/tests/modules/events/test_dispatcher.py backend/tests/modules/events/test_envelopes.py \
  backend/tests/integration/test_upload_behavior_preserved.py backend/tests/worker/test_celery.py
git commit -m "feat: install behavior preserving event transport"
```

### Task 5: Define and persist page-local provenance

**Files:**
- Modify: `backend/src/openrag/modules/documents/pipeline.py`
- Modify: `backend/src/openrag/modules/documents/ingest.py`
- Test: `backend/tests/modules/documents/test_pipeline_parse.py`
- Test: `backend/tests/modules/documents/test_pipeline_chunk.py`
- Test: `backend/tests/modules/documents/test_ingest.py`
- Modify: `backend/tests/integration/test_ingestion_e2e.py`

**Interfaces:**
- Produces pure `PageBlock`, parent `Chunk`, and page-local `EvidenceSpan` contracts and version-scoped persistence.

- [ ] **Step 1: Write RED exact-page and idempotent provenance tests**

```python
def test_multi_page_chunk_becomes_page_local_evidence_spans() -> None:
    chunks, spans = chunk_blocks(two_page_blocks())
    assert chunks[0].page_start == 1 and chunks[0].page_end == 2
    assert [(span.page_number, span.text) for span in spans] == [
        (1, "page one evidence"), (2, "page two evidence")
    ]
```

Also assert deterministic IDs, exact artifact byte ranges, bounded section JSON, same-version membership, retry without duplicate metadata, and no Qdrant/object I/O while a SQL transaction is open.

Run: `cd backend && uv run pytest tests/modules/documents/test_pipeline_parse.py tests/modules/documents/test_pipeline_chunk.py tests/modules/documents/test_ingest.py -q`

Expected: FAIL because page-local spans/rebuild do not exist.

- [ ] **Step 2: Implement bounded provenance**

Normalize headings and enforce section bounds before persistence. A page is always a positive PDF/Word page, slide number, or sheet ordinal; locator label preserves type/name. Chunk parents may span pages, but every indexed `EvidenceSpan` contains text from exactly one page and one section path. Compute SHA-256 over the exact normalized UTF-8 span text that Qdrant stores.

Use deterministic UUIDv5 IDs from `version:kind:ordinal:hash`. Persist metadata in short transactions after object/CPU work. Once provenance is ready, DB triggers prevent silent mutation.

- [ ] **Step 3: Persist provenance without scheduling work directly**

Pure persistence helpers accept completed parse/chunk/span values and write deterministic metadata in short transactions. They do not enqueue Celery tasks, publish Redis events, switch collections, mark authority ready, or enable a workspace. New ingestion and legacy rebuild scheduling are Task 6.

Run: `cd backend && uv run pytest tests/modules/documents/test_pipeline_parse.py tests/modules/documents/test_pipeline_chunk.py tests/modules/documents/test_ingest.py tests/integration/test_ingestion_e2e.py -q`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/openrag/modules/documents/pipeline.py \
  backend/src/openrag/modules/documents/ingest.py \
  backend/tests/modules/documents \
  backend/tests/integration/test_ingestion_e2e.py
git commit -m "feat: persist exact page provenance"
```

### Task 6: Build the durable pipeline and atomically cut over ingestion

**Files:**
- Create: `backend/src/openrag/modules/documents/rebuild.py`
- Create: `backend/src/openrag/modules/documents/stages.py`
- Create: `backend/src/openrag/modules/documents/start_events.py`
- Create: `backend/src/openrag/modules/documents/authority_storage.py`
- Modify: `backend/src/openrag/modules/documents/service.py`
- Modify: `backend/src/openrag/modules/documents/ingest.py`
- Modify: `backend/src/openrag/modules/documents/models.py`
- Modify: `backend/src/openrag/api/routes/documents.py`
- Modify: `backend/src/openrag/cli.py`
- Modify: `backend/src/openrag/worker/tasks.py`
- Create: `backend/tests/modules/documents/test_stages.py`
- Create: `backend/tests/modules/documents/test_start_events.py`
- Create: `backend/tests/modules/documents/test_rebuild.py`
- Create: `backend/tests/modules/documents/test_authority_storage.py`
- Modify: `backend/tests/modules/documents/test_service.py`
- Modify: `backend/tests/api/test_document_versions.py`
- Create: `backend/tests/cli/test_authority_rebuild.py`
- Modify: `backend/tests/worker/test_celery.py`
- Create: `backend/tests/integration/test_ingestion_replay.py`
- Create: `backend/tests/integration/test_upload_durable_pipeline.py`

**Interfaces:**
- `document.version.ingestion_requested.v1` and `document.version.rebuild_requested.v1` are the only start commands.
- Produces durable `IngestStageAttempt(version_id, pipeline_kind, stage, state, checkpoint, lease_owner, lease_expires_at, attempts)` rows and resumable workers.
- Produces idempotent authority-collection provisioning plus a restart-safe keyset-paginated `legacy_pending` scanner available through CLI and Celery beat.
- Atomically changes create/retry to emit ingestion-request commands and removes direct enqueue only after the start consumer and runnable stage executor exist in this same commit.

- [ ] **Step 1: Write RED storage-ordering, scanner, start-consumer, cutover, and crash/replay tests**

Assert the authority physical collection and every exact dense/sparse/payload index exist and match configuration before an authority-upsert stage is claimable. A missing/wrong collection or index leaves that stage queued with `AUTHORITY_STORAGE_NOT_READY`; it must never opportunistically create schema from the stage worker and must never write `openrag_chunks`.

Test the legacy scanner with 1,001 `legacy_pending` versions, a bounded page size, deterministic `(workspace_id,version_id)` keyset order, cursor/progress commit after each page, restart midway, insertion below/above the cursor, end-of-pass cursor reset, two concurrent scanners, and repeat passes. Every eligible version eventually receives one idempotent `document.version.rebuild_requested.v1`; ready/nonlegacy/in-flight versions are skipped; progress counters are monotonic and restart-visible.

Test crash before stage claim commit, crash during CPU/object/Qdrant work, crash after external success before DB acknowledgement, expired lease reclaim, stage retry limit/DLQ, cancellation, rebuild resume from each checkpoint, and exactly-once logical transition to review/ready despite at-least-once execution.

The full integration test begins with HTTP upload and proves `HTTP → Outbox → Redis → Inbox → queued stage → claimed/executed parse → chunk/page-local persistence → embed → authority upsert → review`. It then repeats delivery/restarts workers and proves one logical attempt/result. Patch the legacy direct `enqueue_ingest`, Celery `send_task`, and after-commit scheduling hooks to raise; upload and retry must still complete through the durable path, and no direct call is observed.

Run: `cd backend && uv run pytest tests/modules/documents/test_authority_storage.py tests/modules/documents/test_start_events.py tests/modules/documents/test_stages.py tests/modules/documents/test_rebuild.py tests/modules/documents/test_service.py tests/api/test_document_versions.py tests/cli/test_authority_rebuild.py tests/integration/test_ingestion_replay.py tests/integration/test_upload_durable_pipeline.py -q`

Expected: FAIL because provisioning gates, the start consumer, runnable durable workers, the paginated legacy scanner, and the atomic cutover do not exist.

- [ ] **Step 2: Provision strict authority storage before workers can upsert**

`provision_authority_storage(generation)` idempotently creates `openrag_authority_v1_<generation>` with configured dense dimension/distance and sparse-vector configuration, then creates and verifies exact payload indexes: `tenant_id`, `workspace_id`, `document_id`, `document_version_id`, and `evidence_span_id` as keyword; `is_current_approved` as bool; `projection_revision` and `page_number` as integer. It creates/updates `openrag_authority_active_v1` only through the explicit generation workflow and never aliases or writes `openrag_chunks`.

Expose `openrag authority provision --generation <uuid>` and an idempotent deployment-start task. Both fail nonzero/unready on schema mismatch. The stage claimant calls a read-only cached readiness probe before selecting an authority-upsert stage; after selection it rechecks the exact physical collection immediately before the external upsert. The physical collection name is part of the attempt checkpoint, preventing a retry from silently switching generations.

- [ ] **Step 3: Add restart-safe paginated legacy discovery**

`scan_legacy_pending(page_size, max_pages)` is shared by `openrag authority enqueue-legacy-rebuilds` and a bounded Celery beat task. In each short transaction it locks one workspace scanner checkpoint, keyset-selects a bounded page of `provenance_state='legacy_pending'` versions that have no completed/current rebuild, inserts `document.version.rebuild_requested.v1` with dedupe key `document-version:{version_id}:rebuild:1`, advances the durable cursor, and commits. It performs no object/Qdrant/Redis I/O.

At the end of a pass, reset the cursor and increment `pass_number`, so versions inserted below the previous cursor are found. Concurrent CLI/beat runs use `SKIP LOCKED` checkpoints. Progress exposes only workspace ID, pass/page/scanned/emitted/skipped counts, cursor, and timestamps. The Task 4 dispatcher and the start consumer added below turn emitted rebuild requests into idempotent queued attempts.

- [ ] **Step 4: Add the replay-safe document-start consumer**

`consume_document_starts` uses the Task 4 command stream/group and bounded envelope parser. For ingestion/rebuild requests it locks the version and in one short transaction inserts `InboxEvent(consumer,event_id)` plus the first queued `IngestStageAttempt`; unique Inbox and `(version_id,pipeline_kind,stage,checkpoint)` keys make duplicates harmless. Commit precedes ACK. Reclaim pending messages after 30 seconds. After eight failed deliveries, write only event ID/type/tenant identifiers/safe code to the command DLQ and ACK. It performs no parse/provider/Qdrant/object I/O and is registered in `worker/tasks.py` before the cutover step.

- [ ] **Step 5: Implement a runnable lease-based stage executor**

A Celery beat worker claims queued/expired attempts in a short DB transaction using `SKIP LOCKED`, sets `lease_owner/lease_expires_at`, and commits. Parse/chunk/embed/Qdrant work occurs outside SQL. A second short transaction records output digest/checkpoint and creates the next queued stage. Qdrant upserts use deterministic span IDs, so crash/replay is idempotent. After the final verified stage, transition new ingestion to `review` or legacy rebuild to provenance `ready`; emit lifecycle/projection events transactionally.

Legacy rebuild and new ingestion authority-upsert stages write only to the provisioned physical collection in their checkpoint, with `is_current_approved=false`; they never write `openrag_chunks`. Missing schema readiness prevents claim, and schema drift detected before upsert returns the attempt to queued/fail-closed state. Failure keeps the disabled workspace on legacy reads and records a safe visible processing failure.

- [ ] **Step 6: Atomically switch create/retry after the durable path is runnable**

Only now change `create_document_record`, `create_version_record`, and retry transactions to write `document.version.ingestion_requested.v1` with dedupe key `document-version:{version_id}:ingestion:{attempt}`. In the same commit remove the legacy route/service direct enqueue and after-commit scheduling code. There is no feature state where both paths run: before this commit Task 4 direct ingestion is preserved and emits no command; after this commit the command is transactional and direct enqueue is absent.

Run the full HTTP integration with real service/route wiring, fake bounded external adapters, Task 4 dispatcher, Task 6 start consumer, and lease-stage worker until the version reaches `review`. Assert exactly one start outbox event, stream logical event, Inbox row, stage chain, and final transition despite duplicate delivery; assert no direct enqueue call and no open SQL transaction during external work.

Run: `cd backend && uv run pytest tests/modules/documents/test_authority_storage.py tests/modules/documents/test_start_events.py tests/modules/documents/test_stages.py tests/modules/documents/test_rebuild.py tests/modules/documents/test_service.py tests/api/test_document_versions.py tests/cli/test_authority_rebuild.py tests/worker/test_celery.py tests/integration/test_ingestion_replay.py tests/integration/test_upload_durable_pipeline.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/openrag/modules/documents/rebuild.py \
  backend/src/openrag/modules/documents/stages.py backend/src/openrag/modules/documents/start_events.py \
  backend/src/openrag/modules/documents/authority_storage.py backend/src/openrag/modules/documents/service.py \
  backend/src/openrag/modules/documents/ingest.py backend/src/openrag/modules/documents/models.py \
  backend/src/openrag/api/routes/documents.py backend/src/openrag/cli.py backend/src/openrag/worker/tasks.py \
  backend/tests/modules/documents/test_start_events.py backend/tests/modules/documents/test_stages.py \
  backend/tests/modules/documents/test_rebuild.py backend/tests/modules/documents/test_service.py \
  backend/tests/modules/documents/test_authority_storage.py backend/tests/cli/test_authority_rebuild.py \
  backend/tests/api/test_document_versions.py backend/tests/worker/test_celery.py \
  backend/tests/integration/test_ingestion_replay.py backend/tests/integration/test_upload_durable_pipeline.py
git commit -m "feat: cut ingestion over to durable pipelines"
```

### Task 7: Consume lifecycle events and project current eligibility

**Files:**
- Create: `backend/src/openrag/modules/documents/projection.py`
- Modify: `backend/src/openrag/modules/retrieval/client.py`
- Modify: `backend/src/openrag/worker/tasks.py`
- Create: `backend/tests/modules/documents/test_projection.py`
- Modify: `backend/tests/worker/test_celery.py`
- Create: `backend/tests/integration/test_lifecycle_stream.py`

**Interfaces:**
- Produces Qdrant fields `is_current_approved`, `projection_revision`, and immutable `document_version_id`.
- Uses the Task 4 dispatcher/provisioned lifecycle consumer group and the Task 6 pre-provisioned physical collection; it neither creates storage schema nor performs cutover.

- [ ] **Step 1: Write RED projection idempotency/retry/order tests**

Test Inbox dedupe, Redis ACK only after Qdrant+watermark success, retry delivery count, redacted DLQ after eight attempts, crash after Qdrant before Inbox/watermark commit, pending reclaim, stale event ignored by revision, newer event wins, consumer restart, review points always false, and no write to the legacy collection. Assert startup refuses to consume when the Task 6 physical collection or any exact payload index is absent/wrong; the consumer must not create or repair schema itself.

Run: `cd backend && uv run pytest tests/modules/documents/test_projection.py tests/worker/test_celery.py tests/integration/test_lifecycle_stream.py -q`

Expected: FAIL because no lifecycle projection consumer exists.

- [ ] **Step 2: Implement the replay-safe lifecycle consumer**

`events.consume_document_lifecycle` uses the Task 4 provisioned group with `XREADGROUP`. It validates the content-free event, verifies Task 6 authority storage before external work, performs Qdrant projection outside SQL, then inserts `InboxEvent(consumer,event_id)` and the projection watermark in a short transaction, commits, and ACKs outside SQL. Failed deliveries remain pending; reclaim after 30 seconds; after eight failures write a redacted envelope to `openrag:dlq:document-lifecycle`, ACK, and emit a safe operational error.

- [ ] **Step 3: Project monotonically into the already provisioned collection**

Open the configured Task 6 physical `openrag_authority_v1_<generation>` only after verifying dense vector size/distance, sparse vector config, and the exact payload indexes. Never reuse or write `openrag_chunks`, and never create an index from an event handler.

New/review points are written with `is_current_approved=false`. Approved events set candidate span points true; superseded/obsolete set false. Apply only when incoming lifecycle revision is ≥ payload projection revision. After Qdrant success, upsert `DocumentVersionProjection.applied_revision/applied_at`. PostgreSQL remains authoritative.

Run: `cd backend && uv run pytest tests/modules/documents/test_projection.py tests/worker/test_celery.py tests/integration/test_lifecycle_stream.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/openrag/modules/documents/projection.py \
  backend/src/openrag/modules/retrieval/client.py backend/src/openrag/worker/tasks.py \
  backend/tests/modules/documents/test_projection.py backend/tests/worker/test_celery.py \
  backend/tests/integration/test_lifecycle_stream.py
git commit -m "feat: project authoritative lifecycle state"
```

### Task 8: Retrieve projected spans with real component scores and DB revalidation

**Files:**
- Create: `backend/src/openrag/modules/documents/policy.py`
- Modify: `backend/src/openrag/modules/retrieval/service.py`
- Modify: `backend/src/openrag/modules/retrieval/schemas.py`
- Modify: `backend/src/openrag/api/routes/search.py`
- Create: `backend/tests/modules/documents/test_policy.py`
- Modify: `backend/tests/modules/retrieval/test_retrieve.py`
- Modify: `backend/tests/api/test_search_route.py`
- Create: `backend/tests/isolation/test_retrieval_version_isolation.py`

**Interfaces:**
- Produces `EligibleVersionSnapshot`, `RetrievedEvidenceSpan`, and `RetrievalResult` with dense/sparse/fused score provenance.
- Uses an explicit DB-flag branch: disabled → read-only legacy collection only; enabled → authority alias only. The strict branch uses projected current marker plus bounded lag fallback and never sends an unbounded `MatchAny` list.

- [ ] **Step 1: Write RED lifecycle, tamper, score, and lag tests**

Cover disabled workspace querying only `openrag_chunks`; enabled workspace querying only `openrag_authority_active_v1`; never querying/merging both; no duplicate result across cutover; review/rejected/failed points never selected even if present; all excluded dates/tenants; stale projected old version dropped; forged current marker dropped; Qdrant returned text changed without payload-hash change dropped; span/version mismatch dropped; dense and sparse scores equal separate query results; deterministic RRF; ≤64 lagging current versions included by fallback; 65 or >30-second lag fails closed with `INDEX_PROJECTION_LAG`; no eligible versions avoids embedding/Qdrant.

Run: `cd backend && uv run pytest tests/modules/documents/test_policy.py tests/modules/retrieval/test_retrieve.py tests/isolation/test_retrieval_version_isolation.py -q`

Expected: FAIL on current tenant-only FusionQuery behavior.

- [ ] **Step 2: Implement projected candidate generation and real RRF**

Resolve `Workspace.document_authority_enabled` from PostgreSQL before vector access. If false, call the isolated legacy retriever and return its legacy contract; do not touch the authority alias. If true, call only the authority alias and never fall back to legacy on empty/error. Run separate dense and sparse authority queries concurrently with tenant/workspace/`is_current_approved=true`. Record raw component scores by point ID, then compute RRF in OpenRAG with `k=60` and deterministic point-ID tie break. Do not label Qdrant FusionQuery output as a component score. Rerank remains nullable.

Query PostgreSQL for projection lag. Add at most 64 recently changed version IDs (age ≤30 seconds) through a second bounded `MatchAny` query and fuse; larger/older lag returns a typed fail-closed result. This fallback covers event propagation without scaling the filter with the whole repository.

- [ ] **Step 3: Batch revalidate exact text and provenance**

Parse payload IDs/locators with strict Pydantic. Batch join span→chunk→version→document→workspace and require current approved/effective/non-expired state, ready provenance, authorized workspace, exact IDs, and positive page/section. Recompute SHA-256 from the **returned Qdrant text** after the same normalization and compare with PostgreSQL `DocumentEvidenceSpan.content_hash`; never trust a payload hash. Construct displayed name/version/section/page only from PostgreSQL.

The public search response includes name/version/section/page and component scores but excludes storage keys, internal hashes, and projection internals.

Run: `cd backend && uv run pytest tests/modules/documents/test_policy.py tests/modules/retrieval/test_retrieve.py tests/api/test_search_route.py tests/isolation/test_retrieval_version_isolation.py -q`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/openrag/modules/documents/policy.py backend/src/openrag/modules/retrieval \
  backend/src/openrag/api/routes/search.py backend/tests/modules/documents/test_policy.py \
  backend/tests/modules/retrieval backend/tests/api/test_search_route.py \
  backend/tests/isolation/test_retrieval_version_isolation.py
git commit -m "feat: retrieve current verified evidence spans"
```

### Task 9: Add a locked, SSRF-safe in-process LiteLLM verifier adapter

**Files:**
- Create: `backend/src/openrag/modules/grounding/litellm_verifier.py`
- Create: `backend/src/openrag/core/provider_network.py`
- Modify: `backend/src/openrag/core/logging.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Create: `backend/tests/modules/grounding/test_litellm_verifier.py`
- Create: `backend/tests/core/test_provider_network.py`
- Modify: `backend/tests/core/test_logging.py`
- Create: `backend/tests/architecture/test_provider_imports.py`

**Interfaces:**
- Produces `SafeProviderEndpoint`, `PinnedResolverTransport`, and bounded `invoke_verifier` as the sole LiteLLM import boundary.

- [ ] **Step 1: Write RED dependency, import, SSRF, credential, and redaction tests**

Architecture tests fail while `litellm` is absent, on a direct provider SDK import, or on a LiteLLM import outside the adapter. Network tests reject HTTP in production, credentials in URL, fragments, disallowed ports/hosts, IPv4/IPv6 loopback/private/link-local/multicast/reserved/metadata destinations, integer/hex/octal IP forms, mixed public/private DNS, DNS rebinding between validation/connect, redirects, and an allowlisted hostname resolving to a forbidden address. Credential tests assert decrypt-in-short-session → close session → call-scoped argument, no environment/global mutation, no credential in exception/log/trace/response, and recursive nested-list/dict redaction.

Run: `cd backend && uv run pytest tests/modules/grounding/test_litellm_verifier.py tests/core/test_provider_network.py tests/core/test_logging.py tests/architecture/test_provider_imports.py -q`

Expected: FAIL because dependency/adapter/network policy do not exist.

- [ ] **Step 2: Lock the dependency and import boundary**

Add `"litellm>=1.74,<2"`, run `uv lock`, and commit exact resolved versions. `litellm_verifier.py` is the sole import boundary:

```python
from litellm import acompletion


async def invoke_verifier(*, model: str, messages: list[dict[str, str]], timeout: float) -> dict[str, object]:
    response = await acompletion(
        model=model,
        messages=messages,
        timeout=timeout,
        stream=False,
        response_format={"type": "json_object"},
    )
    return parse_bounded_response(response)
```

The adapter accepts credentials only as call-local arguments, disables verbose provider logging, uses zero redirects, bounds timeout/retries/response bytes, and normalizes exceptions to safe codes. Unit tests inject a fake callable.

Add an import-linter/AST architecture gate: direct AI-provider packages (`openai`, `anthropic`, `google.generativeai`, `cohere`) and direct `bedrock-runtime` client construction are forbidden throughout `openrag`; ordinary object-storage use of `aioboto3` remains allowed. `litellm` imports are allowed only in `grounding/litellm_verifier.py`. This verifier is OpenRAG’s first bounded in-process LiteLLM path. Existing completion proxy removal and all ordinary completion/embedding migration remain explicitly in the later AI-profile/orchestration slice; this task must not claim the whole application is proxy-free.

Run: `cd backend && uv lock && uv sync --locked && uv run pytest tests/architecture/test_provider_imports.py tests/modules/grounding/test_litellm_verifier.py -q && uv run lint-imports`

Expected: PASS with `uv.lock` changed and no direct provider SDK import.

- [ ] **Step 3: Enforce allowlisted DNS-pinned egress and call-scoped secrets**

Provider endpoints must be in the server-configured hostname/port allowlist. Parse/canonicalize URL, resolve all A/AAAA records with a bounded resolver, reject any forbidden address, and use a custom no-redirect transport that connects only to the validated IP set while preserving original Host header/TLS SNI. Re-resolve before each connection and require the set to match; DNS change fails closed. If the installed LiteLLM version cannot accept the pinned client for a custom endpoint, reject custom verifier base URLs rather than falling back to unsafe resolution.

Resolve the encrypted `model:{id}` secret in a short session, close it, invoke with the credential argument, and discard the local reference in `finally`. Never set `OPENAI_API_KEY` or another process environment/global. Extend recursive redaction for mappings/sequences and provider exception strings.

Run: `cd backend && uv run pytest tests/core/test_provider_network.py tests/core/test_logging.py tests/modules/grounding/test_litellm_verifier.py -q`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/openrag/modules/grounding/litellm_verifier.py \
  backend/src/openrag/core/provider_network.py backend/src/openrag/core/logging.py \
  backend/tests/modules/grounding/test_litellm_verifier.py backend/tests/core \
  backend/tests/architecture/test_provider_imports.py backend/pyproject.toml backend/uv.lock
git commit -m "feat: secure in process litellm verifier"
```

This is the first bounded in-process LiteLLM path. Completion-proxy removal and ordinary completion/embedding migration remain later work; do not claim the application is fully proxy-free.

### Task 10: Add grounding-policy administration, calibration, and entailment

**Files:**
- Create: `backend/src/openrag/modules/grounding/evidence.py`
- Create: `backend/src/openrag/modules/grounding/entailment.py`
- Create: `backend/src/openrag/modules/grounding/claims.py`
- Create: `backend/src/openrag/modules/grounding/policy.py`
- Create: `backend/src/openrag/modules/grounding/calibration.py`
- Create: `backend/src/openrag/modules/grounding/schemas.py`
- Create: `backend/src/openrag/api/routes/grounding.py`
- Modify: `backend/src/openrag/api/app.py`
- Modify: `backend/src/openrag/bootstrap.py`
- Modify: `backend/src/openrag/modules/events/dispatcher.py`
- Modify: `backend/src/openrag/modules/models/models.py`
- Modify: `backend/src/openrag/worker/tasks.py`
- Test: `backend/tests/modules/grounding/test_evidence.py`
- Test: `backend/tests/modules/grounding/test_entailment.py`
- Test: `backend/tests/modules/grounding/test_claims.py`
- Test: `backend/tests/modules/grounding/test_policy.py`
- Create: `backend/tests/api/test_grounding_policies.py`
- Create: `backend/tests/integration/test_calibration_stream.py`
- Modify: `backend/tests/worker/test_celery.py`
- Modify: `backend/tests/test_bootstrap.py`

**Interfaces:**
- Produces typed claims/evidence/entailment and complete create/list/inspect/bind/calibrate/activate/retire policy APIs.
- Produces an explicit replay-safe calibration request consumer, durable run record, stream/group, retry/reclaim/DLQ policy, and worker registration.

- [ ] **Step 1: Write RED policy lifecycle/API/capability tests**

Structural tests reject malformed claims, unknown markers, missing page provenance, numeric/date/code mismatch, and oversize records. Entailment tests cover supported paraphrase, token-overlap-but-unsupported, negation, conflicts, timeout/error/malformed JSON, and below threshold. API tests cover:

```text
GET/POST /workspaces/{workspace_id}/grounding-policies
GET /grounding-policies/{policy_id}
POST /grounding-policies/{policy_id}/bind
POST /grounding-policies/{policy_id}/calibrate
GET /grounding-policies/{policy_id}/calibration
POST /grounding-policies/{policy_id}/activate
POST /grounding-policies/{policy_id}/retire
```

Assert exact `rag.evaluate`/`model.configure` combinations, object-oracle rules, immutable passed policies, asynchronous calibration outbox/replay, only one active policy, and production bootstrap refusing fake/demo policy. The calibration integration test proves POST → calibration run/outbox → generic dispatcher → provisioned calibration stream/group → consumer → provider call outside SQL → signed aggregate result plus Inbox commit → ACK. Cover duplicate request/delivery, crash before provider, crash after provider before result commit, crash after result commit before ACK, pending reclaim, restart, provider timeout/malformed result, eight-delivery redacted DLQ, and no prompt/evidence/credential/provider body in DB events, Redis, DLQ, logs, or API.

Run: `cd backend && uv run pytest tests/modules/grounding/test_evidence.py tests/modules/grounding/test_entailment.py tests/modules/grounding/test_claims.py tests/modules/grounding/test_policy.py tests/api/test_grounding_policies.py tests/integration/test_calibration_stream.py tests/worker/test_celery.py tests/test_bootstrap.py -q`

Expected: FAIL because policy administration/calibration do not exist.

- [ ] **Step 2: Implement model/credential capability validation**

The Task 1/2 model schema includes bounded capability flags `supports_chat_completion`, `supports_structured_json`, and `supports_verifier`, plus provider preset version. Binding requires enabled+synced model, all three verifier capabilities, allowlisted LiteLLM model identifier/provider, a credential when the provider requires one, an endpoint passing Task 9 policy, and a successful bounded validation call. APIs expose only credential fingerprint/presence, never value. A disabled/rotated/invalid model makes the binding unusable and blocks activation/release.

- [ ] **Step 3: Implement complete policy lifecycle and enqueue calibration**

Create/list/inspect are workspace/tenant scoped. Bind requires both `model.configure` and `rag.evaluate`. Calibrate creates or returns an idempotent `GroundingCalibrationRun`, writes `grounding.policy.calibration_requested.v1` in the same transaction, and returns `202` with run ID/status; no API request invokes the verifier. Activate locks workspace/policy, requires a passed calibration run matching the exact dataset/model/preset/binding/credential fingerprints, revalidates capabilities/model/credential/endpoint, and retires the old active policy atomically. Retire refuses if it would leave an authority-enabled workspace without an active policy.

- [ ] **Step 4: Wire the replay-safe calibration worker and consumer**

Extend the Task 4 typed stream registry with `grounding.policy.calibration_requested.v1`; idempotently provision `openrag-grounding-calibration-v1` at worker startup. The registered consumer checks Inbox/run state in a short transaction, marks the run running with a lease, closes SQL, evaluates the immutable versioned dataset through Task 9, then in a second short transaction revalidates the requested fingerprints, stores only signed aggregate metrics/pass-fail, marks the Inbox row, and commits before ACK.

Duplicate delivery returns the stored result. Lease expiry/reclaim resumes safely; crash after an external call may repeat the bounded calibration but cannot create a second logical result. After eight deliveries, atomically mark the run failed with a safe code, write a redacted DLQ envelope, and ACK. Worker health fails when the stream/group is unavailable. Tests inject fakes; live keys are never read.

- [ ] **Step 5: Implement fail-closed semantic entailment/conflict**

Structural validation proves schema/provenance/numeric fidelity only. `LiteLLMEntailmentVerifier` sends one claim plus cited page-local spans through Task 9 and applies the active calibrated threshold. Missing/expired policy, model binding change, credential failure, timeout, provider error, invalid JSON, or low score fails closed. High-confidence contradictory current sources produce only a controlled conflict response citing both sides.

- [ ] **Step 6: Add safe demo/bootstrap**

In explicit development mode only, `bootstrap_demo_grounding_policy` binds a configured demo model, runs the same synthetic calibration path, and activates only on pass. Production rejects the demo flag at startup. No bootstrap creates a fake “passed” result or embeds a credential.

Run: `cd backend && uv run pytest tests/modules/grounding tests/api/test_grounding_policies.py tests/integration/test_calibration_stream.py tests/worker/test_celery.py tests/test_bootstrap.py -q`

Expected: PASS with fake adapter/calibration transports and no credential output.

- [ ] **Step 7: Commit**

```bash
git add backend/src/openrag/modules/grounding backend/src/openrag/api/routes/grounding.py \
  backend/src/openrag/api/app.py backend/src/openrag/bootstrap.py \
  backend/src/openrag/modules/events/dispatcher.py backend/src/openrag/modules/models/models.py \
  backend/src/openrag/worker/tasks.py backend/tests/modules/grounding \
  backend/tests/api/test_grounding_policies.py backend/tests/integration/test_calibration_stream.py \
  backend/tests/worker/test_celery.py backend/tests/test_bootstrap.py
git commit -m "feat: govern calibrated grounding policies"
```

### Task 11: Evaluate and activate document authority asynchronously

**Files:**
- Create: `backend/src/openrag/modules/documents/readiness.py`
- Modify: `backend/src/openrag/modules/documents/schemas.py`
- Modify: `backend/src/openrag/api/routes/documents.py`
- Modify: `backend/src/openrag/modules/events/dispatcher.py`
- Modify: `backend/src/openrag/worker/tasks.py`
- Create: `backend/tests/modules/documents/test_readiness.py`
- Create: `backend/tests/api/test_document_authority.py`
- Create: `backend/tests/integration/test_readiness_stream.py`
- Modify: `backend/tests/worker/test_celery.py`

**Interfaces:**
- Produces `POST /api/v1/admin/workspaces/{workspace_id}/document-authority/readiness`, `GET /api/v1/admin/workspaces/{workspace_id}/document-authority/readiness`, and `POST /api/v1/admin/workspaces/{workspace_id}/document-authority/activate`.
- Produces replay-safe `document.authority.readiness_requested.v1` handling and signed immutable readiness results.
- Requires an active, unexpired, passed grounding policy at request, evaluation, and activation time.

- [ ] **Step 1: Write RED asynchronous readiness and policy-gate tests**

POST requires `rag.evaluate`, an accessible workspace, a currently active/unexpired passed `GroundingPolicy`, and an `Idempotency-Key`. It returns `202` with a generation ID/status/poll URL and commits one building `DocumentAuthorityReadiness` row plus one readiness-request outbox event. The idempotency digest covers workspace, physical collection/schema, lifecycle revision digest, active policy ID/version/calibration hash, verifier model/preset/binding/credential fingerprint, and caller key. Repeats return the same generation; a changed snapshot creates a new one.

Test POST/GET/activate tenant oracle and capability behavior, no-policy/expired/retired/unpassed-policy rejection, request/outbox atomicity, dispatcher routing, stream/group provisioning, worker restart and duplicate delivery, external-check crash before result commit, result commit before ACK, pending reclaim, redacted DLQ, signature tampering, expiry/staleness, safe GET polling, concurrent activation, lifecycle/projection/policy/model/binding/credential change between request/evaluation/activation, and activation consuming one generation exactly once. No route performs Qdrant/Redis/provider work.

Run: `cd backend && uv run pytest tests/modules/documents/test_readiness.py tests/api/test_document_authority.py tests/integration/test_readiness_stream.py tests/worker/test_celery.py -q`

Expected: FAIL because asynchronous readiness request/evaluation/activation does not exist.

- [ ] **Step 2: Enqueue idempotent readiness evaluations**

In one short transaction, POST authorizes `rag.evaluate`, locks the workspace, resolves the exact active unexpired passed policy and its passed calibration/model/binding/credential snapshot, computes the bounded request digest from authoritative DB revisions, creates or returns the immutable building generation, and writes `document.authority.readiness_requested.v1`. It returns `202`; it never calls Qdrant, Redis, LiteLLM, embedding, or object storage. GET polls a specified/latest generation and exposes safe status, counts, digest prefix, expiry, and blocker codes—never signatures, hashes, credentials, or raw external errors.

- [ ] **Step 3: Verify externally in a replay-safe worker**

Extend the Task 4 registry and worker startup provisioner with `openrag-document-readiness-v1`. The consumer checks Inbox/generation state and leases the building generation in a short transaction, then closes SQL. Outside transactions it verifies the Task 6 collection/alias, vector schema, exact payload indexes, exact point counts and returned-text hashes, page/section provenance, current-version set, and Task 7 projection watermarks. It reopens a short transaction, locks the generation/workspace/policy, recomputes DB lifecycle/policy digests, and stores passed/failed counts plus a canonical SHA-256 digest, 15-minute expiry, and HMAC signature derived from the server KEK with a distinct HKDF context. It inserts Inbox state and commits before ACK.

Duplicate delivery returns the stored result. Lease reclaim retries a building run; after eight safe failures it records failed state, emits a redacted DLQ envelope, and ACKs. Any active-policy/model/binding/credential drift marks the generation stale rather than passed. External calls never occur while a SQL transaction is open.

- [ ] **Step 4: Consume a passed generation during activation**

Activation accepts `generation_id`, requires `rag.evaluate`, and in the global lock order locks workspace, generation, and exact active policy snapshot. It verifies status `passed`, signature, expiry, collection/alias, unchanged lifecycle/projection digest, and the same active/unexpired passed policy/calibration/model/preset/binding/credential fingerprint. It atomically marks the generation `activated`, records the actor, and flips `Workspace.document_authority_enabled=true`. Reuse, concurrent activation, stale state, or a missing/expired policy fails closed. No Qdrant/Redis/provider call occurs while locked, and no worker auto-enables a workspace.

Run: `cd backend && uv run pytest tests/modules/documents/test_readiness.py tests/api/test_document_authority.py tests/integration/test_readiness_stream.py tests/worker/test_celery.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/openrag/modules/documents/readiness.py \
  backend/src/openrag/modules/documents/schemas.py backend/src/openrag/api/routes/documents.py \
  backend/src/openrag/modules/events/dispatcher.py backend/src/openrag/worker/tasks.py \
  backend/tests/modules/documents/test_readiness.py backend/tests/api/test_document_authority.py \
  backend/tests/integration/test_readiness_stream.py backend/tests/worker/test_celery.py
git commit -m "feat: evaluate document authority readiness"
```

### Task 12: Persist immutable grounded answers and citation snapshots

**Files:**
- Create: `backend/src/openrag/modules/grounding/citations.py`
- Modify: `backend/src/openrag/modules/chat/schemas.py`
- Modify: `backend/src/openrag/modules/chat/service.py`
- Test: `backend/tests/modules/grounding/test_citations.py`
- Modify: `backend/tests/modules/chat/test_tree_service.py`
- Modify: `backend/tests/api/test_chat_history.py`

**Interfaces:**
- Produces `CitationSnapshotInput`, `persist_grounded_answer`, `persist_refusal`, and provenance-complete `CitationOut`.

- [ ] **Step 1: Write RED final-release and citation-service tests**

Cover service attempts to persist grounded output without citations, refused output with citations, citation attachment to user/legacy/refused messages, mismatched org/workspace/version/span, supersession between verification and persistence, active policy retirement/expiry/rebind, model disablement, credential rotation, immutable snapshot update, and historical legacy citations displaying as unverified and never seeding new answers. The raw database `Message INSERT/UPDATE` and `Citation INSERT/UPDATE/DELETE`, last-citation direct-delete, and parent message/chat cascade behaviors are already implemented and tested with the migration in Task 2; this task consumes those invariants rather than introducing them later.

Run: `cd backend && uv run pytest tests/modules/grounding/test_citations.py tests/modules/chat/test_tree_service.py tests/api/test_chat_history.py -q`

Expected: FAIL on current six-field citation persistence.

- [ ] **Step 2: Implement final authorization/eligibility transaction**

Immediately before release persistence, open a fresh session, re-resolve the user’s current authorization from the DB, and follow the global lock order. Revalidate every cited span/hash/current version, then lock and require the **same exact** `GroundingPolicy.id/version/calibration_hash`, verifier `Model.id/provider_preset_version`, binding, and credential fingerprint used during claim verification. The policy must still be active and unexpired, model enabled/synced/verifier-capable, binding unchanged, and credential present/not rotated or revoked. Any drift fails closed before message insertion. Insert assistant message plus immutable citation snapshots in one transaction. Snapshot internal fields include org/workspace, logical/version/span IDs, displayed name/version/section/page/locator, recomputed content hash, dense/sparse/fused/rerank scores, prompt/policy/model/binding/credential-fingerprint versions, claim IDs, and verification state.

Public history exposes marker, document name, version, section, exact page/locator, and safe scores/claim IDs. It excludes internal hashes/storage keys. `persist_refusal` writes a server-owned reason and no citations. Invalid generated text is never persisted.

Run: `cd backend && uv run pytest tests/modules/grounding/test_citations.py tests/modules/chat/test_tree_service.py tests/api/test_chat_history.py -q`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/src/openrag/modules/grounding/citations.py \
  backend/src/openrag/modules/chat/schemas.py backend/src/openrag/modules/chat/service.py \
  backend/tests/modules/grounding/test_citations.py \
  backend/tests/modules/chat/test_tree_service.py backend/tests/api/test_chat_history.py
git commit -m "feat: persist immutable grounded answers"
```

### Task 13: Stream progressively verified claims without a request session

**Files:**
- Create: `backend/src/openrag/modules/chat/runner.py`
- Modify: `backend/src/openrag/modules/chat/prompting.py`
- Modify: `backend/src/openrag/modules/chat/events.py`
- Modify: `backend/src/openrag/api/routes/chats.py`
- Modify: `backend/tests/modules/chat/test_prompting.py`
- Modify: `backend/tests/modules/chat/test_events.py`
- Modify: `backend/tests/api/test_chat_stream.py`
- Create: `backend/tests/integration/test_chat_authorization_races.py`

**Interfaces:**
- Produces immutable `ChatRunRequest(user_id,org_id,workspace_id,chat_id,user_message_id,model_id,request_id)`.
- Produces SSE events `retrieval.progress`, `verification.progress`, `heartbeat`, `claim.verified`, `answer.retracted`, `citations`, and terminal `done|error`.

- [ ] **Step 1: Write RED session-lifetime/progressive/retraction tests**

Assert the route commits/closes its request session before the streaming generator first advances; no runner signature accepts `AsyncSession`; authorization is re-resolved before retrieval and final release; revocation during LLM yields `authorization_changed` and no assistant message; no raw model delta is emitted; a verified first claim may stream; a later invalid claim emits `answer.retracted`, frontend-clear payload, server refusal, and no successful partial persistence; heartbeats occur at ≤15 seconds during provider/verifier waits; successful/refused terminals carry `done.committed=true`; an error terminal never claims committed; exactly one terminal event occurs.

Run: `cd backend && uv run pytest tests/api/test_chat_stream.py tests/integration/test_chat_authorization_races.py -q`

Expected: FAIL because current `stream_reply` captures request session and streams raw deltas.

- [ ] **Step 2: Build prompt and incremental NDJSON parser**

Prompt evidence blocks contain escaped server-derived name/version/section/page markers and untrusted span text. Require one complete `GroundedClaimV1` JSON object per line. Parser caps line bytes, total claims, total output bytes, and marker count; partial lines stay private. Raw provider deltas never become SSE.

- [ ] **Step 3: Implement session-free phases and verified-claim streaming**

The API persists user message/run in a short transaction, creates `ChatRunRequest`, and returns a generator using `session_factory`. Each DB phase opens/closes its own session and reauthorizes. Retrieval, LiteLLM, semantic verification, and heartbeat waits have no DB transaction.

For each complete record: structural/numeric validation → semantic entailment → fresh source eligibility check → emit `claim.verified` as provisional. On clean model completion, final Task 12 persistence succeeds, then emit citations and `done` with `committed=true`. If a later record fails, emit `answer.retracted` instructing clients to clear all provisional claims, persist only the controlled refusal, and emit refused `done` with `committed=true`. If authorization changes, retract provisional claims and emit terminal authorization error without assistant persistence.

Run: `cd backend && uv run pytest tests/modules/chat/test_prompting.py tests/modules/chat/test_events.py tests/api/test_chat_stream.py tests/integration/test_chat_authorization_races.py -q`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/openrag/modules/chat/runner.py backend/src/openrag/modules/chat/prompting.py \
  backend/src/openrag/modules/chat/events.py backend/src/openrag/api/routes/chats.py \
  backend/tests/modules/chat/test_prompting.py backend/tests/modules/chat/test_events.py \
  backend/tests/api/test_chat_stream.py backend/tests/integration/test_chat_authorization_races.py
git commit -m "feat: stream verified grounded claims"
```

### Task 14: Build document governance and verified-answer UX

**Files:**
- Modify: `frontend/src/api/schema.d.ts`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/features/documents/queries.ts`
- Modify: `frontend/src/features/documents/documents-page.tsx`
- Modify: `frontend/src/features/documents/document-row.tsx`
- Create: `frontend/src/features/documents/document-detail-page.tsx`
- Create: `frontend/src/features/documents/version-upload-dialog.tsx`
- Create: `frontend/src/features/documents/version-decision-dialog.tsx`
- Create: `frontend/src/features/documents/authority-readiness.tsx`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/features/chat/stream.ts`
- Modify: `frontend/src/features/chat/use-chat-stream.ts`
- Modify: `frontend/src/features/chat/streaming-message.tsx`
- Modify: `frontend/src/features/chat/source-panel.tsx`
- Modify: `frontend/src/features/chat/chat-page.tsx`
- Modify: `frontend/src/features/chat/no-answer-notice.tsx`
- Test: colocated document/component tests
- Test: colocated chat tests

**Interfaces:**
- Consumes generated governance/readiness contracts plus Task 13 progressive events and Task 12 public citation snapshots; frontend never infers authorization, lifecycle validity, or committed state.

- [ ] **Step 1: Write RED accessible UX tests**

Cover version timeline, normalized display label, current-vs-latest, pending rebuild/readiness counts, no silent empty state, upload retry, approver-only actions, Engineer absence of approval controls, direct backend `403`, obsolete confirmation, no physical delete for governed history, keyboard focus, and narrow layout.

Run: `cd frontend && corepack pnpm test -- src/features/documents src/app`

Expected: FAIL on missing UX.

- [ ] **Step 2: Regenerate API and implement governance UI**

Run `corepack pnpm generate:api` against the current backend; never hand-edit generated components. List logical documents and current/latest versions. Detail timeline is immutable newest-first. Rebuild status says legacy search remains available until strict authority is ready; only a `rag.evaluate` user can activate after the server reports all checks passing.

Run: `cd frontend && corepack pnpm test -- src/features/documents src/app && corepack pnpm lint && corepack pnpm typecheck`

Expected: PASS.

- [ ] **Step 3: Write RED protocol/UI tests**

Test runtime validation for every field; verified claims append only after complete frames; progress and heartbeat do not change answer text; retraction synchronously clears provisional content/citations and shows controlled refusal; user abort, network error, server error, verifier timeout, malformed frame, parser exception, EOF without terminal, `done` without `committed=true`, and any noncommitted terminal all clear provisional claims/sources/citations in one reducer transition; interrupted-stream tests prove no partial answer survives; exactly one terminal state; historical citations use snapshots rather than fabricated `Document <uuid>`; chips expose document/version/section/page in accessible text; no internal hash/raw HTML is rendered.

Run: `cd frontend && corepack pnpm test -- src/features/chat`

Expected: FAIL on unsupported protocol.

- [ ] **Step 4: Implement last-good progressive state**

Track provisional verified claims separately from committed history. A single `clearProvisional()` reducer path handles explicit retraction plus every abort/error/timeout/malformed/EOF/noncommitted terminal. Only `done` with runtime-validated `committed === true` may promote/invalidate history. Missing/false `committed` is an interruption, never success. Source chips display `name · version · section path · Page/Slide/Sheet ordinal`; refusal copy is mapped from allowlisted reason codes and never prints debug detail.

Run: `cd frontend && corepack pnpm test -- src/features/chat && corepack pnpm lint && corepack pnpm typecheck && corepack pnpm build`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api frontend/src/features/documents frontend/src/features/chat frontend/src/app/router.tsx
git commit -m "feat: govern documents and present verified answers"
```

### Task 15: Add evaluations, observability, end-to-end rollout, and release gates

**Files:**
- Create: `backend/src/openrag/modules/grounding/telemetry.py`
- Create: `backend/tests/fixtures/evals/document_version_citation_cases.json`
- Create: `backend/tests/evals/test_document_version_citation_eval.py`
- Create: `backend/tests/integration/test_grounded_version_e2e.py`
- Modify: `backend/tests/isolation/test_chat_isolation.py`
- Modify: `backend/tests/core/test_logging.py`
- Create: `frontend/e2e/document-governance.spec.ts`
- Modify: `README.md`

**Interfaces:**
- Produces content-free lifecycle/retrieval/verification telemetry and a versioned golden release gate.

- [ ] **Step 1: Write RED golden/telemetry/security tests**

Golden cases include current/superseded/obsolete/future/expired/unauthorized versions, exact page spans, multi-claim/multi-source, paraphrase entailment, negation, conflicting values, wrong number/date/code, prompt injection, projection lag, revocation during generation, policy/model/credential drift, retraction, interrupted EOF, and no-document refusal. Security/deployability cases include Task 4 behavior-preserving direct ingestion with no start command, Task 6 HTTP upload through Outbox/Redis/Inbox/queued/claimed/executed stages to review with no direct enqueue, collection non-merging, review leakage, storage/index provision-before-claim, paginated scanner restart/progress, calibration consumer crash/replay/DLQ, asynchronous readiness request/poll/activation, active-policy gates, readiness signature/staleness, durable stage crash/replay, SSRF/DNS rebinding/redirect, and nested credential redaction. Telemetry tests accept only allowlisted metrics.

Run: `cd backend && uv run pytest tests/evals tests/core/test_logging.py -q`

Expected: FAIL until fixtures/telemetry exist.

- [ ] **Step 2: Implement safe metrics and full lifecycle E2E**

Emit counts, revisions, score aggregates, reason/status, claim/citation coverage, projection lag, TTFT-to-first-verified-claim, validation time, and total time through explicit typed arguments. E2E runs:

```text
legacy indexed serving only from openrag_chunks -> expand -> provision authority collection/indexes
-> restart-safe legacy scanner -> outbox/Redis/Inbox -> durable rebuild into authority generation
-> lifecycle projection -> calibrate and activate grounding policy
-> readiness POST returns 202 -> worker external checks -> readiness GET reports passed
-> generation activation POST consumes the passed generation
-> strict reads only from authority alias
upload v1 -> review -> refuse -> approve/project -> grounded exact-page citation
upload v2 -> v1 remains current -> approve v2 -> v1 excluded -> v2 cited
obsolete v2 -> refusal; historical snapshots unchanged
cross-tenant same names/hashes -> zero leaked IDs/text
```

- [ ] **Step 3: Add browser and deployment documentation**

Playwright covers HSE approval, Engineer restrictions, rebuild banner/async signed-generation cutover, grounding-policy calibration/activation, provisional verified claims, later-record retraction, abort/error/malformed-EOF clearing, and exact citation chips. README documents separate legacy/authority collections, collection/index provisioning before workers, paginated scanner CLI/beat and progress, HTTP outbox transport, durable ingestion/rebuild stages, calibration/readiness stream workers and Redis retry/DLQ operation, readiness POST-202/GET-poll/activation flow, grounding-policy bootstrap/calibration, verifier SSRF allowlist, permissions/oracle semantics, strict refusal, migration preflight, fail-closed downgrade, and application rollback with expanded schema.

- [ ] **Step 4: Run the complete gate**

```bash
cd backend
uv run pytest -q
uv run ruff check src tests
uv run mypy src
uv run lint-imports
uv run alembic upgrade head
uv run alembic current --check-heads
uv run alembic check

cd ../frontend
corepack pnpm test
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm build
corepack pnpm e2e
```

Run Compose smoke for health/login, Task 4 behavior-preserved direct ingestion/no start event, Task 6 HTTP upload through outbox/stream/Inbox/queued/claimed/executed stages to review with direct enqueue disabled, legacy-only serving, authority storage provision-before-claim, scanner restart/progress, crash/replayed rebuild, lifecycle projection, replayed calibration and active policy, readiness POST/worker/GET/activation, no dual-collection merge, approval, exact citations, successor exclusion, interrupted/retracted/refused streaming, cross-workspace denial, every consumer retry/DLQ, SSRF rejection, and logout. Scan logs/Redis/DLQ/SSE for secrets, document text, prompts, tokens, tracebacks, and cross-tenant identifiers.

- [ ] **Step 5: Run acceptance scans and commit**

```bash
rg -n "Document [${][^}]+|filename=.*document_id" frontend/src backend/src
rg -n "MatchAny" backend/src/openrag/modules/retrieval
rg -n "AsyncSession" backend/src/openrag/modules/chat/runner.py
rg -n "api_key|token|prompt|document_text|content_hash" \
  backend/src/openrag/modules/grounding/telemetry.py \
  backend/src/openrag/modules/documents/events.py
git status --short
```

Expected: no fabricated filename, no unbounded authority filter, no session parameter in runner, no content/secrets in telemetry/events, and benchmark directories remain untracked.

```bash
git add backend/src/openrag/modules/grounding/telemetry.py backend/tests/fixtures/evals \
  backend/tests/evals backend/tests/integration/test_grounded_version_e2e.py \
  backend/tests/isolation/test_chat_isolation.py backend/tests/core/test_logging.py \
  frontend/e2e/document-governance.spec.ts README.md
git commit -m "test: verify authoritative grounded rag rollout"
```

## Review protocol

Each of the 15 tasks is an independent commit/reviewer gate. The implementer writes `.superpowers/sdd/document-authority-task-N-report.md` with RED/GREEN commands, schema/lock/security decisions, and limitations. A fresh reviewer checks the exact task interfaces, tenant/state races, and migration/event behavior. Fix findings before the next task. Do not push an unapproved task.

## Acceptance mapping

- Composite tenant/same-version/actor integrity and immutability: Tasks 1–2 and 12.
- Safe schema/backfill/downgrade/event preflight: Task 2.
- Exact oracle/capability/lock semantics: Tasks 3–4.
- Page-local section/page evidence and normalized versions: Tasks 1, 5, and 8.
- Behavior-preserving generic outbox/typed Redis provisioning and governance APIs: Task 4.
- Authority storage ordering, restart-safe legacy discovery, start consumption, atomic direct-to-durable cutover, and runnable stage acknowledgement: Task 6.
- No-silent-outage separate-collection rebuild/readiness/cutover: Tasks 2, 4, 6–8, 10–11, 14, and 15.
- Replay-safe current-eligibility projection: Task 7.
- Projected eligibility, explicit legacy/authority DB branch, bounded lag fallback, DB/hash revalidation, and real scores: Task 8.
- SSRF-safe call-scoped LiteLLM dependency/adapter boundary: Task 9.
- Complete grounding-policy administration, model/credential validation, replay-safe calibration, and semantic entailment/conflict: Task 10.
- Async readiness request/worker/poll/activation with active-policy gate: Task 11.
- Immutable citation/message state and exact policy/model binding revalidation: Tasks 2 and 12.
- Session-free verified-claim streaming, heartbeat, committed terminal, and retraction behavior: Task 13.
- Complete governance and citation UX including interruption clearing: Task 14.
- Evaluation, observability, isolation, browser, migration, Compose, and quality gates: Task 15.

## Line-level self-review record

- Task 1 names `document_versions UNIQUE(org_id,id)`, every parent unique/composite FK, and tenant actor FKs; same-version, citation-tenant, and cross-org actor RED tests are explicit.
- Task 2 is a coordinated migration/runtime compatibility boundary, preserves
  `down_revision="6c4a2f8b9d10"`, tests a single Alembic head, copies complete
  rebuild source identity into exact sequence-1 `Legacy 1` versions without
fabricating page counts while assigning the declared bounded legacy profile
sentinels, and deploys scope-aware atomic
  legacy chat writes before traffic resumes. Its trigger matrix isolates exact
  disabled-workspace `legacy_unverified` display rows from authority citations,
  excludes them from grounding/retrieval/memory, rejects new legacy rows after
  activation, and retains bounded JSONB checks, immutable snapshots,
  direct-last-authority-citation delete failure, parent cascade allowance, and
  event-aware downgrade refusal.
- Task 4 installs and tests generic dispatcher/envelope/typed-stream infrastructure plus governance APIs while preserving direct ingestion; its regression proves one direct job and zero ingestion/rebuild commands, command-stream entries, or queued stages.
- Tasks 5–7 separate pure provenance, pre-provisioned durable replayable stage work, and event projection. Task 6 adds the start consumer and runnable executor before atomically changing create/retry to emit commands and removing direct enqueue; its integration proves HTTP → Outbox → Redis → Inbox → queued/claimed/executed stages → review. It also gates authority-upsert claims on exact storage/index readiness and supplies restart-safe paginated legacy scanning through CLI and beat.
- Task 7 only projects lifecycle state into the already provisioned physical collection; it neither creates schema nor performs cutover.
- Task 8 branches on the DB flag, limits lag fallback to 64 versions/30 seconds, recomputes returned-text hashes, creates page-local results, and records actual dense/sparse/RRF scores.
- Task 9 includes dependency/lock changes, a sole adapter import boundary, DNS-pinned no-redirect SSRF defense, call-scoped secret resolution, and recursive redaction tests while explicitly deferring completion-proxy removal.
- Task 10 supplies complete policy APIs, an explicit replay-safe calibration worker/consumer with Inbox/retry/DLQ/crash coverage, demo/bootstrap guard, capability/credential validation, and fail-closed semantic entailment; lexical overlap is not presented as support.
- Task 11 implements readiness as POST-202/outbox/worker/GET-poll/consume-on-activation and requires the same active unexpired passed policy snapshot at request, evaluation, and activation.
- Tasks 12–13 reauthorize exact active policy/model/binding/credential state at final persistence, never capture request sessions, never emit raw provider deltas, and define committed/retraction terminals.
- Task 14 clears provisional state on every abort/error/malformed EOF/timeout/noncommitted terminal; only `done.committed=true` promotes history.
- Tasks 3 and 13 specify deterministic lock/session boundaries and enumerate required races.
- Placeholder scan: no task uses a deferred implementation marker; representative tests contain concrete assertions and exact expected failures.
- Task count remains 15, with every intermediate commit deployable: Task 4 preserves working direct ingestion, Task 5 lands page-local persistence, Task 6 installs the complete runnable durable path before atomically cutting over, storage precedes authority upserts, policy precedes readiness, and database invariants precede citation services.
