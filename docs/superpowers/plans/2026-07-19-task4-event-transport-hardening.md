# Task 4 Event Transport Hardening Plan

> **Status:** Normative addendum to Task 4 of
> `2026-07-19-document-version-citation-foundation.md`. Implementation MUST NOT
> start until this addendum receives an independent APPROVED review.

**Goal:** Install a typed, bounded, durable event transport without changing the
working Legacy-1 ingestion path, exposing raw processing errors, or allowing an
older deployment to destroy events produced by a newer deployment.

**Architecture:** Task 4A freezes the safe document API and durable database
contracts. Task 4B adds a dedicated authenticated Redis Streams transport,
conditional outbox relay, and reusable Inbox consumer helper. Each slice is a
separate deployable commit with RED/GREEN evidence and independent review.

## Non-negotiable security and compatibility rules

- The registry is closed. Every envelope and payload is frozen Pydantic v2 with
  `extra="forbid"`; canonical serialization of the complete schema envelope is
  at most 16 KiB. Redis carries exactly two fields: `envelope_bytes` and
  `envelope_digest`, where the digest is lowercase SHA-256 of `envelope_bytes`
  and is not itself part of those bytes.
- Envelopes carry only IDs, schema version, aggregate type/ID, organization ID,
  workspace ID, lifecycle revision, trace/correlation ID, and safe reason codes.
  They never carry document bytes/text, object paths, URLs, prompts, hashes,
  credentials, exception bodies, stack traces, or model reasoning.
- Redis is untrusted transport. A consumer re-authorizes tenant scope and
  revalidates database state/revision before every business effect. It also
  proves the received envelope exactly matches its authoritative PostgreSQL
  Outbox record; possession of event-Redis write access never authorizes work.
- Outbox insertion remains in the producer's business transaction. Inbox insert
  and the consumer's database effect commit in one transaction. `XACK` happens
  only after that commit and outside SQL.
- All claims, publish marks, retries, dead letters, and lease releases use a
  conditional update matching both row ID and the unique lease token. Lease loss
  is a benign no-op, never an overwrite.
- Task 4 emits and consumes no ingestion/rebuild start command. Legacy-1 upload
  and retry continue to call the existing direct worker exactly once. Task 6 is
  the atomic cutover.
- Event transport is separate from the immutable security audit trail.

## Task 4A: Freeze database, envelope, and document API contracts

### Files

- Create: `backend/migrations/versions/<revision>_harden_transactional_outbox.py`
- Modify: `backend/src/openrag/modules/events/models.py`
- Create: `backend/src/openrag/modules/events/envelopes.py`
- Create: `backend/src/openrag/modules/events/outbox.py`
- Modify: `backend/src/openrag/modules/documents/schemas.py`
- Modify: `backend/src/openrag/api/routes/documents.py`
- Modify: `backend/src/openrag/modules/documents/service.py`
- Modify: `backend/src/openrag/modules/documents/ingest.py`
- Create: `backend/tests/modules/events/test_envelopes.py`
- Create: `backend/tests/modules/events/test_outbox.py`
- Modify: `backend/tests/modules/documents/test_service.py`
- Modify: `backend/tests/modules/documents/test_ingest.py`
- Create: `backend/tests/architecture/test_import_contracts.py`
- Create: `backend/tests/api/test_document_versions.py`
- Modify: `backend/tests/api/test_documents_routes.py`
- Modify: `backend/tests/test_migrations.py`

### Exact public API contract

- `GET /workspaces/{workspace_id}/documents` returns the current compatibility
  list ordered by `created_at DESC, id DESC`. It remains unpaginated only until
  the frontend and API move atomically to a cursor contract in a later task.
- `POST /workspaces/{workspace_id}/documents` returns `201` and accepts only the
  existing Legacy-1 upload contract. It never accepts a client sequence.
- `GET /documents/{document_id}` and `PATCH /documents/{document_id}` return
  `200`.
- `GET /documents/{document_id}/versions` returns all versions ordered by
  `sequence DESC`; `GET /document-versions/{version_id}` returns `200`.
- `POST /document-versions/{version_id}/approve`, `/reject`, and `/obsolete`
  return `200`. Existing `POST /document-versions/{version_id}/retry` returns
  `202` only for Legacy-1 until Task 6.
- Foreign-organization, foreign-workspace, and nonmember object IDs return
  `404`. An accessible object without the required capability returns `403`.
  Illegal lifecycle transitions return `409`. Extra fields, immutable fields,
  invalid bounds, and any client sequence return `422`.
- `DocumentPatch` permits only bounded logical `name`, `department`,
  `document_type`, and `external_identifier`; it forbids extras.
- Responses expose safe display metadata, sequence/label/state, dates,
  provenance readiness, page count, lifecycle revision, and a safe error code.
  They never expose object keys, storage URLs, hashes, internal actor IDs,
  projection internals, raw parser/provider errors, or credentials.

### RED tests

1. Assert every route/method/status, deterministic ordering, response schema,
   capability, object-oracle, transition, and validation rule above.
2. Assert strict envelope parsing, UTC timestamps, deterministic canonical
   bytes, complete-envelope 16 KiB enforcement, registered schema routing, and
   rejection of text/path/URL/hash/credential/error fields.
   Assert the registered producer factory applies these checks before
   `session.add`: a prohibited or oversized payload creates no Outbox row even
   while the dispatcher is stopped. An architecture test rejects direct
   `OutboxEvent(...)` construction outside `modules/events/outbox.py`.
3. Seed a pre-migration database with:
   - negative attempts;
   - overlong event, aggregate, dedupe, lease, and error values;
   - oversized/malformed payloads;
   - contradictory terminal states;
   - active legacy leases;
   - raw exception/error messages.
4. Assert the migration either performs deterministic content-free
   normalization or stops before DDL with exactly
   `OPENRAG_OUTBOX_PREFLIGHT_FAILED`. Valid rows survive unchanged. Raw
   `last_error` becomes only `legacy_dispatch_failure`; legacy leases are
   cleared. Downgrade restores the old schema without inventing sensitive text.

Run:

```bash
cd backend
uv run pytest tests/api/test_documents_routes.py \
  tests/api/test_document_versions.py \
  tests/modules/events/test_envelopes.py \
  tests/test_migrations.py -q
```

Expected RED: new routes, strict envelopes, and migration do not exist.

### GREEN implementation

- Add bounded columns/constraints for event/aggregate/dedupe/lease identifiers,
  `attempts >= 0`, `dispatch_after`, `dead_lettered_at`, safe `last_error_code`,
  a canonical `envelope_digest`, optional published stream/message identifiers,
  and a partial claim index for unpublished, non-dead-lettered rows.
- A dead-lettered row is never marked published. Known permanently malformed
  registered events use only `contract_invalid` in the operational DLQ.
- Unknown or newer schemas are **not** malformed: clear their lease with a
  conditional row+lease update, set `schema_not_registered`, and defer them with
  bounded exponential backoff. A mixed-version test proves an old dispatcher
  cannot DLQ an event that the next version understands.
- Register only the Task 3 lifecycle event. Provisioning future stream names is
  allowed in 4B, but ingestion/rebuild schemas remain unroutable until Task 6.
- `events.outbox.add_registered_event()` is the only producer boundary. It
  accepts a registered typed payload rather than a dictionary, builds the full
  schema envelope, canonicalizes it, enforces field policy and the 16 KiB
  envelope-bytes limit, stores `sha256(envelope_bytes)`, and only then adds the Outbox row to the
  caller's existing business transaction. Convert every direct producer in
  document service/ingestion to this factory. The architecture test prevents
  later direct ORM construction.
- Replace the current public raw `DocumentOut.error` with a bounded safe code.
  Do not persist new raw processing exceptions in document-facing fields.

### Verification and commit

```bash
cd backend
uv run pytest tests/api/test_documents_routes.py \
  tests/api/test_document_versions.py \
  tests/modules/events/test_envelopes.py \
  tests/modules/events/test_outbox.py \
  tests/modules/documents/test_service.py \
  tests/modules/documents/test_ingest.py \
  tests/architecture/test_import_contracts.py \
  tests/test_migrations.py -q
uv run ruff check src tests
uv run mypy src/openrag
uv run lint-imports
uv run alembic upgrade head
uv run alembic current --check-heads
uv run alembic check
```

Commit only the reviewed 4A files as:

```text
feat: freeze safe event and document api contracts
```

## Task 4B: Install durable transport and atomic consumer helper

### Files

- Create: `backend/src/openrag/modules/events/dispatcher.py`
- Create: `backend/src/openrag/modules/events/streams.py`
- Create: `backend/src/openrag/modules/events/consumer.py`
- Create: `backend/src/openrag/modules/events/readiness.py`
- Modify: `backend/src/openrag/worker/tasks.py`
- Modify: `backend/src/openrag/worker/celery_app.py`
- Modify: `backend/src/openrag/core/config.py`
- Modify: `deploy/compose.yaml`
- Create: `backend/tests/modules/events/test_dispatcher.py`
- Create: `backend/tests/modules/events/test_consumer.py`
- Create: `backend/tests/integration/test_event_streams.py`
- Create: `backend/tests/integration/test_upload_behavior_preserved.py`
- Modify: `backend/tests/core/test_config.py`
- Modify: `backend/tests/test_compose.py`
- Modify: `backend/tests/worker/test_celery.py`

### Pinned topology and durability contract

- Add a separate `event-redis` service pinned to Redis `>=7.2,<8` by exact image
  tag/digest. Enable `appendonly yes`, `appendfsync always`, persistent storage,
  authentication/ACLs, a private Compose network, memory limits, and
  `no-new-privileges`. It is never the Celery broker or application rate-limit
  cache. The API and legacy ingestion worker do not depend on it.
- Add a distinct required `event_redis_url`. Do not derive it from the broker
  URL. Credentials are injected, never source-defaulted or logged.
- Run a singleton-safe `event-scheduler` that enqueues bounded relay ticks to a
  dedicated `events` Celery queue, and an `event-worker` that claims, publishes,
  provisions, and consumes only registered event work. Multiple accidental
  schedulers remain safe because DB leases/dedupe are authoritative.
- The event component provisions the command and lifecycle streams/groups
  idempotently. It checks PostgreSQL, authenticated event Redis, exact stream
  names, and exact group names in its own readiness probe. Event Redis failure
  makes only event components unready; API readiness, broker readiness, and the
  Legacy-1 worker remain healthy.
- Clean `SIGTERM` stops new claims, waits a bounded grace period for current
  work, and leaves unconfirmed leases/messages reclaimable.
- `XADD` uses one connection and happens outside SQL. Before an Outbox row is
  marked published, execute `WAITAOF 1 0 <bounded-ms>` on that same connection
  and require one local acknowledgment. Redis documents `WAITAOF` from 7.2 and
  define it as waiting for preceding writes on the connection to reach AOF.
  Timeout, disconnect, or a zero acknowledgment leaves the Outbox unpublished.
- Never trim a stream while its consumer groups have pending messages. Retention
  requires a later acknowledged-watermark policy.

### RED dispatcher and crash tests

1. Prove disjoint `FOR UPDATE SKIP LOCKED` claims of at most 100 rows using
   database time and a unique per-batch lease token.
2. Prove claim commit precedes validation/Redis, `XADD` precedes `WAITAOF`, and
   publish marking occurs only afterward in a second short transaction.
3. Cover Redis down, `XADD` failure, `WAITAOF` timeout/zero/disconnect, partial
   batch success, crash before XADD, crash after XADD before mark, duplicate
   publish, lease expiry, and lease theft. No stale owner can mark/retry/DLQ.
4. Scan logs, Redis values, database errors, and DLQs for sentinel document text,
   prompts, storage paths, URLs, hashes, credentials, and raw exceptions.
5. Prove concurrent stream/group provisioning tolerates `BUSYGROUP` but rejects
   a wrong stream/group topology.

### Generic consumer helper and RED tests

`consume_one()` performs this exact sequence:

1. Before schema-specific parsing, decode a stable bounded base envelope outside
   SQL. Reject invalid UTF-8, duplicate JSON keys, noncanonical bytes, extra base
   fields, and envelope bytes over 16 KiB. Redis entries have exactly
   `envelope_bytes` and `envelope_digest`; the stable base contract decoded from
   the bytes exposes only event ID,
   schema/type/version, aggregate type/ID, organization/workspace, revision,
   correlation ID, and payload. The digest is never inside `envelope_bytes`.
2. Open a short transaction and load the authoritative Outbox row by `event_id`.
   Compute SHA-256 over the received bytes and constant-time compare it with the
   separate transport digest and PostgreSQL `Outbox.envelope_digest`. Reconstruct
   the authoritative canonical envelope and require exact bytes plus base
   schema/type, aggregate, organization/workspace, revision, payload, and
   stream-route match. Missing or mismatched authority produces only
   `event_not_authoritative` and no Inbox or domain effect.
3. Only an Outbox-attested unknown/newer schema is upgrade-deferred: record a
   bounded `schema_not_registered` counter keyed only by bounded schema name
   (stream entry ID may be a structured log field, never a metric label), then
   perform no Inbox insert, business effect, XACK, or DLQ write. Leave it pending
   in the same group, exempt from poison limits and trimming. After upgrade, a
   supporting consumer uses `XAUTOCLAIM` after the idle interval and resumes the
   normal path. An unattested unknown/future entry follows the bounded
   `event_not_authoritative` retry/DLQ/XACK path, so forged entries cannot retain
   unbounded PEL state. Known malformed registered poison reaches a bounded
   redacted DLQ only after its delivery limit.
4. For an attested registered schema, lock/revalidate organization, workspace,
   aggregate, lifecycle revision, and intended state from PostgreSQL.
5. Insert `InboxEvent(consumer,event_id)` and apply the database business effect
   in that same transaction. A unique Inbox conflict means the logical effect
   already committed and is a safe duplicate.
6. Commit and close SQL.
7. `XACK` outside SQL. ACK failure causes redelivery; the Inbox uniqueness makes
   it harmless.

Tests cover first delivery, concurrent duplicate delivery, crash before commit,
business-effect failure, commit failure, crash after commit before ACK, ACK
failure, pending reclaim, stale revision, tenant mismatch, unauthorized state,
and redacted poison handling. They prove there is no XACK before commit and no
Redis/object/provider call while SQL is open.

Trust-boundary tests inject (a) a fully valid-looking event with real current
IDs but no Outbox row, (b) an event reusing a real `event_id` with exactly one of
tenant, aggregate, revision, schema/type, payload, or digest altered, and (c) an
exact Outbox-backed event. Only (c) may insert Inbox/apply an effect. Permanent
missing/mismatch deliveries use only a bounded content-free
`event_not_authoritative` rejection/DLQ record after the delivery limit, then
XACK outside SQL; no received payload or differing value is persisted/logged.

A mixed-version integration test publishes a schema understood only by the new
consumer. The old consumer performs no Inbox insert, effect, XACK, or DLQ; the
entry remains in the pending entries list. After the old consumer stops and the
idle interval elapses, the new consumer claims it with `XAUTOCLAIM`, commits one
logical effect, and XACKs it. Repeating reclaim/delivery produces no second
effect. A companion flood test sends many bounded but unattested unknown-schema
entries; all reach the bounded `event_not_authoritative` terminal path and none
remain indefinitely pending. Duplicate-key and noncanonical JSON fixtures fail
before schema-specific parsing. Tampering either `envelope_bytes` or the separate
digest field fails constant-time attestation and applies no effect.

### Behavior-preservation and failure-isolation tests

- HTTP upload and retry on exact Legacy-1 invoke the current direct ingestion
  worker once per attempt and reach the expected terminal state.
- They create zero ingestion/rebuild Outbox rows, zero command-stream entries,
  and zero queued `IngestStageAttempt` rows. Nonlegacy upload/retry is unavailable.
- Stopping `event-redis` makes only event readiness fail. Login, API readiness,
  Celery broker, and a Legacy-1 upload/retry smoke remain green.

Run:

```bash
cd backend
uv run pytest tests/modules/events/test_dispatcher.py \
  tests/modules/events/test_consumer.py \
  tests/integration/test_event_streams.py \
  tests/integration/test_upload_behavior_preserved.py \
  tests/core/test_config.py tests/test_compose.py tests/worker/test_celery.py -q
uv run ruff check src tests
uv run mypy src/openrag
uv run lint-imports
uv run alembic current --check-heads
uv run alembic check
cd ..
docker compose -f deploy/compose.yaml config --quiet
```

Expected GREEN: focused tests and static/migration/Compose gates pass, while no
ingestion behavior has changed.

Commit only the reviewed 4B files as:

```text
feat: install behavior preserving event transport
```

## Security work that must not be hidden inside Task 4

These independently testable production blockers are mandatory follow-on slices:

1. **Before any public deployment:** split local development Compose from a
   fail-closed production Compose; require injected secrets/bootstrap identity;
   reject known defaults; require secure cookies/proxy policy; protect/disable
   API docs; add security headers and trusted-host configuration.
2. **Immediate auth hardening:** serialize refresh rotation with row/family locks,
   revoke a family atomically on reuse, prove a two-session race, and move JWT
   private signing material to external KMS/secret custody with key IDs/rotation.
3. **Before expanded ingestion/OCR:** stream uploads into quarantine with byte
   counting; validate extension, MIME, and magic; bound archive expansion,
   pages, pixels, CPU, memory, and wall time; add AV/CDR policy; parse/OCR in a
   rootless read-only no-egress worker with narrowly scoped storage credentials
   and no KEK, model-admin, or broad database credentials.
4. **Before authority cutover:** identify vector points by version/evidence span,
   load authoritative evidence after retrieval, and revalidate hash, ACL, state,
   effective dates, and exact approved version before prompts/citations.
5. **Production operations:** enforce storage/backup encryption with readiness
   evidence, and add redacted chat/search/document-read audit events plus
   centralized export/retention. Event logs do not replace security audit.

## Review checklist

- [ ] All six Task 4 preflight gaps have executable tests and unambiguous outcomes.
- [ ] Security event-trust requirements are explicit and content-free.
- [ ] Task 4A and 4B are independently deployable and reviewed.
- [ ] Task 4 does not create a second ingestion path.
- [ ] The reference implementations remain read-only and no RAGHub naming ships.
