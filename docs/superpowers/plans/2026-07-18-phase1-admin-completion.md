# Phase 1 Admin Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the approved Phase 1 admin contracts by adding workspace-member visibility and assignment, workspace default-model configuration, and model editing.

**Architecture:** Extend the existing tenancy API with a typed, org-scoped member list while preserving the current role dependencies. Add focused React dialogs that use the generated OpenAPI client and TanStack Query; keep model credentials write-only and invalidate both admin and public model caches after changes.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Pydantic v2, pytest/httpx, React 18, strict TypeScript, TanStack Query, Radix dialogs, Tailwind semantic tokens, Vitest/Testing Library.

## Global Constraints

- Work directly on `main`; the user explicitly authorized periodic direct commits and pushes.
- Use TDD: write and observe each failing test before production edits.
- All backend routes remain under `/api/v1` and use declared role dependencies.
- Cross-organization resources return the existing typed not-found/access errors without leaking existence.
- API keys are write-only: edit forms start blank and GET responses never contain key material.
- Frontend server state uses TanStack Query and the generated OpenAPI client only.
- Frontend styling uses semantic tokens; raw Tailwind palette classes remain forbidden.
- Run backend gates from `backend/`: `uv run pytest tests -v && uv run ruff check . && uv run mypy src && uv run lint-imports`.
- Run frontend gates from `frontend/`: `corepack pnpm lint && corepack pnpm typecheck && corepack pnpm test && corepack pnpm build`.

---

### Task 1: Workspace member list API

**Files:**
- Modify: `backend/src/openrag/modules/tenancy/schemas.py`
- Modify: `backend/src/openrag/modules/tenancy/service.py`
- Modify: `backend/src/openrag/api/routes/workspaces.py`
- Modify: `backend/tests/api/test_workspaces.py`

**Interfaces:**
- Produces: `WorkspaceMemberOut(user_id: UUID, email: EmailStr, role: str)`.
- Produces: `list_members(session, context, workspace_id) -> list[WorkspaceMemberOut]`.
- Produces: `GET /api/v1/workspaces/{workspace_id}/members -> WorkspaceMemberOut[]` for admins and superadmins.

- [ ] **Step 1: Write the failing API test**

Append a test that creates two same-org users, adds one member, calls the new GET endpoint as an admin, and asserts only that member is returned:

```python
async def test_admin_lists_workspace_members(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    member = User(
        org_id=seeded_user.org_id,
        email="member@acme.com",
        password_hash=seeded_user.password_hash,
        role="user",
    )
    session.add(member)
    await session.commit()
    headers = await auth(client, "a@acme.com")
    workspace = await client.post(
        "/api/v1/workspaces", json={"name": "Member list"}, headers=headers
    )
    workspace_id = workspace.json()["id"]
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={"user_id": str(member.id), "role": "member"},
        headers=headers,
    )

    response = await client.get(
        f"/api/v1/workspaces/{workspace_id}/members", headers=headers
    )

    assert response.status_code == 200
    assert response.json() == [
        {"user_id": str(member.id), "email": "member@acme.com", "role": "member"}
    ]
```

- [ ] **Step 2: Verify the new test fails**

Run: `uv run pytest tests/api/test_workspaces.py::test_admin_lists_workspace_members -v`

Expected: FAIL with `405 Method Not Allowed` because only POST exists.

- [ ] **Step 3: Add the response schema and org-scoped query**

Add to `schemas.py`:

```python
class WorkspaceMemberOut(BaseModel):
    user_id: UUID
    email: str
    role: str
```

Add to `service.py`:

```python
async def list_members(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
) -> list[WorkspaceMemberOut]:
    await get_workspace(session, context, workspace_id)
    rows = await session.execute(
        select(WorkspaceMember, User.email)
        .join(User, User.id == WorkspaceMember.user_id)
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            Workspace.org_id == context.org_id,
            User.org_id == context.org_id,
        )
        .order_by(User.email)
    )
    return [
        WorkspaceMemberOut(user_id=membership.user_id, email=email, role=membership.role)
        for membership, email in rows.all()
    ]
```

Import `WorkspaceMemberOut` in the service and route. Add to `routes/workspaces.py`:

```python
@router.get("/{workspace_id}/members", response_model=list[WorkspaceMemberOut])
async def list_members(
    workspace_id: UUID,
    session: SessionDep,
    context: AdminDep,
) -> list[WorkspaceMemberOut]:
    return await service.list_members(session, context, workspace_id)
```

- [ ] **Step 4: Verify the focused and tenancy tests pass**

Run: `uv run pytest tests/api/test_workspaces.py tests/modules/tenancy -v`

Expected: all selected tests pass.

- [ ] **Step 5: Run backend gates and commit**

Run: `uv run pytest tests -q && uv run ruff check . && uv run mypy src && uv run lint-imports`

Expected: 136+ tests pass and all static gates exit zero.

Commit:

```bash
git add backend/src/openrag/modules/tenancy backend/src/openrag/api/routes/workspaces.py backend/tests/api/test_workspaces.py
git commit -m "feat: list workspace members for administrators"
git push origin main
```

---

### Task 2: Regenerate the frontend contract and add workspace queries

**Files:**
- Modify: `frontend/src/api/schema.d.ts`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/features/workspaces/queries.ts`
- Test: `frontend/src/features/workspaces/queries.test.tsx`

**Interfaces:**
- Produces: `WorkspaceMemberOut` stable alias.
- Produces: `useWorkspaceMembers(workspaceId)` and `useAddWorkspaceMember()`.
- Produces: `usePatchWorkspace()` for `default_model_id` changes.

- [ ] **Step 1: Restart the local API and regenerate OpenAPI types**

Restart the existing uvicorn process from `backend/`, then run from `frontend/`:

```bash
corepack pnpm generate:api
```

Expected: `WorkspaceMemberOut` and the GET-members operation appear in `src/api/schema.d.ts`; generated output is the only schema edit.

- [ ] **Step 2: Write failing query-hook tests**

Create tests that render each mutation with a `QueryClientProvider`, mock `fetch`, call `mutate`, and assert:

```ts
expect(request.method).toBe('POST');
expect(request.url).toContain(`/api/v1/workspaces/${workspaceId}/members`);
expect(await request.clone().json()).toEqual({ user_id: userId, role: 'member' });
```

and:

```ts
expect(request.method).toBe('PATCH');
expect(await request.clone().json()).toEqual({ default_model_id: modelId });
```

- [ ] **Step 3: Verify the hook tests fail**

Run: `corepack pnpm test src/features/workspaces/queries.test.tsx -- --run`

Expected: FAIL because the hooks are not exported.

- [ ] **Step 4: Add stable type and query hooks**

Add to `api/types.ts`:

```ts
export type WorkspaceMemberOut = components['schemas']['WorkspaceMemberOut'];
```

Add to `features/workspaces/queries.ts`:

```ts
export function useWorkspaceMembers(workspaceId: string | null) {
  return useQuery({
    queryKey: ['workspace-members', workspaceId],
    enabled: workspaceId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/workspaces/{workspace_id}/members', {
        params: { path: { workspace_id: workspaceId as string } },
      });
      if (error) throw new Error('Failed to load workspace members');
      return data;
    },
  });
}

export function useAddWorkspaceMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { workspaceId: string; userId: string }) => {
      const { error } = await api.POST('/api/v1/workspaces/{workspace_id}/members', {
        params: { path: { workspace_id: input.workspaceId } },
        body: { user_id: input.userId, role: 'member' },
      });
      if (error) throw new Error('Failed to add workspace member');
    },
    onSuccess: (_data, input) =>
      void queryClient.invalidateQueries({ queryKey: ['workspace-members', input.workspaceId] }),
  });
}

export function usePatchWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { workspaceId: string; defaultModelId: string | null }) => {
      const { data, error } = await api.PATCH('/api/v1/workspaces/{workspace_id}', {
        params: { path: { workspace_id: input.workspaceId } },
        body: { default_model_id: input.defaultModelId },
      });
      if (error) throw new Error('Failed to update workspace');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
  });
}
```

- [ ] **Step 5: Verify hooks and frontend gates**

Run: `corepack pnpm test src/features/workspaces -- --run && corepack pnpm lint && corepack pnpm typecheck`

Expected: all commands pass.

- [ ] **Step 6: Commit and push**

```bash
git add frontend/src/api frontend/src/features/workspaces
git commit -m "feat: add workspace administration queries"
git push origin main
```

---

### Task 3: Workspace membership dialog in Admin Users

**Files:**
- Create: `frontend/src/features/admin/users/workspace-access-dialog.tsx`
- Create: `frontend/src/features/admin/users/workspace-access-dialog.test.tsx`
- Modify: `frontend/src/features/admin/users/users-page.tsx`

**Interfaces:**
- Consumes: `useWorkspaces`, `useWorkspaceMembers`, and `useAddWorkspaceMember`.
- Produces: `<WorkspaceAccessDialog user open onOpenChange>` with workspace selection and explicit member state.

- [ ] **Step 1: Write the failing dialog test**

Mock workspaces and members responses, open the dialog for `new@acme.com`, select `Finance`, click `Grant access`, and assert the POST body is `{user_id: 'user-1', role: 'member'}`. Also assert an existing member sees `Already a member` and no grant button.

- [ ] **Step 2: Verify the dialog test fails**

Run: `corepack pnpm test src/features/admin/users/workspace-access-dialog.test.tsx -- --run`

Expected: FAIL because the dialog module does not exist.

- [ ] **Step 3: Implement the focused dialog**

The dialog must:

```tsx
const [workspaceId, setWorkspaceId] = useState<string>('');
const workspaces = useWorkspaces();
const members = useWorkspaceMembers(workspaceId || null);
const addMember = useAddWorkspaceMember();
const isMember = members.data?.some((member) => member.user_id === user.id) ?? false;
```

Render a labeled workspace `NativeSelect`, loading/error states, the current membership state, and a `Grant access` button that calls:

```tsx
addMember.mutate(
  { workspaceId, userId: user.id },
  {
    onSuccess: () => toast.success(`${user.email} can now access this workspace`),
    onError: (error) => toast.error(error.message),
  },
);
```

Reset the selected workspace and mutation state when the dialog closes.

- [ ] **Step 4: Wire the dialog into the users table**

Add a `Workspace access` action for non-superadmin rows and keep the existing deactivate/reactivate control. Store the selected `UserOut` separately from the activation-confirmation state so the dialogs never overlap.

- [ ] **Step 5: Verify focused and full frontend gates**

Run: `corepack pnpm test src/features/admin/users -- --run && corepack pnpm lint && corepack pnpm typecheck && corepack pnpm build`

Expected: all commands pass.

- [ ] **Step 6: Commit and push**

```bash
git add frontend/src/features/admin/users
git commit -m "feat: manage user workspace access"
git push origin main
```

---

### Task 4: Current-workspace default model dialog

**Files:**
- Create: `frontend/src/components/layout/workspace-settings-dialog.tsx`
- Create: `frontend/src/components/layout/workspace-settings-dialog.test.tsx`
- Modify: `frontend/src/components/layout/workspace-switcher.tsx`

**Interfaces:**
- Consumes: active `WorkspaceOut`, `useModels()`, and `usePatchWorkspace()`.
- Produces: admin-only settings button and dialog that sets or clears `default_model_id`.

- [ ] **Step 1: Write the failing component test**

Render the dialog with an active workspace, mock two public models, select one, save, and assert the PATCH body:

```ts
expect(await request.clone().json()).toEqual({ default_model_id: 'model-2' });
```

Add a second assertion that selecting `Automatic` sends `null`.

- [ ] **Step 2: Verify the component test fails**

Run: `corepack pnpm test src/components/layout/workspace-settings-dialog.test.tsx -- --run`

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement the settings dialog**

Render a `NativeSelect` with:

```tsx
<option value="">Automatic — first enabled model</option>
{models.map((model) => (
  <option key={model.id} value={model.id}>{model.display_name}</option>
))}
```

Initialize from `workspace.default_model_id`, submit through `usePatchWorkspace`, close on success, and show mutation errors in an accessible alert.

- [ ] **Step 4: Add the admin-only settings trigger**

In `WorkspaceSwitcher`, show a `Settings2` icon button beside the switcher only when a workspace is selected and claims role is `admin` or `superadmin`. Give it `aria-label="Workspace settings"` and open the dialog for the current workspace.

- [ ] **Step 5: Verify tests and gates**

Run: `corepack pnpm test src/components/layout -- --run && corepack pnpm lint && corepack pnpm typecheck && corepack pnpm build`

Expected: all commands pass.

- [ ] **Step 6: Commit and push**

```bash
git add frontend/src/components/layout frontend/src/features/workspaces/queries.ts
git commit -m "feat: configure workspace default model"
git push origin main
```

---

### Task 5: Edit registered models without exposing secrets

**Files:**
- Modify: `frontend/src/features/admin/models/model-form-dialog.tsx`
- Modify: `frontend/src/features/admin/models/model-form-dialog.test.tsx`
- Modify: `frontend/src/features/admin/models/models-page.tsx`

**Interfaces:**
- Produces: `<ModelFormDialog model?: ModelOut | null>` supporting create and edit modes.
- Edit mode patches `display_name`, conditional `base_url`, and optional blank-by-default `api_key`; provider kind and LiteLLM model id remain immutable because the backend patch schema intentionally excludes them.

- [ ] **Step 1: Write failing edit-mode tests**

Render with a `ModelOut`, assert display name/base URL are prefilled, API key is blank, no existing secret value is rendered, and submission sends PATCH without `api_key` when the key field remains empty. Add a second test that typing a replacement key includes it.

- [ ] **Step 2: Verify the edit tests fail**

Run: `corepack pnpm test src/features/admin/models/model-form-dialog.test.tsx -- --run`

Expected: FAIL because the component accepts no `model` prop and always POSTs.

- [ ] **Step 3: Implement dual create/edit behavior**

Use `useEffect` on `open` and `model` to initialize non-secret fields. In edit mode render provider and model id as read-only values, keep the password field empty, and submit:

```tsx
const patch = {
  display_name: displayName.trim(),
  ...(needsBaseUrl ? { base_url: baseUrl.trim() } : {}),
  ...(apiKey ? { api_key: apiKey } : {}),
};
patchModel.mutate({ modelId: model.id, body: patch }, callbacks);
```

Create mode keeps the existing POST payload and provider-specific inputs. Dialog title/button become `Edit model` / `Save changes` in edit mode.

- [ ] **Step 4: Add the edit action to the model table**

Add a `Pencil` icon button with `aria-label={\`Edit ${model.display_name}\`}`. Store `editing: ModelOut | null`, pass it to the form dialog, and leave enable/delete behavior unchanged.

- [ ] **Step 5: Verify all frontend gates**

Run: `corepack pnpm lint && corepack pnpm typecheck && corepack pnpm test -- --run && corepack pnpm build && corepack pnpm e2e`

Expected: 110+ tests pass, build exits zero, and Playwright reports one skip without `E2E=1`.

- [ ] **Step 6: Commit and push**

```bash
git add frontend/src/features/admin/models
git commit -m "feat: edit registered models securely"
git push origin main
```

---

## Completion Evidence

- Admin can list a workspace's members and grant a user access from the Users page.
- Admin can set or clear the active workspace's default model.
- Superadmin can edit display name, base URL, and rotate a provider key without any GET or prefilled field exposing the old key.
- Backend tests/static gates and frontend tests/lint/typecheck/build all pass.
- Generated OpenAPI schema contains every new route and response model.
