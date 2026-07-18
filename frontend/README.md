# OpenRAG frontend

The OpenRAG web application is built with React 18, Vite, strict TypeScript, Tailwind CSS, TanStack Query, and a generated OpenAPI client.

## Commands

Run these from `frontend/`:

| Command | Purpose |
|---|---|
| `corepack pnpm dev` | Start the development server on port 5173 and proxy `/api` to port 8000 |
| `corepack pnpm generate:api` | Regenerate `src/api/schema.d.ts` from the running backend |
| `corepack pnpm test` | Run Vitest unit and component tests |
| `corepack pnpm test:watch` | Run Vitest in watch mode |
| `corepack pnpm lint` | Run ESLint, including semantic-palette enforcement |
| `corepack pnpm typecheck` | Run strict TypeScript validation |
| `corepack pnpm build` | Produce a verified production build |
| `corepack pnpm e2e` | Run Playwright; skips unless `E2E=1` |

## Structure

- `src/features/` contains feature-oriented auth, chat, documents, workspaces, and admin modules.
- `src/components/ui/` contains reusable accessible primitives.
- `src/components/layout/` contains the protected application shell.
- `src/components/markdown/` safely renders model output and citation markers.
- `src/api/` contains the generated schema, API client, and stable type aliases.
- `src/lib/` contains auth, JWT, SSE, query-client, and theme utilities.
- `src/styles/tokens.css` is the single visual-token source for light and dark themes.
- `e2e/` contains the live-stack Playwright acceptance journey and its PDF fixture.

## Frontend rules

- Use semantic classes such as `bg-subtle`, `text-muted`, and `border-line`; raw Tailwind palette classes are forbidden in features and shared components.
- Keep all remote state in TanStack Query rather than fetching in effects.
- Render model output through the sanitized `Markdown` component. Never use `dangerouslySetInnerHTML`.
- Treat access tokens as in-memory values. The refresh token remains in its HTTP-only cookie.
- Lazy-load page-level routes to protect the initial bundle.
- Preserve accessible names because the live acceptance test uses them as contracts.

## Live E2E smoke

The acceptance journey signs in, ensures a model and workspace exist, uploads a distinctive PDF, waits for indexing, asks a question, resolves a citation to the uploaded filename, edits the question, and verifies sibling navigation.

Prerequisites: the infrastructure, API, worker, and Vite server are running. Then execute:

```bash
E2E=1 \
E2E_EMAIL=root@openrag.internal \
E2E_PASSWORD=changeme123 \
E2E_OPENAI_API_KEY=sk-... \
  corepack pnpm e2e
```

`E2E_OPENAI_API_KEY` is used only when the model registry is empty. Omit it when a working completion model is already registered. `E2E_BASE_URL` defaults to `http://localhost:5173`.
