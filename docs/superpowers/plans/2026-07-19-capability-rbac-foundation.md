# Capability RBAC Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove role-string privilege escalation and deliver organization-owned custom roles, permission bindings, safe invitations, object-level workspace authorization, and a usable full-stack role administration flow.

**Architecture:** Platform superadmin is a non-assignable boolean identity created only by bootstrap. Organization authorization is resolved from versioned role definitions, permission rows, and user bindings; workspace visibility remains an independent object-access decision based on explicit membership or the `workspace.read_all` capability. Frontend permission checks are UX hints while every backend route and service remains authoritative and deny by default.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, PostgreSQL, Alembic, Pydantic 2, PyJWT, pytest/Testcontainers, React 18, TypeScript 5.6, TanStack Query, React Router, Vitest, Playwright.

## Global Constraints

- Product copy and identifiers use OpenRAG only.
- Organization administrators can never create, invite, promote, bind, or mutate platform superadmin.
- Arbitrary role names never imply access. Only explicit permission bindings and object membership do.
- Every organization, user, role, binding, workspace, and invitation query is organization scoped.
- Backend authorization is deny by default; frontend checks are UX only.
- Migrations preserve existing platform superadmin, admin, user, invitation, and workspace-membership behavior without a privilege-expanding fallback.
- Permission and role changes are audited without secrets or document content.
- Use TDD, strict mypy, Ruff, import-linter, frontend tests, ESLint, TypeScript, build, isolation tests, and Compose smoke verification.

---

## File structure

### Backend files created

- `backend/src/openrag/modules/tenancy/permissions.py`: closed permission vocabulary and immutable built-in role templates.
- `backend/src/openrag/modules/tenancy/authorization.py`: effective-permission resolution, capability guards, and workspace-access helpers.
- `backend/src/openrag/api/routes/roles.py`: organization role catalog, CRUD, and user-binding endpoints.
- `backend/migrations/versions/6c4a2f8b9d10_capability_rbac.py`: schema, data backfill, constraints, and legacy-column removal.
- `backend/tests/modules/tenancy/test_permissions.py`: vocabulary/template unit tests.
- `backend/tests/modules/tenancy/test_authorization.py`: permission resolution and object-access tests.
- `backend/tests/api/test_roles.py`: role/binding API and escalation regression tests.
- `backend/tests/isolation/test_rbac_isolation.py`: cross-organization and custom-role isolation tests.

### Backend files modified

- `backend/src/openrag/modules/auth/models.py`: replace `User.role` with `is_platform_superadmin`; replace invitation role string with `role_id`.
- `backend/src/openrag/modules/auth/schemas.py`: role-ID invitations, permission-aware user output, and safe patch schema.
- `backend/src/openrag/modules/auth/service.py`: validate invite roles, create role binding on acceptance, and project user roles.
- `backend/src/openrag/modules/auth/tokens.py`: emit UI-only permission/platform claims while retaining strict signature and expiry validation.
- `backend/src/openrag/modules/tenancy/models.py`: add roles, permissions, bindings; remove workspace role string.
- `backend/src/openrag/modules/tenancy/context.py`: load current database authorization and expose `require_permission`.
- `backend/src/openrag/modules/tenancy/service.py`: remove role-name branches and use object-access helpers.
- `backend/src/openrag/modules/tenancy/schemas.py`: role/binding schemas and role-free workspace membership.
- `backend/src/openrag/api/routes/auth.py`: require `user.manage`, accept role IDs, and return permission-aware tokens.
- `backend/src/openrag/api/routes/users.py`: require `user.manage`; remove arbitrary role patching.
- `backend/src/openrag/api/routes/workspaces.py`: require capabilities and validate membership independently.
- `backend/src/openrag/api/routes/admin_secrets.py`: platform-superadmin-only guard.
- `backend/src/openrag/api/routes/models.py`: platform-superadmin-only guard until organization model administration is designed.
- `backend/src/openrag/api/app.py`: register roles router.
- `backend/src/openrag/bootstrap.py`: create only a non-assignable platform superadmin.
- `backend/migrations/env.py`: import new tenancy models.
- Existing auth, tenancy, API, bootstrap, isolation, and migration tests: migrate fixtures and assertions.

### Frontend files created

- `frontend/src/app/require-permission.tsx`: permission-aware route guard.
- `frontend/src/features/admin/roles/queries.ts`: typed role and binding queries/mutations.
- `frontend/src/features/admin/roles/roles-page.tsx`: role list, built-in/custom distinction, and editor entry points.
- `frontend/src/features/admin/roles/role-form-dialog.tsx`: name, description, and permission-matrix editor.
- `frontend/src/features/admin/roles/role-form-dialog.test.tsx`: safe custom-role workflow tests.
- `frontend/src/features/admin/roles/roles-page.test.tsx`: role/binding presentation tests.

### Frontend files modified

- `frontend/src/lib/jwt.ts` and tests: parse permission/platform claims safely.
- `frontend/src/lib/use-claims.ts` and tests: expose `hasPermission` and platform state.
- `frontend/src/app/router.tsx`: replace string role gates and add `/admin/roles`.
- `frontend/src/components/layout/sidebar.tsx` and tests: capability-based navigation.
- `frontend/src/features/admin/users/queries.ts`: role catalog and binding contracts.
- `frontend/src/features/admin/users/users-page.tsx`: display bindings and open assignment UI.
- `frontend/src/features/admin/users/invite-dialog.tsx` and tests: select assignable role IDs only.
- `frontend/src/features/admin/users/workspace-access-dialog.tsx`: workspace membership without arbitrary role strings.
- `frontend/src/api/types.ts` and `schema.d.ts`: regenerated contracts.

---

### Task 1: Closed permission vocabulary and persistence model

**Files:**
- Create: `backend/src/openrag/modules/tenancy/permissions.py`
- Modify: `backend/src/openrag/modules/tenancy/models.py`
- Modify: `backend/src/openrag/modules/auth/models.py`
- Test: `backend/tests/modules/tenancy/test_permissions.py`
- Test: `backend/tests/modules/tenancy/test_models.py`

**Interfaces:**
- Produces: `PermissionCode`, `ALL_PERMISSIONS`, `BUILTIN_ROLE_TEMPLATES`, `Role`, `RolePermission`, `UserRoleBinding`.
- Produces: `User.is_platform_superadmin: bool`, `Invitation.role_id: UUID`, and membership-only `WorkspaceMember`.

- [ ] **Step 1: Write failing permission-vocabulary tests**

```python
from openrag.modules.tenancy.permissions import (
    ALL_PERMISSIONS,
    BUILTIN_ROLE_TEMPLATES,
)


def test_builtin_templates_only_use_known_permissions() -> None:
    for template in BUILTIN_ROLE_TEMPLATES.values():
        assert template.permissions
        assert template.permissions <= ALL_PERMISSIONS


def test_platform_superadmin_is_not_an_organization_role() -> None:
    assert "superadmin" not in BUILTIN_ROLE_TEMPLATES
    assert {"administrator", "hse_manager", "engineer", "user"} == set(
        BUILTIN_ROLE_TEMPLATES
    )
```

- [ ] **Step 2: Run the tests and confirm the missing module failure**

Run: `cd backend && uv run pytest tests/modules/tenancy/test_permissions.py -q`

Expected: FAIL with `ModuleNotFoundError: openrag.modules.tenancy.permissions`.

- [ ] **Step 3: Implement the closed vocabulary and built-in templates**

```python
from dataclasses import dataclass
from typing import Literal

PermissionCode = Literal[
    "audit.read",
    "chat.use",
    "document.approve",
    "document.read",
    "document.upload",
    "model.configure",
    "rag.evaluate",
    "role.manage",
    "user.manage",
    "workspace.manage",
    "workspace.read_all",
]

ALL_PERMISSIONS: frozenset[str] = frozenset(PermissionCode.__args__)


@dataclass(frozen=True)
class RoleTemplate:
    key: str
    name: str
    description: str
    permissions: frozenset[str]


BUILTIN_ROLE_TEMPLATES = {
    "administrator": RoleTemplate(
        key="administrator",
        name="Administrator",
        description="Manage organization users, roles, workspaces and knowledge.",
        permissions=ALL_PERMISSIONS,
    ),
    "hse_manager": RoleTemplate(
        key="hse_manager",
        name="HSE Manager",
        description="Manage and approve HSE knowledge in assigned workspaces.",
        permissions=frozenset({
            "chat.use", "document.read", "document.upload", "document.approve"
        }),
    ),
    "engineer": RoleTemplate(
        key="engineer",
        name="Engineer",
        description="Use chat and contribute knowledge in assigned workspaces.",
        permissions=frozenset({"chat.use", "document.read", "document.upload"}),
    ),
    "user": RoleTemplate(
        key="user",
        name="User",
        description="Use grounded chat and read assigned workspace knowledge.",
        permissions=frozenset({"chat.use", "document.read"}),
    ),
}
```

- [ ] **Step 4: Add focused SQLAlchemy models**

Implement these exact persisted responsibilities:

```python
class Role(UUIDPk, Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_roles_org_key"),)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    key: Mapped[str]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    is_system: Mapped[bool] = mapped_column(default=False)
    is_assignable: Mapped[bool] = mapped_column(default=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(primary_key=True)


class UserRoleBinding(UUIDPk, Base):
    __tablename__ = "user_role_bindings"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", "workspace_id", name="uq_user_role_scope"),
    )
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), default=None
    )
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
```

Change `User.role` to `is_platform_superadmin: bool = mapped_column(default=False)`;
change `Invitation.role` to a `role_id` foreign key; remove `WorkspaceMember.role`.
Add model tests that persist all three new model types, reject duplicate role
keys, and prove a workspace member does not acquire permissions merely from a
string.

- [ ] **Step 5: Run focused tests**

Run: `cd backend && uv run pytest tests/modules/tenancy/test_permissions.py tests/modules/tenancy/test_models.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Run: `git add backend/src/openrag/modules/tenancy/permissions.py backend/src/openrag/modules/tenancy/models.py backend/src/openrag/modules/auth/models.py backend/tests/modules/tenancy/test_permissions.py backend/tests/modules/tenancy/test_models.py && git commit -m "feat: model capability based roles"`

### Task 2: Alembic migration and safe legacy backfill

**Files:**
- Create: `backend/migrations/versions/6c4a2f8b9d10_capability_rbac.py`
- Modify: `backend/migrations/env.py`
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: models and templates from Task 1 only for schema parity; migration code must remain self-contained.
- Produces: upgraded databases with one template set per organization, preserved superadmin/admin/user/invitation membership, and no legacy role columns.

- [ ] **Step 1: Write a failing upgrade/backfill test**

Create a migration test that starts at revision `4f2e1c9a7b30`, inserts one
legacy `superadmin`, `admin`, `user`, invitation, and workspace membership,
upgrades to `head`, then asserts:

```python
assert superadmin["is_platform_superadmin"] is True
assert {role["key"] for role in roles} == {
    "administrator", "hse_manager", "engineer", "user"
}
assert admin_binding["role_key"] == "administrator"
assert user_binding["role_key"] == "user"
assert invitation["role_key"] == "administrator"
assert workspace_member_columns == {"workspace_id", "user_id"}
assert "role" not in user_columns
```

- [ ] **Step 2: Run the migration test and confirm the missing revision failure**

Run: `cd backend && uv run pytest tests/test_migrations.py -q`

Expected: FAIL because revision `6c4a2f8b9d10` does not exist.

- [ ] **Step 3: Implement schema and deterministic backfill**

The migration must:

1. Add `users.is_platform_superadmin` with a server default of false.
2. Create `roles`, `role_permissions`, and `user_role_bindings` with the foreign
   keys, indexes, unique constraints, and permission check constraint matching
   `ALL_PERMISSIONS`.
3. Add nullable `invitations.role_id`.
4. Use the Alembic connection plus Python `uuid4()` to create four roles per
   organization and their exact template permissions.
5. Mark legacy `users.role = 'superadmin'` as platform superadmin; bind legacy
   `admin` and `user` to matching organization roles; reject any unexpected
   legacy role instead of treating it as privileged.
6. Backfill invitation role IDs from only `admin` and `user`; fail closed for
   unknown invitation values.
7. Make `invitations.role_id` non-null; drop `users.role`, `invitations.role`,
   and `workspace_members.role`.
8. Downgrade by recreating legacy columns from platform flag and primary
   organization binding, then remove RBAC tables in dependency order.

- [ ] **Step 4: Register all models with Alembic and verify both directions**

Run:

```bash
cd backend
uv run alembic upgrade head
uv run alembic downgrade 4f2e1c9a7b30
uv run alembic upgrade head
uv run pytest tests/test_migrations.py -q
```

Expected: every command succeeds and the migration test passes.

- [ ] **Step 5: Commit**

Run: `git add backend/migrations backend/tests/test_migrations.py && git commit -m "feat: migrate legacy roles to capability rbac"`

### Task 3: Effective permissions and deny-by-default object access

**Files:**
- Create: `backend/src/openrag/modules/tenancy/authorization.py`
- Modify: `backend/src/openrag/modules/tenancy/context.py`
- Modify: `backend/src/openrag/modules/tenancy/service.py`
- Modify: `backend/src/openrag/modules/documents/service.py`
- Test: `backend/tests/modules/tenancy/test_authorization.py`
- Test: `backend/tests/isolation/test_rbac_isolation.py`

**Interfaces:**
- Produces: `AuthorizationSnapshot`, `resolve_authorization`, `require_permission`, `require_platform_superadmin`, `ensure_workspace_access`.
- Removes: role-name comparisons from authorization decisions.

- [ ] **Step 1: Write failing authorization tests**

Cover all of these cases explicitly, using the existing PostgreSQL fixtures:

- a custom role with no permissions cannot list a workspace merely because its
  name is not `user`;
- an organization permission such as `document.read` does not bypass workspace
  membership;
- `workspace.read_all` exposes only workspaces in the caller's organization;
- a workspace-scoped permission applies only to its bound workspace;
- platform superadmin passes a capability guard but service queries still apply
  their deliberate organization scope;
- an inactive user is rejected before permissions are resolved.

- [ ] **Step 2: Run the tests and verify role-string behavior fails them**

Run: `cd backend && uv run pytest tests/modules/tenancy/test_authorization.py tests/isolation/test_rbac_isolation.py -q`

Expected: FAIL because authorization still compares `context.role` to strings.

- [ ] **Step 3: Implement the immutable authorization snapshot**

```python
@dataclass(frozen=True)
class AuthorizationSnapshot:
    user_id: UUID
    org_id: UUID
    is_platform_superadmin: bool
    org_permissions: frozenset[str]
    workspace_permissions: Mapping[UUID, frozenset[str]]
    workspace_ids: frozenset[UUID]

    def has(self, permission: str, workspace_id: UUID | None = None) -> bool:
        if self.is_platform_superadmin:
            return True
        if permission in self.org_permissions:
            return True
        return (
            workspace_id is not None
            and permission in self.workspace_permissions.get(workspace_id, frozenset())
        )
```

`resolve_authorization(session, user)` must join bindings to roles and
permissions with `binding.org_id == user.org_id`, `role.org_id == user.org_id`,
and workspace organization validation. Invalid cross-organization rows are
ignored defensively and should be impossible through constraints/services.

- [ ] **Step 4: Replace dependencies and object checks**

Implement:

```python
def require_permission(
    permission: str,
) -> Callable[[TenantContext], Awaitable[TenantContext]]:
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"unknown permission: {permission}")

    async def guard(
        context: Annotated[TenantContext, Depends(get_tenant_context)],
    ) -> TenantContext:
        if not context.authorization.has(permission):
            raise AuthorizationError(f"requires permission: {permission}")
        return context

    return guard


def require_platform_superadmin(
) -> Callable[[TenantContext], Awaitable[TenantContext]]:
    async def guard(
        context: Annotated[TenantContext, Depends(get_tenant_context)],
    ) -> TenantContext:
        if not context.authorization.is_platform_superadmin:
            raise AuthorizationError("requires platform superadmin")
        return context

    return guard


async def ensure_workspace_access(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    permission: str,
) -> Workspace:
    # same organization is always required
    # permission is required
    # membership is required unless workspace.read_all is effective
    # inaccessible objects return the existing safe not-found/access error
```

Remove every `context.role == "user"`, `context.role == "superadmin"`, and
`require_role` authorization branch. Document access must delegate to the same
workspace object-access policy.

- [ ] **Step 5: Run authorization and isolation tests**

Run: `cd backend && uv run pytest tests/modules/tenancy/test_authorization.py tests/isolation -q`

Expected: PASS with zero cross-organization or custom-role leakage.

- [ ] **Step 6: Commit**

Run: `git add backend/src/openrag/modules/tenancy backend/src/openrag/modules/documents/service.py backend/tests/modules/tenancy backend/tests/isolation && git commit -m "fix: enforce capability and object authorization"`

### Task 4: Secure role, binding, invitation, and token APIs

**Files:**
- Create: `backend/src/openrag/api/routes/roles.py`
- Modify: `backend/src/openrag/modules/tenancy/schemas.py`
- Modify: `backend/src/openrag/modules/tenancy/service.py`
- Modify: `backend/src/openrag/modules/auth/schemas.py`
- Modify: `backend/src/openrag/modules/auth/service.py`
- Modify: `backend/src/openrag/modules/auth/tokens.py`
- Modify: `backend/src/openrag/api/routes/auth.py`
- Modify: `backend/src/openrag/api/routes/users.py`
- Modify: `backend/src/openrag/api/routes/workspaces.py`
- Modify: `backend/src/openrag/api/routes/admin_secrets.py`
- Modify: `backend/src/openrag/api/routes/models.py`
- Modify: `backend/src/openrag/api/app.py`
- Modify: `backend/src/openrag/bootstrap.py`
- Test: `backend/tests/api/test_roles.py`
- Test: `backend/tests/api/test_auth_routes.py`
- Test: `backend/tests/api/test_users.py`
- Test: `backend/tests/api/test_workspaces.py`
- Test: `backend/tests/test_bootstrap.py`

**Interfaces:**
- Produces role endpoints under `/api/v1/roles` and `/api/v1/users/{user_id}/role-bindings`.
- Produces invitation input `{email, role_id}` and token claims `platform_superadmin` plus `permissions` for UX only.
- Backend request authorization always re-resolves current database state.

- [ ] **Step 1: Write escalation and API contract tests first**

Required tests use real route requests and assert status, response schema,
database state, and audit rows:

- organization admin cannot create or assign platform superadmin;
- custom roles reject unknown permissions with 422;
- role CRUD is organization scoped and audited;
- protected fields of system roles cannot be mutated;
- unassignable and cross-organization roles cannot be bound;
- the last active Administrator binding cannot be removed;
- accepting an invitation creates the validated role binding;
- an arbitrary legacy role string is rejected as an extra field;
- organization Administrator is rejected by platform Models and Secrets routes;
- bootstrap is the only supported path that creates platform superadmin.

- [ ] **Step 2: Run focused API tests and verify failures**

Run: `cd backend && uv run pytest tests/api/test_roles.py tests/api/test_auth_routes.py tests/api/test_users.py tests/test_bootstrap.py -q`

Expected: FAIL because role CRUD/bindings do not exist and arbitrary role strings are accepted.

- [ ] **Step 3: Implement strict schemas**

Use `ConfigDict(extra="forbid")`, bounded names/descriptions, and the closed
permission vocabulary. Required shapes:

```python
class RoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=500)
    permissions: set[PermissionCode]


class RoleOut(BaseModel):
    id: UUID
    key: str
    name: str
    description: str
    permissions: list[PermissionCode]
    is_system: bool
    is_assignable: bool


class RoleBindingReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_ids: list[UUID] = Field(max_length=16)


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    role_id: UUID
```

Remove `UserPatch.role`. `UserOut` returns `is_platform_superadmin` and a list of
role summaries; it never exposes or accepts a platform-role assignment field.

- [ ] **Step 4: Implement role and binding services with invariants**

Role keys for custom roles are generated server-side from UUIDs, not user names.
Only `role.manage` can manage roles/bindings. Services validate same-org target,
known permissions, assignable role, workspace organization, and target user.
System roles allow description/permission changes only if the resulting
administrator invariant remains valid; their key/name/system flags cannot be
changed. Deleting a bound/system role is rejected. Replacing bindings locks the
target bindings and refuses removal of the last active Administrator.

Every create/update/delete/bind operation records an audit action with role/user
IDs, never permission-bearing raw request bodies.

- [ ] **Step 5: Update token and bootstrap boundaries**

`issue_access_token` includes:

```python
{
    "sub": str(user_id),
    "org": str(org_id),
    "platform_superadmin": is_platform_superadmin,
    "permissions": sorted(org_permissions),
    "iat": now,
    "exp": expires,
}
```

`decode_access_token` enforces the existing algorithm allowlist and validates
types. `get_tenant_context` trusts only `sub` for lookup, reloads the active user,
and resolves current permissions from PostgreSQL. Bootstrap sets
`is_platform_superadmin=True`; no HTTP service accepts that property.

- [ ] **Step 6: Protect every route with explicit capabilities**

- Users/invitations: `user.manage`.
- Roles/bindings: `role.manage`.
- Workspace create/update/membership: `workspace.manage` plus object checks.
- Chat/document routes: `chat.use`, `document.read`, or `document.upload` plus
  workspace access as appropriate.
- Secrets and current platform model registry: platform-superadmin only.

Register the role router and keep OpenAPI response models explicit.

- [ ] **Step 7: Run focused and complete backend tests**

Run:

```bash
cd backend
uv run pytest tests/api/test_roles.py tests/api/test_auth_routes.py tests/api/test_users.py tests/api/test_workspaces.py tests/test_bootstrap.py -q
uv run pytest tests/isolation -q
uv run ruff check src tests
uv run mypy src
uv run lint-imports
```

Expected: all commands pass.

- [ ] **Step 8: Commit**

Run: `git add backend && git commit -m "feat: expose secure capability role administration"`

### Task 5: Permission-aware frontend and role builder

**Files:**
- Create: `frontend/src/app/require-permission.tsx`
- Create: `frontend/src/features/admin/roles/queries.ts`
- Create: `frontend/src/features/admin/roles/roles-page.tsx`
- Create: `frontend/src/features/admin/roles/role-form-dialog.tsx`
- Create: `frontend/src/features/admin/roles/role-form-dialog.test.tsx`
- Create: `frontend/src/features/admin/roles/roles-page.test.tsx`
- Modify: `frontend/src/lib/jwt.ts`
- Modify: `frontend/src/lib/jwt.test.ts`
- Modify: `frontend/src/lib/use-claims.ts`
- Modify: `frontend/src/lib/use-claims.test.ts`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/components/layout/sidebar.tsx`
- Modify: `frontend/src/components/layout/sidebar.test.tsx`
- Modify: `frontend/src/features/admin/users/queries.ts`
- Modify: `frontend/src/features/admin/users/users-page.tsx`
- Modify: `frontend/src/features/admin/users/invite-dialog.tsx`
- Modify: `frontend/src/features/admin/users/invite-dialog.test.tsx`
- Modify: `frontend/src/features/admin/users/workspace-access-dialog.tsx`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/schema.d.ts`

**Interfaces:**
- Consumes Task 4 role/catalog/binding APIs and permission claims.
- Produces `/admin/roles`, safe role creation/editing, assignable-role invitations, and capability-based navigation.

- [ ] **Step 1: Write failing JWT and route-guard tests**

```tsx
it('denies a route when the claim lacks the permission', () => { /* render */ });
it('allows platform superadmin without an organization permission', () => { /* render */ });
it('treats malformed permission claims as an empty list', () => { /* decode */ });
it('does not treat a role name as a permission', () => { /* decode legacy token */ });
```

- [ ] **Step 2: Implement safe claims and `RequirePermission`**

The decoded UI-only shape is:

```ts
export interface Claims {
  sub: string;
  org: string;
  platform_superadmin: boolean;
  permissions: string[];
  exp: number;
}

export function hasPermission(claims: Claims, permission: string): boolean {
  return claims.platform_superadmin || claims.permissions.includes(permission);
}
```

Invalid arrays, element types, booleans, expiry, or UUID-like identifiers make
decoding return `null`. Route denial navigates safely and never substitutes a
role-string fallback.

- [ ] **Step 3: Write role-builder and invite tests before UI code**

Cover these exact browser-visible behaviors with Testing Library and mocked
typed API responses:

- list built-in and custom roles with permission counts;
- create a custom role using only catalog permissions;
- omit platform superadmin from every role option;
- submit invitation roles using opaque role IDs;
- display effective user bindings and save replacement role IDs;
- render server authorization errors without retaining optimistic privilege UI.

- [ ] **Step 4: Implement the role page and permission editor**

The editor uses checkboxes generated from `/roles/catalog`; no free-form
permission strings are accepted. Built-in roles display a protected badge.
Custom role deletion requires the existing confirmation dialog and relies on
server conflict checks. The page never exposes platform-superadmin controls.

- [ ] **Step 5: Migrate users, invitations, router, and sidebar**

Users display role chips from bindings. Assignment uses role IDs from assignable
same-org roles. Invitations submit `{email, role_id}`. Workspace access remains
an independent membership action. Routes and navigation require `user.manage`,
`role.manage`, or platform-superadmin as appropriate.

- [ ] **Step 6: Regenerate schema and verify frontend**

Run:

```bash
cd frontend
pnpm generate:api
pnpm test -- --run
pnpm lint
pnpm typecheck
pnpm build
```

Expected: all commands pass and the generated schema contains no mutable user
role string or platform-superadmin assignment input.

- [ ] **Step 7: Commit**

Run: `git add frontend && git commit -m "feat: add secure custom role administration"`

### Task 6: End-to-end security regression and public handoff

**Files:**
- Create: `frontend/e2e/rbac.spec.ts`
- Modify: `backend/tests/isolation/test_rbac_isolation.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-19-openrag-production-agentic-rag-design.md` only if implementation evidence changes an assumption.

**Interfaces:**
- Consumes all prior tasks.
- Produces verified browser/API security behavior and deployment documentation.

- [ ] **Step 1: Add browser security smoke cases**

The Playwright test must prove:

1. An Administrator sees Users and Roles but not platform Models/Secrets.
2. The role editor cannot select platform superadmin.
3. An Engineer cannot open Users or Roles and receives an authorization error
   if calling the API directly.
4. Assigning an HSE Manager to one workspace does not expose another workspace.
5. A platform superadmin retains platform administration after migration.

- [ ] **Step 2: Run full backend and frontend verification**

Run:

```bash
cd backend
uv run pytest -q
uv run ruff check src tests
uv run mypy src
uv run lint-imports
cd ../frontend
pnpm test -- --run
pnpm lint
pnpm typecheck
pnpm build
pnpm e2e
```

Expected: every command passes with no skipped RBAC security cases.

- [ ] **Step 3: Run migration and Compose smoke verification**

Run the repository's documented Compose build/start/migrate/bootstrap/smoke
commands, then verify `/healthz`, `/readyz`, login, role catalog, role denial,
workspace isolation, and logout. Do not print tokens or credentials.

Expected: services are healthy, the migrated default login works, authorization
matches the tests, and no secret appears in logs.

- [ ] **Step 4: Update deployment documentation**

Document role templates, custom-role constraints, platform-superadmin bootstrap,
permission meanings, migration behavior, rollback warning, and verification
commands. State explicitly that organization admins cannot assign platform
superadmin and that workspace membership is independent from role names.

- [ ] **Step 5: Final security review and commit**

Search for remaining authorization role strings:

Run: `rg -n 'require_role|context\.role|user\.role|role ==|role !=' backend/src frontend/src`

Expected: no authorization decision uses a role-name comparison. Any remaining
`role` text is presentation/schema terminology for role resources only.

Run: `git add README.md backend frontend docs && git commit -m "test: verify capability rbac end to end"`

- [ ] **Step 6: Push reviewed commits**

Run: `git push origin main`

Expected: public `origin/main` contains the reviewed RBAC slice and the worktree
contains only the intentionally untracked benchmark repositories.

## Self-review record

- Spec coverage: platform-superadmin separation, custom organization roles,
  Administrator/HSE Manager/Engineer/User templates, capability checks,
  workspace object authorization, invitations, frontend administration,
  auditing, migration, isolation, and browser verification are assigned to
  explicit tasks.
- Placeholder scan: every task identifies exact behaviors, commands, expected
  outcomes, interfaces, and security invariants.
- Type consistency: `Role`, `RolePermission`, `UserRoleBinding`,
  `AuthorizationSnapshot`, `require_permission`, role-ID invitation input, and
  permission claims retain the same names and responsibilities across tasks.
- Security sequencing: role-string escalation is removed before custom roles
  are exposed in the frontend.
