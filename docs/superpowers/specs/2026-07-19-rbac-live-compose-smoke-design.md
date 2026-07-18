# Live Compose RBAC smoke design

## Purpose

Add a reproducible, authoritative live API smoke for the running OpenRAG Compose
stack. Keep `frontend/e2e/rbac.spec.ts` as a deterministic mocked-API frontend
authorization test and describe that boundary truthfully in the README.

## Interface

The smoke is a Python module under `backend/scripts/`, invoked from `backend/`
with `uv run python scripts/rbac_compose_smoke.py`. Its API base URL is either an
explicit environment value or the loopback default `http://127.0.0.1:8000`; a
non-loopback value is rejected unless explicitly supplied.

The script reads existing bootstrap credentials from the bootstrap container by
running `docker inspect` with an argument list and `shell=False`. It never prints
or places credential/token values in command arguments, exceptions, assertion
diffs, logs, process listings, or its report. HTTP failures expose only a stable
step name and status code.

## Data flow and fixture lifecycle

1. Verify `/healthz` and `/readyz`.
2. Authenticate the existing platform bootstrap account and verify the role
   catalog excludes `platform.superadmin`.
3. In one organization-scoped database transaction, create only a random
   collision-resistant Engineer user and its binding. Do not mutate existing
   users, roles, or workspaces.
4. Through the real API, create two random workspaces and add the Engineer to
   exactly one.
5. Authenticate the Engineer, verify only the assigned workspace is visible,
   and verify role, user, and workspace administration each return exactly 403.
6. Verify logout returns 204 and refresh after logout returns 401 using the
   `httpx` cookie jar.
7. In `finally`, remove only fixture-owned refresh tokens, memberships,
   bindings, workspaces, and the fixture user. Workspace cleanup uses both
   returned IDs and precomputed collision-resistant names so a lost or malformed
   create response cannot strand a fixture. Cleanup is organization scoped,
   transactional, and works after partial setup.

## Failure and security behavior

The script fails closed on missing container variables, malformed inspection
data, unexpected API shapes, missing system Engineer role, or any unexpected
status. Output contains fixed labels and status codes only. HTTP timeouts are
bounded. Bearer tokens are sent only in authorization headers and are never
serialized to output.

## Testing

Unit tests inject process, HTTP, and database collaborators to prove credential
extraction, loopback/default URL rules, exact smoke sequencing, cleanup after a
partial failure, organization-scoped fixture ownership, and redacted failures.
The test must fail before the script exists. After unit/static checks pass, run
the script against the live Compose stack and perform a secret-safe output/log
scan.

## Documentation

README must explicitly state that `rbac.spec.ts` mocks API responses and proves
frontend navigation/presentation only. It must invoke the new Python smoke as
the authoritative live health, authentication, catalog, denial, isolation, and
logout check.
