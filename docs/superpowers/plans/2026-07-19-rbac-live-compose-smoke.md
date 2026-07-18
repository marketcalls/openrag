# Live Compose RBAC Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reproducible, credential-safe live Compose API smoke and make the README distinguish it from mocked browser RBAC tests.

**Architecture:** A standalone async Python script owns orchestration while small injected process, HTTP, and database collaborators make security behavior unit-testable. A SQLAlchemy fixture store atomically creates a random Engineer, binding, two workspaces, and one membership, then returns immutable exact ownership evidence. Cleanup validates and deletes only exact fixture IDs; all public failures and progress output use fixed labels and status codes only.

**Tech Stack:** Python 3.12, httpx, SQLAlchemy asyncio, pytest, Docker Compose.

## Global Constraints

- Never output credentials, bearer/refresh tokens, response bodies, or database URLs.
- Use subprocess argument lists with `shell=False`; never put secrets in process arguments.
- Use collision-resistant random fixture identifiers and organization-scoped transactional cleanup in `finally`.
- Do not mutate existing users, roles, or workspaces.
- Default to `http://127.0.0.1:8000`; explicit remote URLs must use HTTPS.
- Keep `frontend/e2e/rbac.spec.ts` mocked and label it truthfully.

---

### Task 1: Credential-safe smoke orchestration and fixtures

**Files:**
- Create: `backend/scripts/rbac_compose_smoke.py`
- Create: `backend/tests/scripts/test_rbac_compose_smoke.py`

**Interfaces:**
- Consumes: `Settings`, `build_engine`, `build_session_factory`, current auth/tenancy ORM models, and the live `/api/v1` contracts.
- Produces: `SmokeFailure`, `BootstrapCredentials`, `SmokeFixture`, `resolve_api_base_url`, `load_bootstrap_credentials`, `SqlAlchemyFixtureStore`, `run_smoke`, and `main`.

- [x] **Step 1: Write failing unit contracts**

Create tests that import the planned module, then assert:

```python
def test_default_api_url_is_loopback() -> None:
    assert smoke.resolve_api_base_url({}) == "http://127.0.0.1:8000"

def test_remote_explicit_api_url_requires_https() -> None:
    with pytest.raises(smoke.SmokeFailure, match="secure HTTPS"):
        smoke.resolve_api_base_url({"OPENRAG_SMOKE_API_URL": "http://example.com"})
```

Add an extraction test whose fake `docker inspect` output contains sentinel
credentials and whose error string/output must not contain either sentinel. Add
async orchestration tests with `httpx.MockTransport` and a fake fixture store:
one proves exact successful calls and cleanup; one returns 500 on the second
workspace creation and proves the first workspace plus fixture are cleaned and
neither sentinel password nor token appears in the exception or captured output.

- [x] **Step 2: Run the new test file and verify RED**

Run: `uv run pytest tests/scripts/test_rbac_compose_smoke.py -q`

Expected: collection fails because `scripts.rbac_compose_smoke` does not exist.

- [x] **Step 3: Implement minimal secure smoke**

Implement fixed-label failures:

```python
class SmokeFailure(RuntimeError):
    pass

def require_status(step: str, response: httpx.Response, expected: int) -> None:
    if response.status_code != expected:
        raise SmokeFailure(f"{step} failed with HTTP {response.status_code}")
```

Parse `docker inspect` JSON through an injected `Callable[[Sequence[str]], str]`
that calls `subprocess.run(list(argv), check=True, capture_output=True,
text=True, shell=False)`. Extract only `OPENRAG_BOOTSTRAP_EMAIL` and
`OPENRAG_BOOTSTRAP_PASSWORD`, wrapping all parse/process errors in fixed text.

Implement `SqlAlchemyFixtureStore.provision` with ORM predicates scoped to the
bootstrap user's organization and role key `engineer`. Create only one random
user and one binding in a transaction. Precompute collision-resistant workspace
names before any API request. Implement `cleanup` with predicates for the
fixture organization plus returned workspace IDs or those exact precomputed
names, deleting refresh tokens through the fixture user ID, memberships,
bindings, created workspaces, and the fixture user in dependency order, all in
one transaction. This covers a server-side create whose response is lost or
malformed without touching a same-name workspace in another organization.

Implement `run_smoke` with two cookie-preserving `httpx.AsyncClient` instances,
Bearer headers, bounded timeouts, exact health/readiness/login/catalog/
workspace/403/logout assertions, and cleanup in `finally`. Print only stable
PASS labels. `main` builds the engine/store, runs the smoke, disposes the engine,
and exits nonzero with only `SmokeFailure` text on failure.

- [x] **Step 4: Run focused GREEN and static checks**

Run:

```bash
uv run pytest tests/scripts/test_rbac_compose_smoke.py -q
uv run ruff check scripts/rbac_compose_smoke.py tests/scripts/test_rbac_compose_smoke.py
uv run mypy scripts/rbac_compose_smoke.py
```

Expected: all tests and both static checks pass.

---

### Task 2: Truthful README and live-stack verification

**Files:**
- Modify: `README.md`
- Modify: `.superpowers/sdd/task-6-report.md` (ignored operational report)

**Interfaces:**
- Consumes: `uv run python scripts/rbac_compose_smoke.py` from Task 1.
- Produces: reproducible operator instructions and reviewed live evidence.

- [x] **Step 1: Correct the documentation boundary**

State explicitly that `frontend/e2e/rbac.spec.ts` intercepts `/api` and verifies
frontend navigation/presentation only. Replace the claimed E2E live RBAC command
with:

```bash
cd backend
uv run python scripts/rbac_compose_smoke.py
```

Document optional `OPENRAG_SMOKE_API_URL=https://...`, the default loopback URL,
the required running Compose stack, automatic bounded fixture cleanup, and the
fact that credentials/tokens are never printed. Keep the separate model-backed
RAG browser journey documented as opt-in.

- [x] **Step 2: Run live and regression verification**

Run:

```bash
cd backend
uv run pytest tests/scripts/test_rbac_compose_smoke.py tests/isolation/test_rbac_isolation.py -q
uv run ruff check scripts/rbac_compose_smoke.py tests/scripts/test_rbac_compose_smoke.py
uv run mypy scripts/rbac_compose_smoke.py
uv run python scripts/rbac_compose_smoke.py
cd ../frontend
corepack pnpm e2e
```

Expected: unit/isolation/static checks pass; the live smoke reports only fixed
PASS labels; five mocked RBAC browser cases pass with no RBAC skips.

- [x] **Step 3: Secret-safe review and commit**

Confirm `git diff --check`, scan the smoke output/source for credential/token
values without printing matches, update the Task 6 report with RED/GREEN/live
evidence, and commit only the smoke, tests, README, plan, and report-eligible
tracked files in a new commit. Do not amend `e14836a` and do not push.

---

### Task 3: Replace inferred workspace ownership with exact fixture ownership

**Files:**
- Modify: `backend/scripts/rbac_compose_smoke.py`
- Modify: `backend/tests/scripts/test_rbac_compose_smoke.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-19-rbac-live-compose-smoke-design.md`
- Modify: `.superpowers/sdd/task-6-report.md` (ignored operational report)

**Interfaces:**
- Consumes: existing `SmokeFailure`, credential loader, live auth/catalog/list/
  denial/logout contracts, and current tenancy ORM models.
- Produces: `SmokeFixture` with `allowed_workspace_id`,
  `allowed_workspace_name`, `peer_workspace_id`, and `peer_workspace_name`;
  `SqlAlchemyFixtureStore.provision` creates the complete fixture atomically;
  `cleanup(fixture)` validates and deletes only exact owned rows.

- [x] **Step 1: Add exact-ownership RED contracts**

Extend the PostgreSQL tests so provision returns both workspaces and exactly one
allowed membership. Add a same-organization workspace with the same name as a
fixture workspace and prove it survives cleanup. Add an arbitrary same-org
workspace, replace one fixture ID with that ID, and assert cleanup raises
`SmokeFailure("fixture ownership validation failed")` without deleting either
the arbitrary row or any valid fixture row. Change the fake store to return:

```python
smoke.SmokeFixture(
    org_id=ORG_ID,
    user_id=USER_ID,
    email=engineer_email,
    allowed_workspace_id=ALLOWED_WORKSPACE,
    allowed_workspace_name="RBAC smoke allowed fixture",
    peer_workspace_id=PEER_WORKSPACE,
    peer_workspace_name="RBAC smoke peer fixture",
)
```

Change orchestration assertions so no privileged workspace creation/member API
request occurs, the Engineer list contains only `allowed_workspace_id`, and a
partial HTTP failure after provision still calls `cleanup(fixture)`.

- [x] **Step 2: Run focused RED**

Run:

```bash
uv run pytest \
  tests/scripts/test_rbac_compose_smoke.py::test_sqlalchemy_store_cleanup_preserves_same_org_same_name_workspace \
  tests/scripts/test_rbac_compose_smoke.py::test_sqlalchemy_store_cleanup_rejects_arbitrary_workspace_id \
  -q
```

Expected: fail because `SmokeFixture` lacks exact workspace fields and cleanup
still accepts inferred names/IDs from the caller.

- [x] **Step 3: Implement atomic exact fixture lifecycle**

Within `provision`, create both workspaces and the allowed `WorkspaceMember`
before commit. Store exact IDs and names in the immutable fixture. Implement
cleanup as one transaction that locks and validates the exact fixture user plus
both exact `(org_id, id, name)` workspace rows before any delete. On any mismatch
raise the fixed ownership error so the transaction rolls back unchanged. Delete
refresh tokens, membership, binding, exact workspace IDs, and fixture user in
dependency order. Remove every name-only/OR cleanup predicate.

Remove platform workspace creation and membership API calls from `run_smoke`.
Use only `fixture.allowed_workspace_id` for the live Engineer visibility check;
retain the Engineer workspace POST 403 check.

- [x] **Step 4: Verify GREEN and update handoff**

Run:

```bash
uv run pytest tests/scripts/test_rbac_compose_smoke.py \
  tests/isolation/test_rbac_isolation.py -q
uv run ruff check src tests scripts
uv run mypy src scripts/rbac_compose_smoke.py
uv run lint-imports
uv run python scripts/rbac_compose_smoke.py
cd ../frontend && corepack pnpm e2e
```

Update README to say all temporary fixture rows are created transactionally in
PostgreSQL and cleanup uses exact immutable IDs/names only. Update the report
with RED/GREEN/live evidence, perform the existing secret-safe scan and zero-row
post-cleanup query, then commit as a new fix without amending or pushing.
