# OpenRAG Phase 1 — Plan D: Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The themed OpenRAG web app: login/invite flows, app shell, streaming chat with citations and full message controls (copy / edit-in-place / regenerate / `< n/n >` sibling navigation), documents page with live status, Admin › Users, Superadmin › Models — all against the real backend, ending with the Playwright done-criteria smoke.

**Architecture:** Vite + React 18 + TypeScript strict + pnpm. Feature folders mirror backend modules (`src/features/{auth,chat,documents,admin,workspaces}`), shared UI in `src/components`, generated OpenAPI client in `src/api`, utilities in `src/lib` (foundation spec §2). Server state exclusively via TanStack Query v5 over an `openapi-fetch` client generated from the backend schema. Theme is the token contract in `...-openrag-frontend-theme-design.md` — CSS custom properties consumed by Tailwind; raw palette classes are lint-blocked. This plan is 4 of 4 and runs after Plans A–C.

**Tech Stack:** React 18.3, TypeScript ~5.6 strict, Vite 6, Tailwind 3.4 (v4 deferred: its CSS-first config changes the token mechanism and the shadcn ecosystem is stable on 3.4), Radix primitives + shadcn-style components (hand-themed, see Task 3), TanStack Query 5, react-router-dom 6, openapi-typescript + openapi-fetch, react-markdown + remark-gfm, @fontsource/inter, vitest 3 + Testing Library, ESLint 9 (typescript-eslint) + Prettier, Playwright.

## Assumptions (backend surface from Plans B & C)

Auth, users, workspaces, invitations routes exist now (verified in `backend/src/openrag/api/routes/`). Plans B & C — executed before this plan — add the rest. The generated client (Task 4) reads the real schema from `http://localhost:8000/api/openapi.json`; **if a field/route name below differs from the generated `src/api/schema.d.ts`, fix the alias in `src/api/types.ts` only** — components consume the aliases, never `components['schemas'][...]` directly.

- **Documents (Plan B):** `POST /api/v1/workspaces/{id}/documents` (multipart, field `files`, → list of documents), `GET /api/v1/workspaces/{id}/documents` → `[{id, filename, mime, size_bytes, status: "queued"|"processing"|"indexed"|"failed", error, page_count, created_at}]`, `DELETE /api/v1/documents/{id}` → 204, `POST /api/v1/workspaces/{id}/search`.
- **Chats (Plan C):** `GET/POST /api/v1/chats` (`POST {workspace_id, title?}`; `GET ?workspace_id=` filter), `GET /api/v1/chats/{id}` → `{id, title, workspace_id, messages: MessageOut[]}` where `MessageOut = {id, parent_message_id, sibling_index, role, content, model_id, created_at}` — a **tree as a flat list**; the client renders the newest-sibling path by default, `< n/n >` navigation across siblings, selection is client-side state (phase1 spec §2.1).
- **Chat streaming (Plan C):** `POST /api/v1/chats/{id}/messages` body `{content, parent_message_id?, model_id?}` and `POST /api/v1/messages/{id}/regenerate` both return `text/event-stream` with events `retrieval_started {}` → `sources {sources: [{n, document_id, filename, page, score}]}` → `token {delta}`* → `citations {citations: [{n, document_id, page}]}` → `done {message_id, prompt_tokens, completion_tokens, no_answer}`.
- **Models (Plan C):** superadmin `GET/POST /api/v1/admin/models`, `PATCH/DELETE /api/v1/admin/models/{id}`; `ModelOut = {id, display_name, litellm_model_name, provider_kind: "openai"|"ollama"|"openai_compatible", base_url, enabled, key_fingerprint, sync_status}`; `POST/PATCH` accept write-only `api_key` (stored via the secrets module — the Secrets UI is implicit in the model form). Non-admin picker list: `GET /api/v1/models` (enabled models for any authenticated user).
- Existing now: `POST /auth/login {email,password}` → `{access_token}` + httpOnly `refresh_token` cookie scoped to `/api/v1/auth`; `POST /auth/refresh`; `POST /auth/logout`; `POST /auth/invitations {email, role}` → `{invite_token}` (admin); `POST /auth/invitations/accept {token, password≥12}`; `GET /users`, `PATCH /users/{id} {active?, role?}` (admin); `GET/POST /workspaces`, `POST /workspaces/{id}/members`. Access JWT payload carries `sub`, `org`, `role`, `exp`.

## Global Constraints

- Specs: `docs/superpowers/specs/2026-07-18-openrag-frontend-theme-design.md` (THE theme contract — re-read before Task 2), `...-openrag-phase1-design.md` §5 (pages), `...-openrag-engineering-foundation-design.md` (iron rules; rule 5: model output is untrusted — sanitized markdown only, **never** `dangerouslySetInnerHTML` and no raw-HTML pass-through).
- **Theme tokens only.** Components use semantic Tailwind classes mapped to CSS variables (`bg-subtle`, `text-muted`, `text-accent`, …). Raw palette classes (`bg-gray-100`, `text-indigo-600`) are an ESLint **error** in `src/features` and `src/components` (Task 2). `--text-muted` is for meta text only (WCAG AA).
- **TanStack Query for ALL server state.** No fetch-in-`useEffect` for server data. The single exception is the one-shot session-restore refresh in `RequireAuth` (bootstrap, not server state — documented in Task 5).
- Every page keyboard-reachable; visible focus ring = `--accent`, 2px offset (global rule in Task 2). Status never by color alone — pills carry text.
- TDD adapted to frontend: pure logic (tree selector, SSE parser, auth store/refresh, format helpers) is strictly test-first. Rendering tasks lead with a component test where stated; pure visual wiring steps verify by `pnpm lint && pnpm typecheck` + the task's listed manual check. Every task states its verification commands explicitly.
- All commands run from `frontend/` with pnpm unless stated. Conventional Commits. `pnpm lint && pnpm typecheck && pnpm test` must pass before every commit.
- Components stay small (theme spec / foundation file-size discipline): one component per file; a file crossing ~150 lines is a split signal.

---

### Task 1: Frontend scaffold and tooling

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/vite.config.ts`, `frontend/eslint.config.js`, `frontend/.prettierrc.json`, `frontend/.prettierignore`, `frontend/.gitignore`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/app.tsx`, `frontend/src/test/setup.ts`, `frontend/src/app.test.tsx`, empty dirs `frontend/src/{features,components,lib,api}/.gitkeep`

**Interfaces:**
- Produces: pnpm scripts `dev`, `build`, `test`, `test:watch`, `lint`, `format`, `typecheck`, `generate:api`, `e2e`; path alias `@/*` → `src/*`; dev proxy `/api` → `http://localhost:8000`; vitest + Testing Library wired (jsdom, globals).

- [ ] **Step 1: Create `frontend/package.json`**

```json
{
  "name": "openrag-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest",
    "lint": "eslint .",
    "format": "prettier --write .",
    "typecheck": "tsc --noEmit",
    "generate:api": "openapi-typescript http://localhost:8000/api/openapi.json -o src/api/schema.d.ts",
    "e2e": "playwright test"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.30.0"
  },
  "devDependencies": {
    "@eslint/js": "^9.12.0",
    "@testing-library/jest-dom": "^6.5.0",
    "@testing-library/react": "^16.0.1",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^18.3.10",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.2",
    "eslint": "^9.12.0",
    "eslint-plugin-react-hooks": "^5.0.0",
    "globals": "^15.10.0",
    "jsdom": "^25.0.1",
    "prettier": "^3.3.3",
    "typescript": "~5.6.2",
    "typescript-eslint": "^8.8.0",
    "vite": "^6.0.0",
    "vitest": "^3.0.0"
  }
}
```

(react-router v6, not v7: v6 is the long-stable API and v7's framework-mode adds nothing we need. Vite 6 + vitest 3 is the supported pairing.)

- [ ] **Step 2: Create `frontend/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedIndexedAccess": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "noEmit": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"],
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }
  },
  "include": ["src", "e2e", "vite.config.ts", "playwright.config.ts"]
}
```

`frontend/tsconfig.node.json` is not needed with a single config that includes `vite.config.ts`; skip it (delete from Files list if your generator added one).

- [ ] **Step 3: Create `frontend/vite.config.ts`**

```ts
import react from '@vitejs/plugin-react';
import path from 'node:path';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    css: false,
    exclude: ['e2e/**', 'node_modules/**'],
  },
});
```

- [ ] **Step 4: Create `frontend/eslint.config.js`**

```js
import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: ['dist/**', 'src/api/schema.d.ts', 'playwright-report/**', 'test-results/**'],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: { globals: { ...globals.browser } },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
);
```

(The raw-palette-class rule is appended in Task 2 once the token system exists.)

- [ ] **Step 5: Create the remaining config + entry files**

`frontend/.prettierrc.json`:

```json
{ "singleQuote": true, "printWidth": 100 }
```

`frontend/.prettierignore`:

```
dist
pnpm-lock.yaml
src/api/schema.d.ts
playwright-report
test-results
```

`frontend/.gitignore`:

```
node_modules
dist
playwright-report
test-results
```

`frontend/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>OpenRAG</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/src/main.tsx`:

```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';

import { App } from './app';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

`frontend/src/app.tsx`:

```tsx
export function App() {
  return <h1>OpenRAG</h1>;
}
```

`frontend/src/test/setup.ts`:

```ts
import '@testing-library/jest-dom/vitest';
```

`frontend/src/app.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';

import { App } from './app';

test('renders app shell placeholder', () => {
  render(<App />);
  expect(screen.getByRole('heading', { name: 'OpenRAG' })).toBeInTheDocument();
});
```

Create empty `.gitkeep` files in `src/features`, `src/components`, `src/lib`, `src/api`.

- [ ] **Step 6: Install and run all gates**

Run: `cd frontend && pnpm install && pnpm test && pnpm lint && pnpm typecheck && pnpm build`
Expected: 1 test passed; lint clean; tsc clean; `dist/` produced.

- [ ] **Step 7: Commit**

```bash
git add frontend/
git commit -m "feat: scaffold frontend with vite, react 18, ts strict, vitest, eslint"
```

---

### Task 2: Theme tokens, Tailwind mapping, Inter, palette lint-block

**Files:**
- Create: `frontend/src/styles/tokens.css`, `frontend/src/styles/globals.css`, `frontend/tailwind.config.ts`, `frontend/postcss.config.js`, `frontend/src/styles/tokens.test.ts`
- Modify: `frontend/src/main.tsx` (font + css imports), `frontend/eslint.config.js` (palette rule), `frontend/package.json` (deps)

**Interfaces:**
- Produces: semantic Tailwind classes consumed by every later task — surfaces `bg-bg | bg-sidebar | bg-subtle | bg-raised`; borders `border-line | border-line-strong | border-line-faint`; text `text-ink | text-secondary | text-muted` (muted = meta only); accent `text-accent | bg-accent-soft | text-accent-on-soft | ring-accent`; status `text-success bg-success-soft`, `text-danger bg-danger-soft`, `text-warning bg-warning-soft`; primary buttons `bg-primary text-primary-foreground` (inverted per theme spec §2.2); radii `rounded-sm(6) rounded-md(8) rounded-lg(10) rounded-xl(16) rounded-full`; `shadow-soft`; `max-w-thread` (720px); global `:focus-visible` accent ring.

- [ ] **Step 1: Add dependencies**

Run: `pnpm add @fontsource/inter && pnpm add -D tailwindcss@^3.4.13 postcss@^8.4.47 autoprefixer@^10.4.20`

- [ ] **Step 2: Write the failing token-contract test**

`frontend/src/styles/tokens.test.ts` — guards the exact theme-spec hex values against drift:

```ts
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8');
const [lightBlock = '', darkBlock = ''] = css.split('.dark');

const LIGHT: Record<string, string> = {
  '--bg': '#ffffff',
  '--bg-sidebar': '#f9f9f9',
  '--bg-subtle': '#f4f4f5',
  '--bg-raised': '#fafafa',
  '--border': '#ececec',
  '--border-faint': '#f1f1f1',
  '--text': '#171717',
  '--text-secondary': '#555555',
  '--text-muted': '#8a8a8a',
  '--accent': '#4f46e5',
  '--accent-soft': '#eef2ff',
  '--success': '#059669',
  '--success-soft': '#ecfdf5',
  '--danger': '#dc2626',
  '--danger-soft': '#fef2f2',
  '--warning': '#b45309',
  '--warning-soft': '#fffbeb',
};

const DARK: Record<string, string> = {
  '--bg': '#181818',
  '--bg-sidebar': '#111113',
  '--bg-subtle': '#26262a',
  '--bg-raised': '#1d1d20',
  '--border': '#26262a',
  '--border-strong': '#313136',
  '--text': '#ececec',
  '--text-secondary': '#a7a7ad',
  '--text-muted': '#7a7a80',
  '--accent': '#818cf8',
  '--accent-on-soft': '#a5b4fc',
  '--accent-soft': 'rgba(129, 140, 248, 0.18)',
};

test.each(Object.entries(LIGHT))('light token %s = %s', (name, value) => {
  expect(lightBlock).toContain(`${name}: ${value}`);
});

test.each(Object.entries(DARK))('dark token %s = %s', (name, value) => {
  expect(darkBlock).toContain(`${name}: ${value}`);
});

test('radii per theme spec §2.4', () => {
  for (const decl of ['--r-sm: 6px', '--r-md: 8px', '--r-lg: 10px', '--r-xl: 16px']) {
    expect(lightBlock).toContain(decl);
  }
});
```

Run: `pnpm test src/styles` — Expected: FAIL (`tokens.css` missing).

- [ ] **Step 3: Create `frontend/src/styles/tokens.css`** (exact theme spec §2.1/§2.2 values)

```css
/* Design tokens — the single source of truth (theme spec §2).
   White-label rule (§4): org branding may rewrite --accent/--accent-soft (+ dark
   variants) only. Components never reference raw accent hexes. */

:root {
  --bg: #ffffff;
  --bg-sidebar: #f9f9f9;
  --bg-subtle: #f4f4f5;
  --bg-raised: #fafafa;
  --border: #ececec;
  --border-strong: #e0e0e0; /* derived: one step past --border; dark value is spec'd */
  --border-faint: #f1f1f1;
  --text: #171717;
  --text-secondary: #555555;
  --text-muted: #8a8a8a; /* meta/decorative only — below AA for body copy */
  --accent: #4f46e5;
  --accent-soft: #eef2ff;
  --accent-on-soft: #4f46e5; /* text on accent-soft; dark mode overrides */
  --success: #059669;
  --success-soft: #ecfdf5;
  --danger: #dc2626;
  --danger-soft: #fef2f2;
  --warning: #b45309;
  --warning-soft: #fffbeb;

  /* shape & elevation (theme spec §2.4) */
  --r-sm: 6px;
  --r-md: 8px;
  --r-lg: 10px;
  --r-xl: 16px;
  --shadow-soft: 0 1px 2px rgba(0, 0, 0, 0.03);
}

.dark {
  --bg: #181818;
  --bg-sidebar: #111113;
  --bg-subtle: #26262a;
  --bg-raised: #1d1d20;
  --border: #26262a;
  --border-strong: #313136;
  --border-faint: #1f1f22;
  --text: #ececec;
  --text-secondary: #a7a7ad;
  --text-muted: #7a7a80;
  --accent: #818cf8;
  --accent-soft: rgba(129, 140, 248, 0.18);
  --accent-on-soft: #a5b4fc;
  /* status: same hues one step lighter; soft = ~15% alpha overlays (spec §2.2) */
  --success: #10b981;
  --success-soft: rgba(16, 185, 129, 0.15);
  --danger: #ef4444;
  --danger-soft: rgba(239, 68, 68, 0.15);
  --warning: #d97706;
  --warning-soft: rgba(217, 119, 6, 0.15);
}
```

- [ ] **Step 4: Create Tailwind config, PostCSS config, globals**

`frontend/tailwind.config.ts`:

```ts
import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    // FULL palette replacement (not extend): raw classes like bg-gray-100 generate
    // no CSS at all. The ESLint rule below makes them a loud error too.
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      white: '#ffffff',
      bg: 'var(--bg)',
      sidebar: 'var(--bg-sidebar)',
      subtle: 'var(--bg-subtle)',
      raised: 'var(--bg-raised)',
      line: 'var(--border)',
      'line-strong': 'var(--border-strong)',
      'line-faint': 'var(--border-faint)',
      ink: 'var(--text)',
      secondary: 'var(--text-secondary)',
      muted: 'var(--text-muted)',
      accent: 'var(--accent)',
      'accent-soft': 'var(--accent-soft)',
      'accent-on-soft': 'var(--accent-on-soft)',
      success: 'var(--success)',
      'success-soft': 'var(--success-soft)',
      danger: 'var(--danger)',
      'danger-soft': 'var(--danger-soft)',
      warning: 'var(--warning)',
      'warning-soft': 'var(--warning-soft)',
      // primary buttons invert (theme spec §2.2): near-black on light, light on dark
      primary: 'var(--text)',
      'primary-foreground': 'var(--bg)',
    },
    borderRadius: {
      none: '0',
      sm: 'var(--r-sm)',
      DEFAULT: 'var(--r-md)',
      md: 'var(--r-md)',
      lg: 'var(--r-lg)',
      xl: 'var(--r-xl)',
      full: '9999px',
    },
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'JetBrains Mono', 'monospace'],
      },
      boxShadow: { soft: 'var(--shadow-soft)' },
      maxWidth: { thread: '720px' },
    },
  },
  plugins: [],
} satisfies Config;
```

`frontend/postcss.config.js`:

```js
export default {
  plugins: { tailwindcss: {}, autoprefixer: {} },
};
```

`frontend/src/styles/globals.css`:

```css
@import './tokens.css';

@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  body {
    @apply bg-bg font-sans text-[14px] text-ink antialiased;
  }
  /* Accessibility (theme spec §5): visible accent focus ring, 2px offset, everywhere */
  :focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  table {
    font-variant-numeric: tabular-nums;
  }
}
```

Update `frontend/src/main.tsx` imports (fonts self-hosted per theme spec §2.3 — no CDN):

```tsx
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import './styles/globals.css';
```

(place above the existing imports)

- [ ] **Step 5: Add the raw-palette ESLint block**

Append to `frontend/eslint.config.js` config array (mechanism: `no-restricted-syntax` with esquery regex matchers — dependency-free, works in flat config, and catches the classes in string literals and template chunks; combined with the palette replacement above, violations both fail lint and produce no CSS):

```js
  {
    files: ['src/features/**/*.{ts,tsx}', 'src/components/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector:
            'Literal[value=/\\b(?:bg|text|border|ring|outline|fill|stroke|decoration|divide|from|via|to)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}\\b/]',
          message:
            'Raw Tailwind palette class — use theme tokens (bg-subtle, text-muted, text-accent, …) per the theme spec.',
        },
        {
          selector:
            'TemplateElement[value.raw=/\\b(?:bg|text|border|ring|outline|fill|stroke|decoration|divide|from|via|to)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}\\b/]',
          message:
            'Raw Tailwind palette class — use theme tokens (bg-subtle, text-muted, text-accent, …) per the theme spec.',
        },
      ],
    },
  },
```

- [ ] **Step 6: Verify — tests, lint canary, build**

Run: `pnpm test src/styles`
Expected: all token tests PASS.

Canary — create `src/components/lint-canary.tsx` containing:

```tsx
export function Canary() {
  return <div className="bg-gray-100 text-indigo-600">x</div>;
}
```

Run: `pnpm lint`
Expected: **2 errors** ("Raw Tailwind palette class …") on `lint-canary.tsx`. Then delete the file and re-run: `pnpm lint` — clean.

Run: `pnpm typecheck && pnpm build`
Expected: clean; build output includes inlined Inter woff2 assets.

- [ ] **Step 7: Commit**

```bash
git add frontend/
git commit -m "feat: theme tokens, tailwind semantic mapping, self-hosted inter, palette lint-block"
```

---

### Task 3: UI primitives (shadcn-style, token-themed)

**Files:**
- Create: `frontend/src/lib/cn.ts`, `frontend/src/components/ui/button.tsx`, `ui/input.tsx`, `ui/label.tsx`, `ui/dialog.tsx`, `ui/dropdown-menu.tsx`, `ui/popover.tsx`, `ui/select.tsx` (native), `ui/table.tsx`, `ui/status-pill.tsx`, `ui/spinner.tsx`, `ui/toaster.tsx`
- Test: `frontend/src/components/ui/button.test.tsx`, `ui/status-pill.test.tsx`, `ui/dialog.test.tsx`

**Judgment call (record in PR):** We use shadcn/ui's copy-in model but author the components directly against our token classes instead of running the shadcn CLI. CLI output uses its own semantic names (`bg-muted`, `text-muted-foreground`) that collide with our spec-mandated names (`text-muted` = `--text-muted`) and would need a full restyle pass anyway — which the palette lint would force. Same architecture (Radix primitives + cva, code owned in-repo), zero restyle churn, and theme spec §6 ("themed exclusively through token variables") holds by construction.

**Interfaces (consumed by every later task):**
- `cn(...inputs: ClassValue[]): string`
- `<Button variant="primary"|"secondary"|"ghost"|"danger" size="sm"|"md"|"icon">` (default secondary/md; renders `<button>`, forwards ref + props)
- `<Input>` (forwards ref; token-styled `<input>`); `<Label htmlFor>`
- Dialog: `<Dialog open onOpenChange>` + `<DialogContent title description?>` (renders overlay, panel `rounded-lg`, close button; children = body; `<DialogFooter>` right-aligned)
- DropdownMenu: re-exported Radix parts `DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator`
- Popover: `Popover, PopoverTrigger, PopoverContent`
- `<NativeSelect>` (styled `<select>` — used for role/provider pickers; full Radix Select is not needed in Phase 1)
- Table: `Table, THead, TBody, TR, TH, TD` (hairline borders, `bg-raised` header, ≥10px row padding, tabular-nums)
- `<StatusPill tone="success"|"accent"|"danger"|"warning">text</StatusPill>` — pill radius, soft bg + strong text (theme spec §3)
- `<Spinner label?>` (aria-live polite); `<Toaster />` (sonner) + `toast` re-export

- [ ] **Step 1: Add dependencies**

Run: `pnpm add @radix-ui/react-dialog@^1.1.1 @radix-ui/react-dropdown-menu@^2.1.1 @radix-ui/react-popover@^1.1.1 @radix-ui/react-slot@^1.1.0 class-variance-authority@^0.7.0 clsx@^2.1.1 tailwind-merge@^2.5.2 sonner@^1.5.0 lucide-react@^0.451.0`

- [ ] **Step 2: Write failing tests**

`frontend/src/components/ui/button.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';

import { Button } from './button';

test('primary variant uses inverted primary tokens', () => {
  render(<Button variant="primary">Save</Button>);
  const btn = screen.getByRole('button', { name: 'Save' });
  expect(btn.className).toContain('bg-primary');
  expect(btn.className).toContain('text-primary-foreground');
});

test('defaults to secondary and supports disabled', () => {
  render(<Button disabled>Cancel</Button>);
  const btn = screen.getByRole('button', { name: 'Cancel' });
  expect(btn).toBeDisabled();
  expect(btn.className).toContain('border-line');
});
```

`frontend/src/components/ui/status-pill.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';

import { StatusPill } from './status-pill';

test.each([
  ['success', 'bg-success-soft text-success'],
  ['accent', 'bg-accent-soft text-accent-on-soft'],
  ['danger', 'bg-danger-soft text-danger'],
  ['warning', 'bg-warning-soft text-warning'],
] as const)('tone %s applies soft bg + strong text', (tone, expected) => {
  render(<StatusPill tone={tone}>Indexed</StatusPill>);
  const pill = screen.getByText('Indexed');
  for (const cls of expected.split(' ')) expect(pill.className).toContain(cls);
  expect(pill.className).toContain('rounded-full');
});
```

`frontend/src/components/ui/dialog.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

import { Button } from './button';
import { Dialog, DialogContent } from './dialog';

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Open</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title="Confirm delete" description="This cannot be undone.">
          <p>body</p>
        </DialogContent>
      </Dialog>
    </>
  );
}

test('opens with accessible title and closes via close button', async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.click(screen.getByRole('button', { name: 'Open' }));
  expect(screen.getByRole('dialog', { name: 'Confirm delete' })).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Close' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
```

Run: `pnpm test src/components/ui` — Expected: FAIL (modules missing).

- [ ] **Step 3: Implement `frontend/src/lib/cn.ts`**

```ts
import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
```

- [ ] **Step 4: Implement the primitives**

`frontend/src/components/ui/button.tsx`:

```tsx
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { forwardRef, type ButtonHTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        primary: 'bg-primary text-primary-foreground hover:opacity-90',
        secondary: 'border border-line bg-bg text-ink hover:bg-subtle',
        ghost: 'text-secondary hover:bg-subtle hover:text-ink',
        danger: 'bg-danger text-white hover:opacity-90',
      },
      size: {
        sm: 'h-7 px-2 text-[12px]',
        md: 'h-8 px-3 text-[13px]',
        icon: 'h-7 w-7',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp
        ref={ref}
        type={asChild ? undefined : (type ?? 'button')}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      />
    );
  },
);
Button.displayName = 'Button';
```

`frontend/src/components/ui/input.tsx`:

```tsx
import { forwardRef, type InputHTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        'h-8 w-full rounded-md border border-line bg-bg px-2.5 text-[13px] text-ink',
        'placeholder:text-muted focus-visible:border-accent',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = 'Input';
```

`frontend/src/components/ui/label.tsx`:

```tsx
import { type LabelHTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    // eslint-disable-next-line jsx-a11y/label-has-associated-control -- htmlFor supplied by callers
    <label className={cn('mb-1 block text-[12px] font-medium text-secondary', className)} {...props} />
  );
}
```

(If `jsx-a11y` is not installed the disable comment is inert; keep it for future lint hardening.)

`frontend/src/components/ui/dialog.tsx`:

```tsx
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { type ReactNode } from 'react';

import { cn } from '@/lib/cn';

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;

export function DialogContent({
  title,
  description,
  children,
  className,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-ink/40" />
      <DialogPrimitive.Content
        className={cn(
          'fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2',
          'rounded-lg border border-line bg-bg p-5 shadow-soft',
          className,
        )}
      >
        <DialogPrimitive.Title className="text-[16px] font-semibold text-ink">
          {title}
        </DialogPrimitive.Title>
        {description ? (
          <DialogPrimitive.Description className="mt-1 text-[13px] text-secondary">
            {description}
          </DialogPrimitive.Description>
        ) : null}
        <div className="mt-4">{children}</div>
        <DialogPrimitive.Close
          aria-label="Close"
          className="absolute right-3 top-3 rounded-sm p-1 text-secondary hover:bg-subtle hover:text-ink"
        >
          <X className="h-4 w-4" aria-hidden />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export function DialogFooter({ children }: { children: ReactNode }) {
  return <div className="mt-5 flex justify-end gap-2">{children}</div>;
}
```

`frontend/src/components/ui/dropdown-menu.tsx`:

```tsx
import * as Menu from '@radix-ui/react-dropdown-menu';
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from 'react';

import { cn } from '@/lib/cn';

export const DropdownMenu = Menu.Root;
export const DropdownMenuTrigger = Menu.Trigger;

export const DropdownMenuContent = forwardRef<
  ElementRef<typeof Menu.Content>,
  ComponentPropsWithoutRef<typeof Menu.Content>
>(({ className, sideOffset = 4, ...props }, ref) => (
  <Menu.Portal>
    <Menu.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        'z-50 min-w-[180px] rounded-md border border-line bg-bg p-1 shadow-soft',
        className,
      )}
      {...props}
    />
  </Menu.Portal>
));
DropdownMenuContent.displayName = 'DropdownMenuContent';

export const DropdownMenuItem = forwardRef<
  ElementRef<typeof Menu.Item>,
  ComponentPropsWithoutRef<typeof Menu.Item>
>(({ className, ...props }, ref) => (
  <Menu.Item
    ref={ref}
    className={cn(
      'cursor-default select-none rounded-sm px-2 py-1.5 text-[13px] text-ink outline-none',
      'data-[highlighted]:bg-subtle data-[disabled]:opacity-50',
      className,
    )}
    {...props}
  />
));
DropdownMenuItem.displayName = 'DropdownMenuItem';

export const DropdownMenuSeparator = ({ className }: { className?: string }) => (
  <Menu.Separator className={cn('my-1 h-px bg-line-faint', className)} />
);
```

`frontend/src/components/ui/popover.tsx`:

```tsx
import * as PopoverPrimitive from '@radix-ui/react-popover';
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from 'react';

import { cn } from '@/lib/cn';

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;

export const PopoverContent = forwardRef<
  ElementRef<typeof PopoverPrimitive.Content>,
  ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>
>(({ className, sideOffset = 4, ...props }, ref) => (
  <PopoverPrimitive.Portal>
    <PopoverPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        'z-50 max-w-sm rounded-md border border-line bg-bg p-3 text-[13px] text-ink shadow-soft',
        className,
      )}
      {...props}
    />
  </PopoverPrimitive.Portal>
));
PopoverContent.displayName = 'PopoverContent';
```

`frontend/src/components/ui/select.tsx`:

```tsx
import { forwardRef, type SelectHTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

export const NativeSelect = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        'h-8 w-full rounded-md border border-line bg-bg px-2 text-[13px] text-ink',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    >
      {children}
    </select>
  ),
);
NativeSelect.displayName = 'NativeSelect';
```

`frontend/src/components/ui/table.tsx`:

```tsx
import { type HTMLAttributes, type TdHTMLAttributes, type ThHTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className={cn('w-full text-[13px] tabular-nums', className)} {...props} />
    </div>
  );
}

export function THead(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className="bg-raised text-left" {...props} />;
}

export function TBody(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody {...props} />;
}

export function TR({ className, ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn('border-t border-line-faint first:border-t-0', className)} {...props} />;
}

export function TH({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn('px-3 py-2.5 text-[12px] font-medium text-secondary', className)}
      {...props}
    />
  );
}

export function TD({ className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn('px-3 py-2.5 text-ink', className)} {...props} />;
}
```

`frontend/src/components/ui/status-pill.tsx`:

```tsx
import { type ReactNode } from 'react';

import { cn } from '@/lib/cn';

const TONES = {
  success: 'bg-success-soft text-success',
  accent: 'bg-accent-soft text-accent-on-soft',
  danger: 'bg-danger-soft text-danger',
  warning: 'bg-warning-soft text-warning',
} as const;

export type StatusTone = keyof typeof TONES;

export function StatusPill({
  tone,
  className,
  children,
}: {
  tone: StatusTone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-[12px] font-medium',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
```

`frontend/src/components/ui/spinner.tsx`:

```tsx
import { Loader2 } from 'lucide-react';

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2 text-secondary">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      <span className="text-[13px]">{label}</span>
    </div>
  );
}
```

`frontend/src/components/ui/toaster.tsx`:

```tsx
import { Toaster as SonnerToaster } from 'sonner';

export { toast } from 'sonner';

export function Toaster() {
  return (
    <SonnerToaster
      position="bottom-right"
      toastOptions={{
        style: {
          background: 'var(--bg)',
          color: 'var(--text)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--r-md)',
        },
      }}
    />
  );
}
```

- [ ] **Step 5: Run tests and gates**

Run: `pnpm test src/components/ui && pnpm lint && pnpm typecheck`
Expected: all PASS (the palette lint also validates every component above uses tokens only).

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat: token-themed ui primitives (button, input, dialog, menus, table, pills, toaster)"
```

---

### Task 4: Generated API client, auth store, single-flight refresh

**Files:**
- Create: `frontend/src/lib/auth-store.ts`, `frontend/src/lib/jwt.ts`, `frontend/src/lib/query-client.ts`, `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/api/schema.d.ts` (generated, committed)
- Test: `frontend/src/lib/auth-store.test.ts`, `frontend/src/lib/jwt.test.ts`, `frontend/src/api/client.test.ts`

**Judgment call (record in PR):** openapi-typescript + openapi-fetch over orval — zero runtime codegen (openapi-fetch is a 6 kB typed wrapper over `fetch`), the generated artifact is a single ambient `.d.ts` (trivially diffable, no generated hooks to fight), and TanStack Query wrappers stay hand-written per feature which keeps cache-key policy explicit.

**Interfaces:**
- `auth-store`: `getAccessToken(): string | null`, `setAccessToken(t: string | null): void`, `subscribeAuth(fn: () => void): () => void` (module-level in-memory store — access token never touches localStorage).
- `jwt`: `decodeClaims(token: string): { sub: string; org: string; role: 'superadmin' | 'admin' | 'user'; exp: number } | null` (payload decode only, no verification — display/role-gating hints; the server is the authority).
- `client`: `api` — `openapi-fetch` client over `paths` (`api.GET('/api/v1/workspaces')`, …); `authFetch(req: Request): Promise<Response>` (used directly by SSE + XHR-free flows); `refreshAccessToken(): Promise<boolean>` (single-flight); `setOnAuthFailure(fn: () => void)`.
- 401 policy: any 401 (except on `/auth/login`, `/auth/refresh`, `/auth/invitations/accept`) triggers one cookie-based refresh shared across concurrent callers; success → retry original request once; failure → clear token + `onAuthFailure()` (wired to redirect to `/login` in Task 5).
- `types.ts`: alias layer — `UserOut`, `WorkspaceOut`, `DocumentOut`, `ChatOut`, `ChatDetailOut`, `MessageOut`, `ModelOut` from `components['schemas']`, plus hand-typed SSE payloads `SourceRef`, `CitationRef`, `DoneInfo` (SSE is outside OpenAPI).

- [ ] **Step 1: Add dependencies and generate the schema**

Run: `pnpm add openapi-fetch@^0.13.0 @tanstack/react-query@^5.59.0 && pnpm add -D openapi-typescript@^7.4.0`

Backend must be running (Plans A–C executed): from repo root `docker compose -f deploy/compose.yaml up -d` and `cd backend && uv run uvicorn --factory openrag.api.app:create_app --port 8000 &`.

Run: `pnpm generate:api`
Expected: `src/api/schema.d.ts` created, containing `'/api/v1/auth/login'`, `'/api/v1/workspaces'`, `'/api/v1/chats/{chat_id}'`, `'/api/v1/admin/models'` path keys. **Reconcile now:** open the file and confirm the schema names assumed in `types.ts` (Step 4); fix aliases there if Plans B/C named them differently.

- [ ] **Step 2: Write failing tests**

`frontend/src/lib/auth-store.test.ts`:

```ts
import { getAccessToken, setAccessToken, subscribeAuth } from './auth-store';

afterEach(() => setAccessToken(null));

test('stores and clears the token in memory', () => {
  expect(getAccessToken()).toBeNull();
  setAccessToken('tok');
  expect(getAccessToken()).toBe('tok');
  setAccessToken(null);
  expect(getAccessToken()).toBeNull();
});

test('notifies subscribers and supports unsubscribe', () => {
  const seen: (string | null)[] = [];
  const unsub = subscribeAuth(() => seen.push(getAccessToken()));
  setAccessToken('a');
  unsub();
  setAccessToken('b');
  expect(seen).toEqual(['a']);
});
```

`frontend/src/lib/jwt.test.ts`:

```ts
import { decodeClaims } from './jwt';

function fakeJwt(payload: object): string {
  const b64 = (o: object) =>
    btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64({ alg: 'HS256' })}.${b64(payload)}.sig`;
}

test('decodes sub, org, role, exp', () => {
  const claims = decodeClaims(fakeJwt({ sub: 'u1', org: 'o1', role: 'admin', exp: 123 }));
  expect(claims).toEqual({ sub: 'u1', org: 'o1', role: 'admin', exp: 123 });
});

test('returns null for garbage', () => {
  expect(decodeClaims('not-a-jwt')).toBeNull();
  expect(decodeClaims('a.%%%.c')).toBeNull();
});
```

`frontend/src/api/client.test.ts`:

```ts
import { setAccessToken } from '@/lib/auth-store';

import { authFetch, refreshAccessToken, setOnAuthFailure } from './client';

function res(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
  setOnAuthFailure(() => {});
});

test('attaches bearer token', async () => {
  setAccessToken('tok-1');
  const fetchMock = vi.fn(async (req: Request) => {
    expect(req.headers.get('authorization')).toBe('Bearer tok-1');
    return res(200);
  });
  vi.stubGlobal('fetch', fetchMock);
  const r = await authFetch(new Request('http://x/api/v1/workspaces'));
  expect(r.status).toBe(200);
});

test('401 → refresh → retry succeeds; concurrent 401s share ONE refresh', async () => {
  setAccessToken('stale');
  let refreshCalls = 0;
  const fetchMock = vi.fn(async (req: Request) => {
    const url = typeof req === 'string' ? req : req.url;
    if (url.includes('/auth/refresh')) {
      refreshCalls += 1;
      await new Promise((r) => setTimeout(r, 10)); // widen the race window
      return res(200, { access_token: 'fresh' });
    }
    return req.headers.get('authorization') === 'Bearer fresh' ? res(200) : res(401);
  });
  vi.stubGlobal('fetch', fetchMock);

  const [a, b] = await Promise.all([
    authFetch(new Request('http://x/api/v1/workspaces')),
    authFetch(new Request('http://x/api/v1/users')),
  ]);
  expect(a.status).toBe(200);
  expect(b.status).toBe(200);
  expect(refreshCalls).toBe(1);
});

test('refresh failure clears token and fires onAuthFailure', async () => {
  setAccessToken('stale');
  const onFail = vi.fn();
  setOnAuthFailure(onFail);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) =>
      req.url.includes('/auth/refresh') ? res(401) : res(401),
    ),
  );
  const r = await authFetch(new Request('http://x/api/v1/workspaces'));
  expect(r.status).toBe(401);
  expect(onFail).toHaveBeenCalledOnce();
  expect(await refreshAccessToken()).toBe(false);
});

test('401 on auth endpoints does NOT trigger refresh', async () => {
  const fetchMock = vi.fn(async () => res(401));
  vi.stubGlobal('fetch', fetchMock);
  await authFetch(new Request('http://x/api/v1/auth/login', { method: 'POST' }));
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
```

Run: `pnpm test src/lib src/api` — Expected: FAIL (modules missing).

- [ ] **Step 3: Implement stores and helpers**

`frontend/src/lib/auth-store.ts`:

```ts
// Access token lives in module memory only (never localStorage): XSS cannot read
// what is not persisted, and the httpOnly refresh cookie restores sessions.
let accessToken: string | null = null;
const listeners = new Set<() => void>();

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
  for (const listener of listeners) listener();
}

export function subscribeAuth(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
```

`frontend/src/lib/jwt.ts`:

```ts
export interface AccessClaims {
  sub: string;
  org: string;
  role: 'superadmin' | 'admin' | 'user';
  exp: number;
}

/** Payload decode only — no signature verification. UI hinting; the server decides. */
export function decodeClaims(token: string): AccessClaims | null {
  const part = token.split('.')[1];
  if (!part) return null;
  try {
    const json = atob(part.replace(/-/g, '+').replace(/_/g, '/'));
    const payload = JSON.parse(json) as Record<string, unknown>;
    if (
      typeof payload.sub !== 'string' ||
      typeof payload.org !== 'string' ||
      typeof payload.role !== 'string' ||
      typeof payload.exp !== 'number'
    ) {
      return null;
    }
    return {
      sub: payload.sub,
      org: payload.org,
      role: payload.role as AccessClaims['role'],
      exp: payload.exp,
    };
  } catch {
    return null;
  }
}
```

`frontend/src/lib/query-client.ts`:

```ts
import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 30_000, refetchOnWindowFocus: false },
  },
});
```

- [ ] **Step 4: Implement the client**

`frontend/src/api/client.ts`:

```ts
import createClient from 'openapi-fetch';

import { getAccessToken, setAccessToken } from '@/lib/auth-store';

import type { paths } from './schema';

let onAuthFailure: () => void = () => {};

export function setOnAuthFailure(fn: () => void): void {
  onAuthFailure = fn;
}

// Single-flight: concurrent 401s share one refresh round-trip.
let refreshInFlight: Promise<boolean> | null = null;

export function refreshAccessToken(): Promise<boolean> {
  refreshInFlight ??= doRefresh().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

async function doRefresh(): Promise<boolean> {
  const res = await fetch('/api/v1/auth/refresh', { method: 'POST', credentials: 'include' });
  if (!res.ok) {
    setAccessToken(null);
    return false;
  }
  const body = (await res.json()) as { access_token: string };
  setAccessToken(body.access_token);
  return true;
}

// Endpoints where a 401 is a real answer, not an expired access token.
const NO_REFRESH = ['/api/v1/auth/login', '/api/v1/auth/refresh', '/api/v1/auth/invitations/accept'];

export async function authFetch(input: Request): Promise<Response> {
  const send = (): Promise<Response> => {
    const req = input.clone();
    const token = getAccessToken();
    if (token) req.headers.set('Authorization', `Bearer ${token}`);
    return fetch(req);
  };
  let res = await send();
  if (res.status === 401 && !NO_REFRESH.some((p) => input.url.includes(p))) {
    if (await refreshAccessToken()) {
      res = await send();
    } else {
      onAuthFailure();
    }
  }
  return res;
}

export const api = createClient<paths>({
  baseUrl: '/',
  credentials: 'include',
  fetch: authFetch,
});
```

`frontend/src/api/types.ts`:

```ts
// The ONLY file allowed to reach into components['schemas']. If a generated name
// differs from an assumption, fix the alias here — nowhere else.
import type { components } from './schema';

export type UserOut = components['schemas']['UserOut'];
export type WorkspaceOut = components['schemas']['WorkspaceOut'];
export type DocumentOut = components['schemas']['DocumentOut'];
export type ChatOut = components['schemas']['ChatOut'];
export type ChatDetailOut = components['schemas']['ChatDetailOut'];
export type MessageOut = components['schemas']['MessageOut'];
export type ModelOut = components['schemas']['ModelOut'];

export type DocumentStatus = DocumentOut['status'];

// --- SSE payloads (outside OpenAPI; Plan C event schema) ---

export interface SourceRef {
  n: number;
  document_id: string;
  filename: string;
  page: number | null;
  score: number;
}

export interface CitationRef {
  n: number;
  document_id: string;
  page: number | null;
}

export interface DoneInfo {
  message_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  no_answer: boolean;
}
```

- [ ] **Step 5: Run tests and gates**

Run: `pnpm test src/lib src/api && pnpm lint && pnpm typecheck`
Expected: all PASS. If `typecheck` fails inside `types.ts`, the generated schema names differ — reconcile the aliases (and note the real names in the commit body), do not touch consumers.

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat: generated openapi client, in-memory auth store, single-flight cookie refresh"
```

---

### Task 5: Router, protected routes, login and accept-invite pages

**Files:**
- Create: `frontend/src/app/router.tsx`, `frontend/src/app/require-auth.tsx`, `frontend/src/features/auth/mutations.ts`, `frontend/src/features/auth/login-page.tsx`, `frontend/src/features/auth/accept-invite-page.tsx`, `frontend/src/features/auth/auth-card.tsx`
- Modify: `frontend/src/app.tsx` (providers + router), delete `frontend/src/app.test.tsx` placeholder assertions (replace, below)
- Test: `frontend/src/features/auth/login-page.test.tsx`, `frontend/src/features/auth/accept-invite-page.test.tsx`, `frontend/src/app/require-auth.test.tsx`

**Interfaces:**
- Routes: `/login`, `/invite` (`?token=`), and under `<RequireAuth>`: `/` → redirect `/chat`, `/chat`, `/chat/:chatId`, `/documents`, `/admin/users`, `/admin/models` (shell layout arrives Task 6; until then protected routes render a plain `<Outlet/>` parent).
- `useLogin(): UseMutationResult` — POST `/auth/login`, on success `setAccessToken`, navigate `/chat`.
- `useLogout()` — POST `/auth/logout`, clear token, clear query cache, navigate `/login`.
- `useAcceptInvite()` — POST `/auth/invitations/accept`.
- `<RequireAuth/>`: token present → render; else one-shot `refreshAccessToken()` session restore (documented exception to the no-fetch-in-effect rule: bootstrap, not server data), spinner while checking, `<Navigate to="/login">` on failure. Also wires `setOnAuthFailure` → navigate `/login`.
- `problemDetail(body: unknown): string` — extracts RFC 9457 `detail` for display (in `mutations.ts`, exported).

- [ ] **Step 1: Write failing tests**

`frontend/src/app/require-auth.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { setAccessToken } from '@/lib/auth-store';

import { RequireAuth } from './require-auth';

function renderProtected() {
  return render(
    <MemoryRouter initialEntries={['/secret']}>
      <Routes>
        <Route path="/login" element={<div>login page</div>} />
        <Route element={<RequireAuth />}>
          <Route path="/secret" element={<div>secret page</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('renders children when a token exists', () => {
  setAccessToken('tok');
  renderProtected();
  expect(screen.getByText('secret page')).toBeInTheDocument();
});

test('restores session via refresh cookie when no token', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ access_token: 'restored' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  );
  renderProtected();
  expect(await screen.findByText('secret page')).toBeInTheDocument();
});

test('redirects to /login when refresh fails', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
  renderProtected();
  expect(await screen.findByText('login page')).toBeInTheDocument();
});
```

`frontend/src/features/auth/login-page.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { getAccessToken, setAccessToken } from '@/lib/auth-store';

import { LoginPage } from './login-page';

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('successful login stores the access token', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ access_token: 'tok-9' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  );
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('Email'), 'a@acme.com');
  await user.type(screen.getByLabelText('Password'), 'pw123456');
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
  await vi.waitFor(() => expect(getAccessToken()).toBe('tok-9'));
});

test('shows problem+json detail on 401', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(
        JSON.stringify({ title: 'Authentication failed', detail: 'invalid credentials', status: 401 }),
        { status: 401, headers: { 'content-type': 'application/problem+json' } },
      ),
    ),
  );
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('Email'), 'a@acme.com');
  await user.type(screen.getByLabelText('Password'), 'wrong');
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('invalid credentials');
});
```

`frontend/src/features/auth/accept-invite-page.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { AcceptInvitePage } from './accept-invite-page';

function renderPage(url = '/invite?token=inv-tok') {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[url]}>
        <AcceptInvitePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test('rejects passwords under 12 characters client-side', async () => {
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('Password'), 'short');
  await user.type(screen.getByLabelText('Confirm password'), 'short');
  await user.click(screen.getByRole('button', { name: 'Set password' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('at least 12 characters');
});

test('rejects mismatched confirmation', async () => {
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('Password'), 'a-long-password-1');
  await user.type(screen.getByLabelText('Confirm password'), 'a-long-password-2');
  await user.click(screen.getByRole('button', { name: 'Set password' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('do not match');
});

test('missing token shows an error state, not a form', () => {
  renderPage('/invite');
  expect(screen.getByText(/invitation link is invalid/i)).toBeInTheDocument();
});
```

Run: `pnpm test src/features/auth src/app` — Expected: FAIL.

- [ ] **Step 2: Implement mutations**

`frontend/src/features/auth/mutations.ts`:

```ts
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import { api } from '@/api/client';
import { setAccessToken } from '@/lib/auth-store';

export function problemDetail(body: unknown): string {
  if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
    return body.detail || 'Request failed';
  }
  return 'Request failed';
}

export function useLogin() {
  const navigate = useNavigate();
  return useMutation({
    mutationFn: async (creds: { email: string; password: string }) => {
      const { data, error } = await api.POST('/api/v1/auth/login', { body: creds });
      if (error) throw new Error(problemDetail(error));
      return data;
    },
    onSuccess: (data) => {
      setAccessToken(data.access_token);
      navigate('/chat', { replace: true });
    },
  });
}

export function useLogout() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await api.POST('/api/v1/auth/logout');
    },
    onSettled: () => {
      setAccessToken(null);
      queryClient.clear();
      navigate('/login', { replace: true });
    },
  });
}

export function useAcceptInvite() {
  return useMutation({
    mutationFn: async (body: { token: string; password: string }) => {
      const { data, error } = await api.POST('/api/v1/auth/invitations/accept', { body });
      if (error) throw new Error(problemDetail(error));
      return data;
    },
  });
}
```

- [ ] **Step 3: Implement pages**

`frontend/src/features/auth/auth-card.tsx` (shared minimal themed frame):

```tsx
import { type ReactNode } from 'react';

export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-sidebar">
      <div className="w-full max-w-sm rounded-lg border border-line bg-bg p-6 shadow-soft">
        <div className="mb-5 flex items-center gap-2">
          <span aria-hidden className="h-5 w-5 rounded-sm bg-accent" />
          <span className="text-[16px] font-semibold tracking-[-0.01em] text-ink">OpenRAG</span>
        </div>
        <h1 className="mb-4 text-[18px] font-semibold tracking-[-0.01em] text-ink">{title}</h1>
        {children}
      </div>
    </div>
  );
}
```

`frontend/src/features/auth/login-page.tsx`:

```tsx
import { useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

import { AuthCard } from './auth-card';
import { useLogin } from './mutations';

export function LoginPage() {
  const login = useLogin();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate({ email, password });
  };

  return (
    <AuthCard title="Sign in">
      <form onSubmit={onSubmit} className="space-y-3">
        <div>
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {login.isError ? (
          <p role="alert" className="text-[12px] text-danger">
            {login.error.message}
          </p>
        ) : null}
        <Button type="submit" variant="primary" className="w-full" disabled={login.isPending}>
          Sign in
        </Button>
      </form>
    </AuthCard>
  );
}
```

`frontend/src/features/auth/accept-invite-page.tsx`:

```tsx
import { useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

import { AuthCard } from './auth-card';
import { useAcceptInvite } from './mutations';

export function AcceptInvitePage() {
  const [params] = useSearchParams();
  const token = params.get('token');
  const accept = useAcceptInvite();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [clientError, setClientError] = useState<string | null>(null);

  if (!token) {
    return (
      <AuthCard title="Accept invitation">
        <p className="text-[13px] text-secondary">
          This invitation link is invalid — it is missing its token. Ask your admin for a new
          invitation.
        </p>
      </AuthCard>
    );
  }

  if (accept.isSuccess) {
    return (
      <AuthCard title="You're in">
        <p className="text-[13px] text-secondary">Your password is set.</p>
        <Button asChild variant="primary" className="mt-4 w-full">
          <Link to="/login">Go to sign in</Link>
        </Button>
      </AuthCard>
    );
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (password.length < 12) {
      setClientError('Password must be at least 12 characters.');
      return;
    }
    if (password !== confirm) {
      setClientError('Passwords do not match.');
      return;
    }
    setClientError(null);
    accept.mutate({ token, password });
  };

  const error = clientError ?? (accept.isError ? accept.error.message : null);

  return (
    <AuthCard title="Set your password">
      <form onSubmit={onSubmit} className="space-y-3">
        <div>
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="confirm">Confirm password</Label>
          <Input
            id="confirm"
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </div>
        {error ? (
          <p role="alert" className="text-[12px] text-danger">
            {error}
          </p>
        ) : null}
        <Button type="submit" variant="primary" className="w-full" disabled={accept.isPending}>
          Set password
        </Button>
      </form>
    </AuthCard>
  );
}
```

- [ ] **Step 4: Implement RequireAuth, router, providers**

`frontend/src/app/require-auth.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { Navigate, Outlet, useNavigate } from 'react-router-dom';

import { refreshAccessToken, setOnAuthFailure } from '@/api/client';
import { Spinner } from '@/components/ui/spinner';
import { getAccessToken } from '@/lib/auth-store';

type Gate = 'checking' | 'authed' | 'anon';

export function RequireAuth() {
  const navigate = useNavigate();
  const [gate, setGate] = useState<Gate>(() => (getAccessToken() ? 'authed' : 'checking'));

  // Session-restore bootstrap (NOT server state — sanctioned useEffect exception):
  // no in-memory token yet, so try the httpOnly refresh cookie exactly once.
  useEffect(() => {
    if (gate !== 'checking') return;
    let cancelled = false;
    void refreshAccessToken().then((ok) => {
      if (!cancelled) setGate(ok ? 'authed' : 'anon');
    });
    return () => {
      cancelled = true;
    };
  }, [gate]);

  useEffect(() => {
    setOnAuthFailure(() => navigate('/login', { replace: true }));
    return () => setOnAuthFailure(() => {});
  }, [navigate]);

  if (gate === 'checking') {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Signing you in…" />
      </div>
    );
  }
  if (gate === 'anon') return <Navigate to="/login" replace />;
  return <Outlet />;
}
```

`frontend/src/app/router.tsx`:

```tsx
import { createBrowserRouter, Navigate, Outlet } from 'react-router-dom';

import { AcceptInvitePage } from '@/features/auth/accept-invite-page';
import { LoginPage } from '@/features/auth/login-page';

import { RequireAuth } from './require-auth';

// Placeholder pages are replaced as their tasks land (Tasks 6, 10, 12, 13, 14).
function ComingSoon({ name }: { name: string }) {
  return <p className="p-6 text-secondary">{name} — under construction</p>;
}

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/invite', element: <AcceptInvitePage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <Outlet />, // replaced by <AppShell /> in Task 6
        children: [
          { path: '/', element: <Navigate to="/chat" replace /> },
          { path: '/chat', element: <ComingSoon name="Chat" /> },
          { path: '/chat/:chatId', element: <ComingSoon name="Chat" /> },
          { path: '/documents', element: <ComingSoon name="Documents" /> },
          { path: '/admin/users', element: <ComingSoon name="Users" /> },
          { path: '/admin/models', element: <ComingSoon name="Models" /> },
        ],
      },
    ],
  },
]);
```

Replace `frontend/src/app.tsx`:

```tsx
import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from 'react-router-dom';

import { Toaster } from '@/components/ui/toaster';
import { queryClient } from '@/lib/query-client';

import { router } from './app/router';

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>
  );
}
```

Replace `frontend/src/app.test.tsx` with a smoke that the unauthenticated app lands on login:

```tsx
import { render, screen } from '@testing-library/react';

import { App } from './app';

test('unauthenticated app redirects to the login page', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
  window.history.pushState({}, '', '/');
  render(<App />);
  expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  vi.unstubAllGlobals();
});
```

- [ ] **Step 5: Run tests, gates, and a manual smoke**

Run: `pnpm test && pnpm lint && pnpm typecheck`
Expected: all PASS.

Manual (backend running): `pnpm dev`, open `http://localhost:5173` → redirected to `/login`; sign in with the bootstrap superadmin → lands on `/chat` placeholder; reload the page → session restores without re-login (cookie refresh).

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat: router with protected routes, login and accept-invite pages, session restore"
```

---

### Task 6: App shell — sidebar, top bar, theme toggle, workspace context, role gate

**Files:**
- Create: `frontend/src/lib/theme.ts`, `frontend/src/lib/use-claims.ts`, `frontend/src/features/workspaces/queries.ts`, `frontend/src/features/workspaces/workspace-context.tsx`, `frontend/src/features/chat/queries.ts` (chat list only; extended in Task 10), `frontend/src/components/layout/app-shell.tsx`, `layout/sidebar.tsx`, `layout/workspace-switcher.tsx`, `layout/sidebar-chat-list.tsx`, `layout/user-footer.tsx`, `layout/top-bar.tsx`, `layout/theme-toggle.tsx`, `frontend/src/app/require-role.tsx`
- Modify: `frontend/src/app/router.tsx` (AppShell as protected layout, role-gated admin routes), `frontend/src/main.tsx` (apply initial theme before render)
- Test: `frontend/src/lib/theme.test.ts`, `frontend/src/lib/use-claims.test.ts`, `frontend/src/components/layout/sidebar.test.tsx`

**Interfaces:**
- `theme.ts`: `type Theme = 'light' | 'dark'`; `resolveInitialTheme(): Theme` (localStorage `openrag-theme`, else `prefers-color-scheme`); `applyTheme(t)` (toggles `.dark` on `<html>`); `storeTheme(t)`; `useTheme(): { theme, toggle }`.
- `use-claims.ts`: `useClaims(): AccessClaims | null` (subscribes to auth store via `useSyncExternalStore`, decodes JWT).
- Workspaces: `useWorkspaces()` (`['workspaces']` → GET `/workspaces`), `useCreateWorkspace()` (invalidates `['workspaces']`); `<WorkspaceProvider>` + `useWorkspace(): { workspaceId: string | null, setWorkspaceId }` — defaults to first workspace, persists per user in localStorage `openrag-workspace`.
- Chat list: `useChats(workspaceId)` (`['chats', workspaceId]` → GET `/chats?workspace_id=`).
- `<AppShell/>`: sidebar (240px, `bg-sidebar`) + scrollable content `<Outlet/>`. Pages render their own `<TopBar title actions?/>` (thin bar, `border-b border-line`).
- `<RequireRole role="admin" | "superadmin"/>`: route wrapper; superadmin passes admin gates; failure → `<Navigate to="/chat">`. Sidebar hides links the role cannot use (server still enforces).

- [ ] **Step 1: Write failing tests**

`frontend/src/lib/theme.test.ts`:

```ts
import { applyTheme, resolveInitialTheme, storeTheme } from './theme';

afterEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove('dark');
});

test('stored theme wins over media preference', () => {
  storeTheme('dark');
  expect(resolveInitialTheme()).toBe('dark');
});

test('falls back to prefers-color-scheme', () => {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((q: string) => ({ matches: q.includes('dark'), addEventListener: vi.fn(), removeEventListener: vi.fn() })),
  );
  expect(resolveInitialTheme()).toBe('dark');
  vi.unstubAllGlobals();
});

test('applyTheme toggles the .dark class on <html>', () => {
  applyTheme('dark');
  expect(document.documentElement.classList.contains('dark')).toBe(true);
  applyTheme('light');
  expect(document.documentElement.classList.contains('dark')).toBe(false);
});
```

`frontend/src/lib/use-claims.test.ts`:

```ts
import { renderHook, act } from '@testing-library/react';

import { setAccessToken } from './auth-store';
import { useClaims } from './use-claims';

const b64 = (o: object) =>
  btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const token = `${b64({ alg: 'HS256' })}.${b64({ sub: 'u1', org: 'o1', role: 'superadmin', exp: 9 })}.s`;

afterEach(() => setAccessToken(null));

test('exposes decoded claims and tracks token changes', () => {
  const { result } = renderHook(() => useClaims());
  expect(result.current).toBeNull();
  act(() => setAccessToken(token));
  expect(result.current?.role).toBe('superadmin');
});
```

`frontend/src/components/layout/sidebar.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { setAccessToken } from '@/lib/auth-store';
import { WorkspaceProvider } from '@/features/workspaces/workspace-context';

import { Sidebar } from './sidebar';

const b64 = (o: object) =>
  btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const tokenFor = (role: string) =>
  `${b64({ alg: 'HS256' })}.${b64({ sub: 'u1', org: 'o1', role, exp: 9, email: 'a@x.com' })}.s`;

function renderSidebar() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      const url = req.url;
      const body = url.includes('/workspaces')
        ? [{ id: 'w1', name: 'Finance', embedding_model: 'bge-m3', min_score: 0.35 }]
        : [];
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <WorkspaceProvider>
          <Sidebar />
        </WorkspaceProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
  localStorage.clear();
});

test('user role sees no admin links', async () => {
  setAccessToken(tokenFor('user'));
  renderSidebar();
  expect(await screen.findByText('Finance')).toBeInTheDocument();
  expect(screen.queryByText('Users')).not.toBeInTheDocument();
  expect(screen.queryByText('Models')).not.toBeInTheDocument();
});

test('superadmin sees Users and Models', async () => {
  setAccessToken(tokenFor('superadmin'));
  renderSidebar();
  expect(await screen.findByText('Users')).toBeInTheDocument();
  expect(screen.getByText('Models')).toBeInTheDocument();
});
```

Run: `pnpm test src/lib/theme src/lib/use-claims src/components/layout` — Expected: FAIL.

- [ ] **Step 2: Implement theme + claims**

`frontend/src/lib/theme.ts`:

```ts
import { useCallback, useState } from 'react';

export type Theme = 'light' | 'dark';

const KEY = 'openrag-theme';

export function storeTheme(theme: Theme): void {
  localStorage.setItem(KEY, theme);
}

export function resolveInitialTheme(): Theme {
  const stored = localStorage.getItem(KEY);
  if (stored === 'light' || stored === 'dark') return stored;
  const prefersDark =
    typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches;
  return prefersDark ? 'dark' : 'light';
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark');
}

export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setThemeState] = useState<Theme>(resolveInitialTheme);
  const toggle = useCallback(() => {
    setThemeState((prev) => {
      const next: Theme = prev === 'dark' ? 'light' : 'dark';
      storeTheme(next);
      applyTheme(next);
      return next;
    });
  }, []);
  return { theme, toggle };
}
```

`frontend/src/lib/use-claims.ts`:

```ts
import { useSyncExternalStore } from 'react';

import { getAccessToken, subscribeAuth } from './auth-store';
import { decodeClaims, type AccessClaims } from './jwt';

export function useClaims(): AccessClaims | null {
  const token = useSyncExternalStore(subscribeAuth, getAccessToken, getAccessToken);
  return token ? decodeClaims(token) : null;
}
```

In `frontend/src/main.tsx`, before `ReactDOM.createRoot(...)`:

```tsx
import { applyTheme, resolveInitialTheme } from './lib/theme';

applyTheme(resolveInitialTheme());
```

- [ ] **Step 3: Implement workspace + chat-list queries and context**

`frontend/src/features/workspaces/queries.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useWorkspaces() {
  return useQuery({
    queryKey: ['workspaces'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/workspaces');
      if (error) throw new Error('failed to load workspaces');
      return data;
    },
  });
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { name: string }) => {
      const { data, error } = await api.POST('/api/v1/workspaces', { body });
      if (error) throw new Error('failed to create workspace');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
  });
}
```

`frontend/src/features/workspaces/workspace-context.tsx`:

```tsx
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { useWorkspaces } from './queries';

const KEY = 'openrag-workspace';

interface WorkspaceState {
  workspaceId: string | null;
  setWorkspaceId: (id: string) => void;
}

const Ctx = createContext<WorkspaceState | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { data: workspaces } = useWorkspaces();
  const [workspaceId, setWorkspaceIdState] = useState<string | null>(() =>
    localStorage.getItem(KEY),
  );

  // Snap to a real workspace once the list loads (stored id may be stale/foreign).
  useEffect(() => {
    if (!workspaces || workspaces.length === 0) return;
    if (!workspaceId || !workspaces.some((w) => w.id === workspaceId)) {
      setWorkspaceIdState(workspaces[0]?.id ?? null);
    }
  }, [workspaces, workspaceId]);

  const value = useMemo<WorkspaceState>(
    () => ({
      workspaceId,
      setWorkspaceId: (id: string) => {
        localStorage.setItem(KEY, id);
        setWorkspaceIdState(id);
      },
    }),
    [workspaceId],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkspace(): WorkspaceState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useWorkspace outside WorkspaceProvider');
  return ctx;
}
```

`frontend/src/features/chat/queries.ts` (list only for the sidebar; Task 10 extends this file):

```ts
import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useChats(workspaceId: string | null) {
  return useQuery({
    queryKey: ['chats', workspaceId],
    enabled: workspaceId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/chats', {
        params: { query: { workspace_id: workspaceId as string } },
      });
      if (error) throw new Error('failed to load chats');
      return data;
    },
  });
}
```

- [ ] **Step 4: Implement layout components**

`frontend/src/components/layout/top-bar.tsx`:

```tsx
import { type ReactNode } from 'react';

export function TopBar({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-line bg-bg px-4">
      <h1 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">{title}</h1>
      {actions ? <div className="flex items-center gap-3">{actions}</div> : null}
    </header>
  );
}
```

`frontend/src/components/layout/theme-toggle.tsx`:

```tsx
import { Moon, Sun } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useTheme } from '@/lib/theme';

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const next = theme === 'dark' ? 'light' : 'dark';
  return (
    <Button variant="ghost" size="icon" aria-label={`Switch to ${next} mode`} onClick={toggle}>
      {theme === 'dark' ? <Sun className="h-4 w-4" aria-hidden /> : <Moon className="h-4 w-4" aria-hidden />}
    </Button>
  );
}
```

`frontend/src/components/layout/workspace-switcher.tsx`:

```tsx
import { Check, ChevronsUpDown, Plus } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useClaims } from '@/lib/use-claims';

import { useCreateWorkspace, useWorkspaces } from '@/features/workspaces/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';

export function WorkspaceSwitcher() {
  const claims = useClaims();
  const { data: workspaces } = useWorkspaces();
  const { workspaceId, setWorkspaceId } = useWorkspace();
  const create = useCreateWorkspace();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState('');

  const current = workspaces?.find((w) => w.id === workspaceId);
  const isAdmin = claims?.role === 'admin' || claims?.role === 'superadmin';

  const onCreate = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      { name },
      {
        onSuccess: (ws) => {
          setWorkspaceId(ws.id);
          setName('');
          setDialogOpen(false);
        },
      },
    );
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-[13px] font-medium text-ink hover:bg-subtle"
            aria-label="Switch workspace"
          >
            <span className="truncate">{current?.name ?? 'Select workspace'}</span>
            <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          {(workspaces ?? []).map((w) => (
            <DropdownMenuItem key={w.id} onSelect={() => setWorkspaceId(w.id)}>
              <span className="flex-1 truncate">{w.name}</span>
              {w.id === workspaceId ? <Check className="h-3.5 w-3.5 text-accent" aria-hidden /> : null}
            </DropdownMenuItem>
          ))}
          {isAdmin ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => setDialogOpen(true)}>
                <Plus className="mr-1 h-3.5 w-3.5" aria-hidden /> New workspace
              </DropdownMenuItem>
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent title="New workspace">
          <form onSubmit={onCreate}>
            <Label htmlFor="ws-name">Name</Label>
            <Input id="ws-name" required value={name} onChange={(e) => setName(e.target.value)} />
            <DialogFooter>
              <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={create.isPending}>
                Create
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
```

`frontend/src/components/layout/sidebar-chat-list.tsx`:

```tsx
import { MessageSquarePlus } from 'lucide-react';
import { NavLink, useNavigate } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/cn';

import { useChats } from '@/features/chat/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';

export function SidebarChatList() {
  const { workspaceId } = useWorkspace();
  const { data: chats } = useChats(workspaceId);
  const navigate = useNavigate();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between px-2 pb-1">
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted">Chats</span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="New chat"
          onClick={() => navigate('/chat')}
        >
          <MessageSquarePlus className="h-4 w-4" aria-hidden />
        </Button>
      </div>
      <nav aria-label="Chats" className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-1">
        {(chats ?? []).map((chat) => (
          <NavLink
            key={chat.id}
            to={`/chat/${chat.id}`}
            className={({ isActive }) =>
              cn(
                'block truncate rounded-md px-2 py-1.5 text-[13px] text-secondary hover:bg-subtle hover:text-ink',
                isActive && 'bg-subtle text-ink',
              )
            }
          >
            {chat.title || 'Untitled chat'}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
```

`frontend/src/components/layout/user-footer.tsx`:

```tsx
import { LogOut } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useClaims } from '@/lib/use-claims';

import { useLogout } from '@/features/auth/mutations';

import { ThemeToggle } from './theme-toggle';

export function UserFooter() {
  const claims = useClaims();
  const logout = useLogout();
  return (
    <div className="flex items-center justify-between border-t border-line-faint px-2 py-2">
      <div className="min-w-0">
        <p className="truncate text-[12px] font-medium text-ink">{claims?.sub ?? ''}</p>
        <p className="text-[11px] text-muted">{claims?.role ?? ''}</p>
      </div>
      <div className="flex items-center">
        <ThemeToggle />
        <Button variant="ghost" size="icon" aria-label="Sign out" onClick={() => logout.mutate()}>
          <LogOut className="h-4 w-4" aria-hidden />
        </Button>
      </div>
    </div>
  );
}
```

(If Plan C's `GET /users/me` exists, swap `claims.sub` for the fetched email; otherwise the JWT `sub` (user id) is acceptable Phase 1 footer text — note it in the commit body.)

`frontend/src/components/layout/sidebar.tsx`:

```tsx
import { FileText, Settings2, Users } from 'lucide-react';
import { NavLink } from 'react-router-dom';

import { cn } from '@/lib/cn';
import { useClaims } from '@/lib/use-claims';

import { SidebarChatList } from './sidebar-chat-list';
import { UserFooter } from './user-footer';
import { WorkspaceSwitcher } from './workspace-switcher';

function SideLink({ to, label, icon }: { to: string; label: string; icon: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] text-secondary hover:bg-subtle hover:text-ink',
          isActive && 'bg-subtle text-ink',
        )
      }
    >
      {icon}
      {label}
    </NavLink>
  );
}

export function Sidebar() {
  const claims = useClaims();
  const isAdmin = claims?.role === 'admin' || claims?.role === 'superadmin';
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-sidebar">
      <div className="flex items-center gap-2 px-3 pb-1 pt-3">
        <span aria-hidden className="h-4 w-4 rounded-sm bg-accent" />
        <span className="text-[14px] font-semibold tracking-[-0.01em] text-ink">OpenRAG</span>
      </div>
      <div className="px-2 py-2">
        <WorkspaceSwitcher />
      </div>
      <SidebarChatList />
      <nav aria-label="Sections" className="space-y-0.5 border-t border-line-faint px-1 py-2">
        <SideLink to="/documents" label="Documents" icon={<FileText className="h-4 w-4" aria-hidden />} />
        {isAdmin ? (
          <SideLink to="/admin/users" label="Users" icon={<Users className="h-4 w-4" aria-hidden />} />
        ) : null}
        {claims?.role === 'superadmin' ? (
          <SideLink to="/admin/models" label="Models" icon={<Settings2 className="h-4 w-4" aria-hidden />} />
        ) : null}
      </nav>
      <UserFooter />
    </aside>
  );
}
```

`frontend/src/components/layout/app-shell.tsx`:

```tsx
import { Outlet } from 'react-router-dom';

import { WorkspaceProvider } from '@/features/workspaces/workspace-context';

import { Sidebar } from './sidebar';

export function AppShell() {
  return (
    <WorkspaceProvider>
      <div className="flex h-screen overflow-hidden bg-bg">
        <Sidebar />
        <main className="flex min-w-0 flex-1 flex-col">
          <Outlet />
        </main>
      </div>
    </WorkspaceProvider>
  );
}
```

`frontend/src/app/require-role.tsx`:

```tsx
import { Navigate, Outlet } from 'react-router-dom';

import { useClaims } from '@/lib/use-claims';

export function RequireRole({ role }: { role: 'admin' | 'superadmin' }) {
  const claims = useClaims();
  const ok =
    claims !== null &&
    (claims.role === 'superadmin' || (role === 'admin' && claims.role === 'admin'));
  // UI convenience only — the backend role dependencies are the real gate.
  return ok ? <Outlet /> : <Navigate to="/chat" replace />;
}
```

Update `frontend/src/app/router.tsx`: replace the inner `element: <Outlet />` with `element: <AppShell />`, and wrap the admin routes:

```tsx
import { AppShell } from '@/components/layout/app-shell';
import { RequireRole } from './require-role';
// inside RequireAuth children:
      {
        element: <AppShell />,
        children: [
          { path: '/', element: <Navigate to="/chat" replace /> },
          { path: '/chat', element: <ComingSoon name="Chat" /> },
          { path: '/chat/:chatId', element: <ComingSoon name="Chat" /> },
          { path: '/documents', element: <ComingSoon name="Documents" /> },
          {
            element: <RequireRole role="admin" />,
            children: [{ path: '/admin/users', element: <ComingSoon name="Users" /> }],
          },
          {
            element: <RequireRole role="superadmin" />,
            children: [{ path: '/admin/models', element: <ComingSoon name="Models" /> }],
          },
        ],
      },
```

- [ ] **Step 5: Run tests, gates, and a manual smoke**

Run: `pnpm test && pnpm lint && pnpm typecheck`
Expected: all PASS.

Manual: `pnpm dev` (backend up) → sign in → sidebar shows workspaces (create one if empty), theme toggle flips and survives reload, `user` role account sees no admin links, `/admin/models` as admin redirects to `/chat`.

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat: app shell with sidebar, workspace context, theme toggle, role-gated nav"
```

---

### Task 7: Message tree → active path selector (pure, test-first)

**Files:**
- Create: `frontend/src/features/chat/tree.ts`
- Test: `frontend/src/features/chat/tree.test.ts`

**Interfaces:**
- `ROOT: '__root__'` — branch key for top-level messages.
- `interface PathEntry { message: MessageOut; siblings: string[]; position: number }` (`siblings` = ordered ids at this branch point; `position` = 0-based index of the chosen one → render `< {position+1}/{siblings.length} >`).
- `type SelectionOverrides = Readonly<Record<string, string>>` — branch key (parent message id, or `ROOT`) → chosen child id. Client-side only, per phase1 spec §2.1.
- `selectActivePath(messages: MessageOut[], overrides: SelectionOverrides): PathEntry[]` — walks from ROOT choosing the override if present and valid, else the **newest sibling** (highest `sibling_index`, ties by `created_at` then `id`); cycle- and orphan-safe; pure.
- `branchKeyOf(message: MessageOut): string` — `parent_message_id ?? ROOT`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/features/chat/tree.test.ts`:

```ts
import type { MessageOut } from '@/api/types';

import { ROOT, branchKeyOf, selectActivePath } from './tree';

function msg(over: Partial<MessageOut> & { id: string }): MessageOut {
  return {
    parent_message_id: null,
    sibling_index: 0,
    role: 'user',
    content: `content-${over.id}`,
    model_id: null,
    created_at: '2026-07-18T00:00:00Z',
    ...over,
  } as MessageOut;
}

const linear = [
  msg({ id: 'u1', role: 'user' }),
  msg({ id: 'a1', role: 'assistant', parent_message_id: 'u1' }),
  msg({ id: 'u2', role: 'user', parent_message_id: 'a1' }),
  msg({ id: 'a2', role: 'assistant', parent_message_id: 'u2' }),
];

test('linear thread returns the full path with singleton sibling sets', () => {
  const path = selectActivePath(linear, {});
  expect(path.map((p) => p.message.id)).toEqual(['u1', 'a1', 'u2', 'a2']);
  expect(path.every((p) => p.siblings.length === 1 && p.position === 0)).toBe(true);
});

test('edited user message: newest sibling wins by default, old downstream kept apart', () => {
  const edited = [
    ...linear,
    msg({ id: 'u2b', role: 'user', parent_message_id: 'a1', sibling_index: 1 }),
    msg({ id: 'a2b', role: 'assistant', parent_message_id: 'u2b' }),
  ];
  const path = selectActivePath(edited, {});
  expect(path.map((p) => p.message.id)).toEqual(['u1', 'a1', 'u2b', 'a2b']);
  const entry = path[2]!;
  expect(entry.siblings).toEqual(['u2', 'u2b']);
  expect(entry.position).toBe(1); // renders as 2/2
});

test('override navigates back to the older sibling and its own answers', () => {
  const edited = [
    ...linear,
    msg({ id: 'u2b', role: 'user', parent_message_id: 'a1', sibling_index: 1 }),
    msg({ id: 'a2b', role: 'assistant', parent_message_id: 'u2b' }),
  ];
  const path = selectActivePath(edited, { a1: 'u2' });
  expect(path.map((p) => p.message.id)).toEqual(['u1', 'a1', 'u2', 'a2']);
  expect(path[2]!.position).toBe(0); // renders as 1/2
});

test('regenerated assistant: newest sibling default, both reachable', () => {
  const regen = [
    ...linear,
    msg({ id: 'a2b', role: 'assistant', parent_message_id: 'u2', sibling_index: 1 }),
  ];
  expect(selectActivePath(regen, {}).at(-1)!.message.id).toBe('a2b');
  expect(selectActivePath(regen, { u2: 'a2' }).at(-1)!.message.id).toBe('a2');
});

test('invalid override id falls back to newest', () => {
  const path = selectActivePath(linear, { [ROOT]: 'nope' });
  expect(path[0]!.message.id).toBe('u1');
});

test('sibling order: sibling_index, then created_at, then id', () => {
  const twins = [
    msg({ id: 'b', sibling_index: 0, created_at: '2026-07-18T00:00:02Z' }),
    msg({ id: 'a', sibling_index: 0, created_at: '2026-07-18T00:00:01Z' }),
  ];
  expect(selectActivePath(twins, {})[0]!.siblings).toEqual(['a', 'b']);
});

test('orphans and empty input are safe', () => {
  expect(selectActivePath([], {})).toEqual([]);
  const orphan = [msg({ id: 'x', parent_message_id: 'ghost' })];
  expect(selectActivePath(orphan, {})).toEqual([]);
});

test('branchKeyOf', () => {
  expect(branchKeyOf(msg({ id: 'u1' }))).toBe(ROOT);
  expect(branchKeyOf(msg({ id: 'a1', parent_message_id: 'u1' }))).toBe('u1');
});
```

Run: `pnpm test src/features/chat/tree` — Expected: FAIL (`tree.ts` missing).

- [ ] **Step 2: Implement `frontend/src/features/chat/tree.ts`**

```ts
import type { MessageOut } from '@/api/types';

export const ROOT = '__root__';

export interface PathEntry {
  message: MessageOut;
  siblings: string[];
  position: number;
}

export type SelectionOverrides = Readonly<Record<string, string>>;

export function branchKeyOf(message: MessageOut): string {
  return message.parent_message_id ?? ROOT;
}

function compareSiblings(a: MessageOut, b: MessageOut): number {
  return (
    a.sibling_index - b.sibling_index ||
    a.created_at.localeCompare(b.created_at) ||
    a.id.localeCompare(b.id)
  );
}

/**
 * Flat message list → the single rendered path (phase1 spec §2.1).
 * At each branch point: the override's child if it exists there, else the
 * NEWEST sibling. Pure; cycle- and orphan-safe.
 */
export function selectActivePath(
  messages: readonly MessageOut[],
  overrides: SelectionOverrides,
): PathEntry[] {
  const byBranch = new Map<string, MessageOut[]>();
  for (const message of messages) {
    const key = branchKeyOf(message);
    const bucket = byBranch.get(key);
    if (bucket) bucket.push(message);
    else byBranch.set(key, [message]);
  }
  for (const bucket of byBranch.values()) bucket.sort(compareSiblings);

  const path: PathEntry[] = [];
  const visited = new Set<string>();
  let branchKey = ROOT;
  for (;;) {
    const siblings = byBranch.get(branchKey);
    if (!siblings || siblings.length === 0) break;
    const overrideId = overrides[branchKey];
    const chosen = siblings.find((m) => m.id === overrideId) ?? siblings[siblings.length - 1]!;
    if (visited.has(chosen.id)) break; // corrupt data cycle guard
    visited.add(chosen.id);
    path.push({
      message: chosen,
      siblings: siblings.map((m) => m.id),
      position: siblings.indexOf(chosen),
    });
    branchKey = chosen.id;
  }
  return path;
}
```

- [ ] **Step 3: Run tests**

Run: `pnpm test src/features/chat/tree && pnpm lint && pnpm typecheck`
Expected: 8 PASS, gates clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/
git commit -m "feat: pure message-tree active-path selector with newest-sibling default"
```

---

### Task 8: SSE parser and chat stream hook (test-first)

**Files:**
- Create: `frontend/src/lib/sse.ts`, `frontend/src/features/chat/stream.ts`, `frontend/src/features/chat/use-chat-stream.ts`
- Test: `frontend/src/lib/sse.test.ts`, `frontend/src/features/chat/stream.test.ts`

**Interfaces:**
- `sse.ts` (transport-agnostic, pure): `interface SseMessage { event: string; data: string }`; `createSseParser(onMessage): { feed(chunk: string): void; flush(): void }` — handles partial chunks across `feed` calls, `\n` / `\r\n` / `\r` line endings, multi-`data:` accumulation, comment lines, default event name `message`.
- `stream.ts`: `type ChatSseEvent = { type: 'retrieval_started' } | { type: 'sources'; sources: SourceRef[] } | { type: 'token'; delta: string } | { type: 'citations'; citations: CitationRef[] } | { type: 'done'; done: DoneInfo } | { type: 'error'; detail: string }`; `streamChatSse(url: string, body: unknown, onEvent: (e: ChatSseEvent) => void, signal: AbortSignal): Promise<void>` — POST via `authFetch` (bearer + refresh-on-401 for free), reads `res.body` with `TextDecoder(stream: true)`, feeds the parser, emits a terminal `error` event on non-OK/malformed frames, swallows `AbortError`.
- `use-chat-stream.ts`: `useChatStream(chatId: string | null)` → `{ status: 'idle' | 'retrieving' | 'streaming' | 'done' | 'error'; text: string; sources: SourceRef[]; citations: CitationRef[]; noAnswer: boolean; errorDetail: string | null; pendingUserContent: string | null; doneMessageId: string | null; send(content: string, parentMessageId?: string | null, modelId?: string | null): void; regenerate(messageId: string): void; abort(): void; reset(): void }`. On `done`: invalidates `['chat', chatId]` and `['chats']`. `send` records `pendingUserContent` for optimistic rendering until the refetch lands.

- [ ] **Step 1: Write failing parser tests**

`frontend/src/lib/sse.test.ts`:

```ts
import { createSseParser, type SseMessage } from './sse';

function collect(): { messages: SseMessage[]; parser: ReturnType<typeof createSseParser> } {
  const messages: SseMessage[] = [];
  const parser = createSseParser((m) => messages.push(m));
  return { messages, parser };
}

test('parses a complete event', () => {
  const { messages, parser } = collect();
  parser.feed('event: token\ndata: {"delta":"Hi"}\n\n');
  expect(messages).toEqual([{ event: 'token', data: '{"delta":"Hi"}' }]);
});

test('reassembles events split across arbitrary chunk boundaries', () => {
  const { messages, parser } = collect();
  for (const chunk of ['eve', 'nt: tok', 'en\nda', 'ta: {"delta":"a', 'b"}\n', '\n']) {
    parser.feed(chunk);
  }
  expect(messages).toEqual([{ event: 'token', data: '{"delta":"ab"}' }]);
});

test('multiple events per chunk, CRLF endings, comments ignored', () => {
  const { messages, parser } = collect();
  parser.feed(': keepalive\r\nevent: a\r\ndata: 1\r\n\r\nevent: b\r\ndata: 2\r\n\r\n');
  expect(messages).toEqual([
    { event: 'a', data: '1' },
    { event: 'b', data: '2' },
  ]);
});

test('multi-line data joins with newline; default event name is message', () => {
  const { messages, parser } = collect();
  parser.feed('data: line1\ndata: line2\n\n');
  expect(messages).toEqual([{ event: 'message', data: 'line1\nline2' }]);
});

test('event name resets after dispatch', () => {
  const { messages, parser } = collect();
  parser.feed('event: token\ndata: 1\n\ndata: 2\n\n');
  expect(messages[1]).toEqual({ event: 'message', data: '2' });
});

test('flush dispatches a trailing unterminated event', () => {
  const { messages, parser } = collect();
  parser.feed('event: done\ndata: {"ok":true}');
  expect(messages).toEqual([]);
  parser.flush();
  expect(messages).toEqual([{ event: 'done', data: '{"ok":true}' }]);
});
```

Run: `pnpm test src/lib/sse` — Expected: FAIL.

- [ ] **Step 2: Implement `frontend/src/lib/sse.ts`**

```ts
export interface SseMessage {
  event: string;
  data: string;
}

/** Incremental text/event-stream parser (WHATWG SSE grammar subset we consume). */
export function createSseParser(onMessage: (message: SseMessage) => void): {
  feed(chunk: string): void;
  flush(): void;
} {
  let buffer = '';
  let event = 'message';
  let dataLines: string[] = [];

  const dispatch = (): void => {
    if (dataLines.length > 0) onMessage({ event, data: dataLines.join('\n') });
    event = 'message';
    dataLines = [];
  };

  const processLine = (line: string): void => {
    if (line === '') {
      dispatch();
      return;
    }
    if (line.startsWith(':')) return; // comment / keepalive
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') event = value;
    else if (field === 'data') dataLines.push(value);
    // id / retry: not used by our protocol
  };

  return {
    feed(chunk: string): void {
      buffer += chunk;
      for (;;) {
        const match = /\r\n|\n|\r/.exec(buffer);
        if (!match) break;
        // A lone \r at the very end might be half of \r\n — wait for more input.
        if (match[0] === '\r' && match.index === buffer.length - 1) break;
        const line = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        processLine(line);
      }
    },
    flush(): void {
      if (buffer !== '') {
        processLine(buffer.replace(/\r$/, ''));
        buffer = '';
      }
      dispatch();
    },
  };
}
```

- [ ] **Step 3: Write failing stream tests**

`frontend/src/features/chat/stream.test.ts`:

```ts
import { setAccessToken } from '@/lib/auth-store';

import { streamChatSse, type ChatSseEvent } from './stream';

function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('emits typed events in order, tokens split across chunks', async () => {
  setAccessToken('tok');
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        'event: retrieval_started\ndata: {}\n\n',
        'event: sources\ndata: {"sources":[{"n":1,"document_id":"d1","filename":"a.pdf","page":3,"score":0.7}]}\n\n',
        'event: token\ndata: {"del',
        'ta":"Hel"}\n\nevent: token\ndata: {"delta":"lo"}\n\n',
        'event: citations\ndata: {"citations":[{"n":1,"document_id":"d1","page":3}]}\n\n',
        'event: done\ndata: {"message_id":"m1","prompt_tokens":10,"completion_tokens":5,"no_answer":false}\n\n',
      ]),
    ),
  );
  const events: ChatSseEvent[] = [];
  await streamChatSse('/api/v1/chats/c1/messages', { content: 'hi' }, (e) => events.push(e), new AbortController().signal);
  expect(events.map((e) => e.type)).toEqual([
    'retrieval_started',
    'sources',
    'token',
    'token',
    'citations',
    'done',
  ]);
  const tokens = events.filter((e): e is Extract<ChatSseEvent, { type: 'token' }> => e.type === 'token');
  expect(tokens.map((t) => t.delta).join('')).toBe('Hello');
});

test('non-OK response emits a terminal error event', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'workspace access denied' }), {
        status: 403,
        headers: { 'content-type': 'application/problem+json' },
      }),
    ),
  );
  const events: ChatSseEvent[] = [];
  await streamChatSse('/api/v1/chats/c1/messages', { content: 'hi' }, (e) => events.push(e), new AbortController().signal);
  expect(events).toEqual([{ type: 'error', detail: 'workspace access denied' }]);
});

test('malformed frame data emits error but keeps the stream alive', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse(['event: token\ndata: not-json\n\n', 'event: token\ndata: {"delta":"ok"}\n\n']),
    ),
  );
  const events: ChatSseEvent[] = [];
  await streamChatSse('/api/v1/chats/c1/messages', {}, (e) => events.push(e), new AbortController().signal);
  expect(events.map((e) => e.type)).toEqual(['error', 'token']);
});
```

Run: `pnpm test src/features/chat/stream` — Expected: FAIL.

- [ ] **Step 4: Implement `frontend/src/features/chat/stream.ts`**

```ts
import { authFetch } from '@/api/client';
import type { CitationRef, DoneInfo, SourceRef } from '@/api/types';
import { createSseParser, type SseMessage } from '@/lib/sse';

export type ChatSseEvent =
  | { type: 'retrieval_started' }
  | { type: 'sources'; sources: SourceRef[] }
  | { type: 'token'; delta: string }
  | { type: 'citations'; citations: CitationRef[] }
  | { type: 'done'; done: DoneInfo }
  | { type: 'error'; detail: string };

function toEvent(message: SseMessage): ChatSseEvent {
  try {
    const data: unknown = JSON.parse(message.data);
    switch (message.event) {
      case 'retrieval_started':
        return { type: 'retrieval_started' };
      case 'sources':
        return { type: 'sources', sources: (data as { sources: SourceRef[] }).sources };
      case 'token':
        return { type: 'token', delta: (data as { delta: string }).delta };
      case 'citations':
        return { type: 'citations', citations: (data as { citations: CitationRef[] }).citations };
      case 'done':
        return { type: 'done', done: data as DoneInfo };
      default:
        return { type: 'error', detail: `unknown event: ${message.event}` };
    }
  } catch {
    return { type: 'error', detail: `malformed ${message.event} frame` };
  }
}

export async function streamChatSse(
  url: string,
  body: unknown,
  onEvent: (event: ChatSseEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await authFetch(
      new Request(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
        body: JSON.stringify(body),
        credentials: 'include',
        signal,
      }),
    );
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') return;
    onEvent({ type: 'error', detail: 'network error' });
    return;
  }
  if (!res.ok || !res.body) {
    let detail = `request failed (${res.status})`;
    try {
      const problem = (await res.json()) as { detail?: string };
      if (problem.detail) detail = problem.detail;
    } catch {
      /* keep default detail */
    }
    onEvent({ type: 'error', detail });
    return;
  }

  const parser = createSseParser((m) => onEvent(toEvent(m)));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
    }
    parser.feed(decoder.decode());
    parser.flush();
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') return;
    onEvent({ type: 'error', detail: 'stream interrupted' });
  }
}
```

- [ ] **Step 5: Implement `frontend/src/features/chat/use-chat-stream.ts`**

```ts
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef, useState } from 'react';

import type { CitationRef, SourceRef } from '@/api/types';

import { streamChatSse, type ChatSseEvent } from './stream';

export type StreamStatus = 'idle' | 'retrieving' | 'streaming' | 'done' | 'error';

export interface ChatStreamState {
  status: StreamStatus;
  text: string;
  sources: SourceRef[];
  citations: CitationRef[];
  noAnswer: boolean;
  errorDetail: string | null;
  pendingUserContent: string | null;
  doneMessageId: string | null; // lets the page hide the streamed block once the refetched tree contains it
}

const IDLE: ChatStreamState = {
  status: 'idle',
  text: '',
  sources: [],
  citations: [],
  noAnswer: false,
  errorDetail: null,
  pendingUserContent: null,
  doneMessageId: null,
};

function reduce(state: ChatStreamState, event: ChatSseEvent): ChatStreamState {
  switch (event.type) {
    case 'retrieval_started':
      return { ...state, status: 'retrieving' };
    case 'sources':
      return { ...state, sources: event.sources };
    case 'token':
      return { ...state, status: 'streaming', text: state.text + event.delta };
    case 'citations':
      return { ...state, citations: event.citations };
    case 'done':
      return {
        ...state,
        status: 'done',
        noAnswer: event.done.no_answer,
        doneMessageId: event.done.message_id,
      };
    case 'error':
      return { ...state, status: 'error', errorDetail: event.detail };
  }
}

export function useChatStream(chatId: string | null) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<ChatStreamState>(IDLE);
  const abortRef = useRef<AbortController | null>(null);

  const run = useCallback(
    (url: string, body: unknown, pendingUserContent: string | null) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setState({ ...IDLE, status: 'retrieving', pendingUserContent });
      void streamChatSse(
        url,
        body,
        (event) => {
          setState((prev) => reduce(prev, event));
          if (event.type === 'done') {
            void queryClient.invalidateQueries({ queryKey: ['chat', chatId] });
            void queryClient.invalidateQueries({ queryKey: ['chats'] });
          }
        },
        controller.signal,
      );
    },
    [chatId, queryClient],
  );

  const send = useCallback(
    (content: string, parentMessageId?: string | null, modelId?: string | null) => {
      if (!chatId) return;
      run(
        `/api/v1/chats/${chatId}/messages`,
        {
          content,
          ...(parentMessageId ? { parent_message_id: parentMessageId } : {}),
          ...(modelId ? { model_id: modelId } : {}),
        },
        content,
      );
    },
    [chatId, run],
  );

  const regenerate = useCallback(
    (messageId: string) => run(`/api/v1/messages/${messageId}/regenerate`, {}, null),
    [run],
  );

  const abort = useCallback(() => abortRef.current?.abort(), []);
  const reset = useCallback(() => setState(IDLE), []);

  return { ...state, send, regenerate, abort, reset };
}
```

- [ ] **Step 6: Run tests and gates**

Run: `pnpm test src/lib/sse src/features/chat && pnpm lint && pnpm typecheck`
Expected: all PASS (tree tests from Task 7 still green).

- [ ] **Step 7: Commit**

```bash
git add frontend/
git commit -m "feat: sse parser, typed chat stream transport, useChatStream state machine"
```

---

### Task 9: Sanitized markdown renderer, citation chips, source panel

**Files:**
- Create: `frontend/src/features/chat/remark-citations.ts`, `frontend/src/components/markdown/markdown.tsx`, `markdown/code-block.tsx`, `frontend/src/features/chat/citation-chip.tsx`, `chat/citation-context.tsx`, `chat/source-panel.tsx`
- Test: `frontend/src/features/chat/remark-citations.test.ts`, `frontend/src/components/markdown/markdown.test.tsx`

**Interfaces:**
- `remarkCitations()` — remark plugin: splits text nodes on `[n]` (1–2 digits), inserting nodes with `data.hName = 'citation-chip'`, `data.hProperties = { n }`. Skips code/inlineCode (visitor never descends into them for text nodes of type `text` only).
- `<Markdown content={string} />` — react-markdown + remark-gfm + remarkCitations, `skipHtml` (iron rule 5: raw HTML in model output renders as nothing; no `rehype-raw`, no `dangerouslySetInnerHTML` anywhere), token-styled element overrides (chat prose 15px/1.6; tables sticky-header + zebra `bg-raised` per theme spec §6), fenced code via `<CodeBlock>` with a copy button.
- `<CitationContext.Provider onCitationClick(n)>` + `useCitationClick()` — chips inside markdown call up to the message that knows its sources.
- `<CitationChip n onClick?>` — 15px chip, `bg-accent-soft text-accent-on-soft rounded-sm` (theme spec §3).
- `<SourcePanel sources highlightedN? onSelect?>` — `[n] filename · p. N` chips on `bg-raised`, hairline border.

- [ ] **Step 1: Add dependencies**

Run: `pnpm add react-markdown@^9.0.1 remark-gfm@^4.0.0 unist-util-visit@^5.0.0`

- [ ] **Step 2: Write failing tests**

`frontend/src/features/chat/remark-citations.test.ts`:

```ts
import { remark } from 'remark';

import { remarkCitations } from './remark-citations';

// remark is a transitive dep of react-markdown; add it explicitly for tests:
// pnpm add -D remark@^15.0.1

interface Node {
  type: string;
  value?: string;
  children?: Node[];
  data?: { hName?: string; hProperties?: { n?: string } };
}

function transform(md: string): Node {
  const processor = remark().use(remarkCitations);
  return processor.runSync(processor.parse(md)) as unknown as Node;
}

function flatten(node: Node, out: Node[] = []): Node[] {
  out.push(node);
  for (const child of node.children ?? []) flatten(child, out);
  return out;
}

test('splits [n] markers into citation nodes preserving surrounding text', () => {
  const nodes = flatten(transform('Revenue rose 12% [1] and churn fell [2].'));
  const chips = nodes.filter((n) => n.data?.hName === 'citation-chip');
  expect(chips.map((c) => c.data?.hProperties?.n)).toEqual(['1', '2']);
  const texts = nodes.filter((n) => n.type === 'text').map((n) => n.value);
  expect(texts).toEqual(['Revenue rose 12% ', ' and churn fell ', '.']);
});

test('leaves text without markers untouched', () => {
  const nodes = flatten(transform('No citations here [not one].'));
  expect(nodes.some((n) => n.data?.hName === 'citation-chip')).toBe(false);
});

test('does not rewrite inside inline code', () => {
  const nodes = flatten(transform('Use `arr[1]` to index.'));
  expect(nodes.some((n) => n.data?.hName === 'citation-chip')).toBe(false);
});
```

`frontend/src/components/markdown/markdown.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';

import { CitationProvider } from '@/features/chat/citation-context';

import { Markdown } from './markdown';

function renderMd(content: string, onCitationClick = vi.fn()) {
  render(
    <CitationProvider onCitationClick={onCitationClick}>
      <Markdown content={content} />
    </CitationProvider>,
  );
  return onCitationClick;
}

test('renders gfm tables and formatting', () => {
  renderMd('| a | b |\n|---|---|\n| 1 | 2 |');
  expect(screen.getByRole('table')).toBeInTheDocument();
});

test('IRON RULE 5: raw HTML in model output is never rendered as elements', () => {
  renderMd('before <img src=x onerror="window.pwned=1"> <script>window.pwned=1</script> after');
  expect(document.querySelector('img')).toBeNull();
  expect(document.querySelector('script')).toBeNull();
  expect((window as { pwned?: number }).pwned).toBeUndefined();
});

test('renders [n] as clickable citation chips', async () => {
  const { default: userEvent } = await import('@testing-library/user-event');
  const onClick = renderMd('Answer text [1] more.');
  const chip = screen.getByRole('button', { name: 'Citation 1' });
  await userEvent.setup().click(chip);
  expect(onClick).toHaveBeenCalledWith(1);
});

test('fenced code renders with a copy button', () => {
  renderMd('```py\nprint(1)\n```');
  expect(screen.getByRole('button', { name: 'Copy code' })).toBeInTheDocument();
});
```

Run: `pnpm add -D remark@^15.0.1` then `pnpm test src/features/chat/remark-citations src/components/markdown` — Expected: FAIL.

- [ ] **Step 3: Implement plugin and context**

`frontend/src/features/chat/remark-citations.ts`:

```ts
import { visit, SKIP } from 'unist-util-visit';

interface TextNode {
  type: 'text';
  value: string;
}

interface Parent {
  type: string;
  children: Array<Record<string, unknown>>;
}

const MARKER = /\[(\d{1,2})\]/g;

/** remark plugin: turn `[n]` in prose text into citation-chip nodes. */
export function remarkCitations() {
  return (tree: Parent): void => {
    visit(
      tree as never,
      'text',
      (node: TextNode, index: number | undefined, parent: Parent | undefined) => {
        if (!parent || index === undefined) return;
        if (parent.type === 'code' || parent.type === 'inlineCode' || parent.type === 'link') return;
        MARKER.lastIndex = 0;
        if (!MARKER.test(node.value)) return;
        MARKER.lastIndex = 0;

        const replacement: Array<Record<string, unknown>> = [];
        let cursor = 0;
        let match: RegExpExecArray | null;
        while ((match = MARKER.exec(node.value)) !== null) {
          if (match.index > cursor) {
            replacement.push({ type: 'text', value: node.value.slice(cursor, match.index) });
          }
          replacement.push({
            type: 'citationChip',
            data: { hName: 'citation-chip', hProperties: { n: match[1] } },
            children: [],
          });
          cursor = match.index + match[0].length;
        }
        if (cursor < node.value.length) {
          replacement.push({ type: 'text', value: node.value.slice(cursor) });
        }
        parent.children.splice(index, 1, ...replacement);
        return [SKIP, index + replacement.length] as const;
      },
    );
  };
}
```

`frontend/src/features/chat/citation-context.tsx`:

```tsx
import { createContext, useContext, type ReactNode } from 'react';

const Ctx = createContext<(n: number) => void>(() => {});

export function CitationProvider({
  onCitationClick,
  children,
}: {
  onCitationClick: (n: number) => void;
  children: ReactNode;
}) {
  return <Ctx.Provider value={onCitationClick}>{children}</Ctx.Provider>;
}

export function useCitationClick(): (n: number) => void {
  return useContext(Ctx);
}
```

`frontend/src/features/chat/citation-chip.tsx`:

```tsx
import { useCitationClick } from './citation-context';

export function CitationChip({ n }: { n: string | number }) {
  const onCitationClick = useCitationClick();
  const num = Number(n);
  return (
    <button
      type="button"
      aria-label={`Citation ${num}`}
      onClick={() => onCitationClick(num)}
      className="mx-0.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-sm bg-accent-soft px-1 align-baseline text-[11px] font-medium text-accent-on-soft hover:opacity-80"
    >
      {num}
    </button>
  );
}
```

- [ ] **Step 4: Implement the renderer**

`frontend/src/components/markdown/code-block.tsx`:

```tsx
import { Check, Copy } from 'lucide-react';
import { useState, type ReactNode } from 'react';

function textOf(node: ReactNode): string {
  if (typeof node === 'string') return node;
  if (Array.isArray(node)) return node.map(textOf).join('');
  if (node && typeof node === 'object' && 'props' in node) {
    return textOf((node as { props: { children?: ReactNode } }).props.children);
  }
  return '';
}

export function CodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(textOf(children).replace(/\n$/, ''));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="group relative my-2 rounded-md border border-line bg-subtle">
      <button
        type="button"
        aria-label="Copy code"
        onClick={() => void copy()}
        className="absolute right-2 top-2 rounded-sm p-1 text-muted hover:bg-raised hover:text-ink"
      >
        {copied ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
      </button>
      <pre className="overflow-x-auto p-3 font-mono text-[13px] leading-relaxed text-ink">
        {children}
      </pre>
    </div>
  );
}
```

`frontend/src/components/markdown/markdown.tsx`:

```tsx
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { CitationChip } from '@/features/chat/citation-chip';
import { remarkCitations } from '@/features/chat/remark-citations';

import { CodeBlock } from './code-block';

// 'citation-chip' comes from remarkCitations' hName; react-markdown accepts
// custom element names via a widened Components type.
const components = {
  'citation-chip': CitationChip,
  pre: CodeBlock,
  code: ({ children, className }: { children?: React.ReactNode; className?: string }) =>
    className ? (
      <code className={className}>{children}</code> // inside <pre>, CodeBlock styles it
    ) : (
      <code className="rounded-sm bg-subtle px-1 py-0.5 font-mono text-[13px]">{children}</code>
    ),
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
    <a href={href} target="_blank" rel="noreferrer noopener" className="text-accent underline">
      {children}
    </a>
  ),
  p: (p: { children?: React.ReactNode }) => <p className="my-2 leading-relaxed">{p.children}</p>,
  ul: (p: { children?: React.ReactNode }) => <ul className="my-2 list-disc pl-5">{p.children}</ul>,
  ol: (p: { children?: React.ReactNode }) => <ol className="my-2 list-decimal pl-5">{p.children}</ol>,
  h1: (p: { children?: React.ReactNode }) => <h2 className="mt-4 mb-2 text-[16px] font-semibold">{p.children}</h2>,
  h2: (p: { children?: React.ReactNode }) => <h3 className="mt-4 mb-2 text-[15px] font-semibold">{p.children}</h3>,
  h3: (p: { children?: React.ReactNode }) => <h4 className="mt-3 mb-1 text-[14px] font-semibold">{p.children}</h4>,
  table: (p: { children?: React.ReactNode }) => (
    <div className="my-2 overflow-x-auto rounded-md border border-line">
      <table className="w-full text-[13px] tabular-nums">{p.children}</table>
    </div>
  ),
  thead: (p: { children?: React.ReactNode }) => (
    <thead className="sticky top-0 bg-raised text-left">{p.children}</thead>
  ),
  th: (p: { children?: React.ReactNode }) => (
    <th className="border-b border-line px-2.5 py-1.5 font-medium text-secondary">{p.children}</th>
  ),
  td: (p: { children?: React.ReactNode }) => (
    <td className="border-b border-line-faint px-2.5 py-1.5">{p.children}</td>
  ),
  tr: (p: { children?: React.ReactNode }) => <tr className="even:bg-raised">{p.children}</tr>,
  blockquote: (p: { children?: React.ReactNode }) => (
    <blockquote className="my-2 border-l-2 border-line-strong pl-3 text-secondary">
      {p.children}
    </blockquote>
  ),
} as Components;

export function Markdown({ content }: { content: string }) {
  return (
    <div className="text-[15px] leading-[1.6] text-ink">
      <ReactMarkdown skipHtml remarkPlugins={[remarkGfm, remarkCitations]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
```

`frontend/src/features/chat/source-panel.tsx`:

```tsx
import { FileText } from 'lucide-react';

import type { SourceRef } from '@/api/types';
import { cn } from '@/lib/cn';

export function SourcePanel({
  sources,
  highlightedN,
  onSelect,
}: {
  sources: SourceRef[];
  highlightedN?: number | null;
  onSelect?: (n: number) => void;
}) {
  if (sources.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Sources">
      {sources.map((source) => (
        <button
          key={source.n}
          type="button"
          onClick={() => onSelect?.(source.n)}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-md border border-line bg-raised px-2 py-1 text-[12px] text-secondary hover:text-ink',
            highlightedN === source.n && 'border-accent text-ink',
          )}
        >
          <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-sm bg-accent-soft px-0.5 text-[10px] font-medium text-accent-on-soft">
            {source.n}
          </span>
          <FileText className="h-3 w-3 text-muted" aria-hidden />
          <span className="max-w-[220px] truncate">{source.filename}</span>
          {source.page !== null ? <span className="text-muted">· p. {source.page}</span> : null}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 5: Run tests and gates**

Run: `pnpm test src/features/chat src/components/markdown && pnpm lint && pnpm typecheck`
Expected: all PASS — including the iron-rule-5 raw-HTML test.

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat: sanitized markdown renderer with citation chips and source panel"
```

---

### Task 10: Chat page — thread, streaming, input, model selector, usage stub

**Files:**
- Create: `frontend/src/features/models/queries.ts`, `frontend/src/features/chat/use-tree-selection.ts`, `chat/chat-input.tsx`, `chat/user-message.tsx`, `chat/assistant-message.tsx` (base; actions row added Task 11), `chat/streaming-message.tsx`, `chat/no-answer-notice.tsx`, `chat/model-selector.tsx`, `chat/usage-meter.tsx`, `chat/chat-page.tsx`
- Modify: `frontend/src/features/chat/queries.ts` (add `useChat`, `useCreateChat`), `frontend/src/app/router.tsx` (mount `ChatPage`)
- Test: `frontend/src/features/chat/chat-input.test.tsx`, `chat/use-tree-selection.test.ts`

**Assumption (verify against schema.d.ts):** Plan C extends `WorkspaceOut` with `default_model_id: string | null`. If absent, the model selector falls back to the first enabled model — implement the fallback either way.

**Interfaces:**
- `useChat(chatId)` — `['chat', chatId]` → GET `/api/v1/chats/{chat_id}` → `ChatDetailOut` (flat message tree). `useCreateChat()` — POST `/chats {workspace_id}`, invalidates `['chats']`. `useModels()` — `['models']` → GET `/api/v1/models` (enabled, any authenticated user).
- `useTreeSelection(messages)` → `{ path: PathEntry[]; select(branchKey: string, id: string): void }` (thin stateful wrapper over Task 7's pure selector).
- `<ChatInput onSend(content) disabled placeholder?>` — pill (`rounded-xl`, `shadow-soft`), auto-growing textarea, Enter sends / Shift+Enter newline, 26px circular send button (`bg-ink text-bg`), `aria-label="Message"`. No attach button: per-chat attachments are Phase 2 (phase1 spec Out list) — the theme spec slot stays empty this phase.
- `<UserMessage content>` / `<AssistantMessage message sources? citations?>` / `<StreamingMessage stream>` / `<NoAnswerNotice sources onSelect?>`.
- `<ModelSelector models value onChange>`; `<UsageMeter>` — **STUB: hardcoded sample until Phase 2 quotas**, visibly labeled.
- New-chat flow: `/chat` (no id) renders the same page with an empty thread; first send creates the chat, then navigates to `/chat/:id` with `location.state.initialMessage`, which auto-sends exactly once.

- [ ] **Step 1: Write failing tests**

`frontend/src/features/chat/use-tree-selection.test.ts`:

```ts
import { act, renderHook } from '@testing-library/react';

import type { MessageOut } from '@/api/types';

import { useTreeSelection } from './use-tree-selection';

const messages = [
  { id: 'u1', parent_message_id: null, sibling_index: 0, role: 'user', content: 'q', model_id: null, created_at: 't1' },
  { id: 'u1b', parent_message_id: null, sibling_index: 1, role: 'user', content: 'q2', model_id: null, created_at: 't2' },
] as MessageOut[];

test('defaults to newest, select() navigates a branch', () => {
  const { result, rerender } = renderHook(({ msgs }) => useTreeSelection(msgs), {
    initialProps: { msgs: messages },
  });
  expect(result.current.path[0]?.message.id).toBe('u1b');
  act(() => result.current.select('__root__', 'u1'));
  rerender({ msgs: messages });
  expect(result.current.path[0]?.message.id).toBe('u1');
  expect(result.current.path[0]?.position).toBe(0);
  expect(result.current.path[0]?.siblings).toHaveLength(2);
});
```

`frontend/src/features/chat/chat-input.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ChatInput } from './chat-input';

test('Enter sends and clears; Shift+Enter inserts a newline', async () => {
  const onSend = vi.fn();
  const user = userEvent.setup();
  render(<ChatInput onSend={onSend} disabled={false} />);
  const box = screen.getByRole('textbox', { name: 'Message' });
  await user.type(box, 'hello');
  await user.keyboard('{Enter}');
  expect(onSend).toHaveBeenCalledWith('hello');
  expect(box).toHaveValue('');
  await user.type(box, 'a{Shift>}{Enter}{/Shift}b');
  expect(box).toHaveValue('a\nb');
  expect(onSend).toHaveBeenCalledTimes(1);
});

test('whitespace-only content is not sent; disabled blocks sending', async () => {
  const onSend = vi.fn();
  const user = userEvent.setup();
  const { rerender } = render(<ChatInput onSend={onSend} disabled={false} />);
  await user.type(screen.getByRole('textbox', { name: 'Message' }), '   {Enter}');
  expect(onSend).not.toHaveBeenCalled();
  rerender(<ChatInput onSend={onSend} disabled />);
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
});
```

Run: `pnpm test src/features/chat/use-tree-selection src/features/chat/chat-input` — Expected: FAIL.

- [ ] **Step 2: Implement queries and the selection hook**

Append to `frontend/src/features/chat/queries.ts`:

```ts
import { useMutation, useQueryClient } from '@tanstack/react-query'; // merge with existing imports

export function useChat(chatId: string | null) {
  return useQuery({
    queryKey: ['chat', chatId],
    enabled: chatId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/chats/{chat_id}', {
        params: { path: { chat_id: chatId as string } },
      });
      if (error) throw new Error('failed to load chat');
      return data;
    },
  });
}

export function useCreateChat() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { workspace_id: string }) => {
      const { data, error } = await api.POST('/api/v1/chats', { body });
      if (error) throw new Error('failed to create chat');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['chats'] }),
  });
}
```

`frontend/src/features/models/queries.ts`:

```ts
import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useModels() {
  return useQuery({
    queryKey: ['models'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/models');
      if (error) throw new Error('failed to load models');
      return data;
    },
  });
}
```

`frontend/src/features/chat/use-tree-selection.ts`:

```ts
import { useCallback, useMemo, useState } from 'react';

import type { MessageOut } from '@/api/types';

import { selectActivePath, type PathEntry } from './tree';

export function useTreeSelection(messages: readonly MessageOut[] | undefined): {
  path: PathEntry[];
  select: (branchKey: string, id: string) => void;
} {
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const path = useMemo(
    () => selectActivePath(messages ?? [], overrides),
    [messages, overrides],
  );
  const select = useCallback((branchKey: string, id: string) => {
    setOverrides((prev) => ({ ...prev, [branchKey]: id }));
  }, []);
  return { path, select };
}
```

- [ ] **Step 3: Implement the message components**

`frontend/src/features/chat/chat-input.tsx`:

```tsx
import { ArrowUp } from 'lucide-react';
import { useRef, useState, type KeyboardEvent } from 'react';

export function ChatInput({
  onSend,
  disabled,
  placeholder = 'Ask about your documents…',
}: {
  onSend: (content: string) => void;
  disabled: boolean;
  placeholder?: string;
}) {
  const [value, setValue] = useState('');
  const boxRef = useRef<HTMLTextAreaElement>(null);

  const submit = (): void => {
    const content = value.trim();
    if (!content || disabled) return;
    onSend(content);
    setValue('');
    if (boxRef.current) boxRef.current.style.height = 'auto';
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="mx-auto w-full max-w-thread px-4 pb-4">
      <div className="flex items-end gap-2 rounded-xl border border-line bg-bg p-2 shadow-soft">
        <textarea
          ref={boxRef}
          aria-label="Message"
          rows={1}
          value={value}
          placeholder={placeholder}
          onChange={(e) => {
            setValue(e.target.value);
            e.target.style.height = 'auto';
            e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`;
          }}
          onKeyDown={onKeyDown}
          className="max-h-[200px] flex-1 resize-none bg-transparent px-2 py-1 text-[15px] text-ink outline-none placeholder:text-muted"
        />
        <button
          type="button"
          aria-label="Send"
          disabled={disabled || value.trim() === ''}
          onClick={submit}
          className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full bg-ink text-bg disabled:opacity-40"
        >
          <ArrowUp className="h-4 w-4" aria-hidden />
        </button>
      </div>
    </div>
  );
}
```

`frontend/src/features/chat/user-message.tsx`:

```tsx
import { type ReactNode } from 'react';

export function UserMessage({ content, footer }: { content: string; footer?: ReactNode }) {
  return (
    <div className="flex flex-col items-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-subtle px-3 py-2 text-[15px] leading-[1.6] text-ink">
        {content}
      </div>
      {footer}
    </div>
  );
}
```

`frontend/src/features/chat/assistant-message.tsx`:

```tsx
import { useState, type ReactNode } from 'react';

import type { SourceRef } from '@/api/types';
import { Markdown } from '@/components/markdown/markdown';

import { CitationProvider } from './citation-context';
import { NoAnswerNotice } from './no-answer-notice';
import { SourcePanel } from './source-panel';

export function AssistantMessage({
  content,
  sources,
  noAnswer = false,
  footer,
}: {
  content: string;
  sources: SourceRef[];
  noAnswer?: boolean;
  footer?: ReactNode;
}) {
  const [highlightedN, setHighlightedN] = useState<number | null>(null);
  return (
    <div>
      <CitationProvider onCitationClick={setHighlightedN}>
        <Markdown content={content} />
      </CitationProvider>
      {noAnswer ? (
        <NoAnswerNotice />
      ) : (
        <SourcePanel sources={sources} highlightedN={highlightedN} onSelect={setHighlightedN} />
      )}
      {noAnswer && sources.length > 0 ? (
        <div className="mt-2">
          <p className="mb-1 text-[12px] text-muted">Nearest sources</p>
          <SourcePanel sources={sources} highlightedN={highlightedN} onSelect={setHighlightedN} />
        </div>
      ) : null}
      {footer}
    </div>
  );
}
```

`frontend/src/features/chat/no-answer-notice.tsx`:

```tsx
import { SearchX } from 'lucide-react';

export function NoAnswerNotice() {
  return (
    <div className="mt-2 flex items-center gap-2 rounded-md border border-line bg-raised px-3 py-2 text-[13px] text-secondary">
      <SearchX className="h-4 w-4 shrink-0 text-muted" aria-hidden />
      <span>No confident answer in this workspace's documents for that question.</span>
    </div>
  );
}
```

`frontend/src/features/chat/streaming-message.tsx`:

```tsx
import { Spinner } from '@/components/ui/spinner';

import { AssistantMessage } from './assistant-message';
import type { ChatStreamState } from './use-chat-stream';
import { UserMessage } from './user-message';

export function StreamingMessage({ stream }: { stream: ChatStreamState }) {
  return (
    <>
      {stream.pendingUserContent ? <UserMessage content={stream.pendingUserContent} /> : null}
      {stream.status === 'retrieving' ? <Spinner label="Searching documents…" /> : null}
      {stream.status === 'streaming' || stream.status === 'done' ? (
        <AssistantMessage
          content={stream.text}
          sources={stream.sources}
          noAnswer={stream.noAnswer}
        />
      ) : null}
      {stream.status === 'error' ? (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-[13px] text-danger">
          {stream.errorDetail ?? 'Something went wrong.'}
        </p>
      ) : null}
    </>
  );
}
```

`frontend/src/features/chat/model-selector.tsx`:

```tsx
import type { ModelOut } from '@/api/types';
import { NativeSelect } from '@/components/ui/select';

export function ModelSelector({
  models,
  value,
  onChange,
}: {
  models: ModelOut[];
  value: string | null;
  onChange: (id: string) => void;
}) {
  const enabled = models.filter((m) => m.enabled);
  if (enabled.length === 0) return <span className="text-[12px] text-muted">No models</span>;
  return (
    <NativeSelect
      aria-label="Model"
      className="h-7 w-auto min-w-[140px] text-[12px]"
      value={value ?? enabled[0]?.id}
      onChange={(e) => onChange(e.target.value)}
    >
      {enabled.map((m) => (
        <option key={m.id} value={m.id}>
          {m.display_name}
        </option>
      ))}
    </NativeSelect>
  );
}
```

`frontend/src/features/chat/usage-meter.tsx`:

```tsx
// STUB(phase2-quotas): hardcoded sample values until the quotas module lands.
// Visibly labeled so nobody mistakes it for live data.
export function UsageMeter() {
  return (
    <span
      className="text-[12px] tabular-nums text-muted"
      title="Sample data — usage tracking arrives with Phase 2 quotas"
    >
      12.3k / 100k tokens (sample)
    </span>
  );
}
```

- [ ] **Step 4: Implement `frontend/src/features/chat/chat-page.tsx`**

```tsx
import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';

import { TopBar } from '@/components/layout/top-bar';
import { Spinner } from '@/components/ui/spinner';

import { useModels } from '@/features/models/queries';
import { useWorkspaces } from '@/features/workspaces/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';

import { AssistantMessage } from './assistant-message';
import { ChatInput } from './chat-input';
import { ModelSelector } from './model-selector';
import { useChat, useCreateChat } from './queries';
import { StreamingMessage } from './streaming-message';
import { UsageMeter } from './usage-meter';
import { UserMessage } from './user-message';
import { useChatStream } from './use-chat-stream';
import { useTreeSelection } from './use-tree-selection';

export function ChatPage() {
  const { chatId = null } = useParams<{ chatId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const { workspaceId } = useWorkspace();
  const { data: workspaces } = useWorkspaces();
  const { data: models } = useModels();
  const chatQuery = useChat(chatId);
  const createChat = useCreateChat();
  const stream = useChatStream(chatId);
  const { path, select } = useTreeSelection(chatQuery.data?.messages);
  const [modelId, setModelId] = useState<string | null>(null);

  const workspace = workspaces?.find((w) => w.id === workspaceId);
  // Workspace default model, else first enabled (see task assumption).
  const workspaceDefault =
    (workspace && 'default_model_id' in workspace
      ? (workspace as { default_model_id?: string | null }).default_model_id
      : null) ?? null;
  const effectiveModelId = modelId ?? workspaceDefault;

  // New-chat handoff: /chat → create → navigate with initialMessage → auto-send once.
  const initialSentRef = useRef(false);
  const initialMessage = (location.state as { initialMessage?: string } | null)?.initialMessage;
  useEffect(() => {
    if (chatId && initialMessage && !initialSentRef.current) {
      initialSentRef.current = true;
      stream.send(initialMessage, null, effectiveModelId);
      navigate(location.pathname, { replace: true, state: null }); // consume the state
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per mount/handoff
  }, [chatId, initialMessage]);

  // Once the refetched tree contains the streamed message, drop the streamed block.
  const streamedInTree = useMemo(
    () =>
      stream.doneMessageId !== null &&
      (chatQuery.data?.messages ?? []).some((m) => m.id === stream.doneMessageId),
    [chatQuery.data, stream.doneMessageId],
  );
  useEffect(() => {
    if (streamedInTree) stream.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset is stable
  }, [streamedInTree]);

  const onSend = (content: string): void => {
    if (chatId) {
      stream.send(content, null, effectiveModelId);
      return;
    }
    if (!workspaceId) return;
    createChat.mutate(
      { workspace_id: workspaceId },
      { onSuccess: (chat) => navigate(`/chat/${chat.id}`, { state: { initialMessage: content } }) },
    );
  };

  const busy = stream.status === 'retrieving' || stream.status === 'streaming';
  const showStreamBlock = stream.status !== 'idle' && !streamedInTree;

  return (
    <>
      <TopBar
        title={chatQuery.data?.title || 'New chat'}
        actions={
          <>
            <UsageMeter />
            <ModelSelector models={models ?? []} value={effectiveModelId} onChange={setModelId} />
          </>
        }
      />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-thread space-y-5 px-4 py-6">
          {chatId && chatQuery.isPending ? <Spinner label="Loading chat…" /> : null}
          {path.map((entry) =>
            entry.message.role === 'user' ? (
              <UserMessage key={entry.message.id} content={entry.message.content} />
            ) : (
              <AssistantMessage
                key={entry.message.id}
                content={entry.message.content}
                sources={[]}
              />
            ),
          )}
          {showStreamBlock ? <StreamingMessage stream={stream} /> : null}
          {!chatId && path.length === 0 && stream.status === 'idle' ? (
            <p className="pt-16 text-center text-[15px] text-secondary">
              Ask a question about the documents in this workspace.
            </p>
          ) : null}
        </div>
      </div>
      <ChatInput onSend={onSend} disabled={busy || (!chatId && createChat.isPending)} />
    </>
  );
}
```

Note: persisted assistant messages render with `sources={[]}` until Plan C's `GET /chats/{id}` includes per-message citations (assumed as `MessageOut.citations` or a `citations` sibling list — reconcile in Task 11 Step 3 where the actions row wires real data; if the field is absent, chips render without a source panel for historical messages, which is acceptable for Phase 1 and noted in the commit body).

In `frontend/src/app/router.tsx`, replace both chat `ComingSoon` routes with `<ChatPage />` (import it).

- [ ] **Step 5: Run tests, gates, manual streaming smoke**

Run: `pnpm test && pnpm lint && pnpm typecheck`
Expected: all PASS.

Manual (full stack up, a model configured, a document indexed): `pnpm dev` → new chat → ask a question → watch retrieval spinner → streamed tokens → citation chips `[1]` → source chips under the answer; ask an unanswerable question → no-answer notice. Verify the streamed block is replaced seamlessly by the persisted tree (no duplicate flash).

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat: chat page with streaming thread, citations, model selector, usage stub"
```

---

### Task 11: Message actions — copy, edit-in-place, regenerate, sibling navigation

**Files:**
- Create: `frontend/src/features/chat/message-actions.tsx`, `chat/edit-message-form.tsx`
- Modify: `frontend/src/features/chat/chat-page.tsx` (wire actions + edit state)
- Test: `frontend/src/features/chat/message-actions.test.tsx`, `chat/edit-message-form.test.tsx`

**Interfaces:**
- `<MessageActions entry disabled onSelectSibling(branchKey, id) onEdit? onRegenerate?>` — the actions row under every message (phase1 spec §5): **Copy** always (clipboard + toast); **Edit** button only when `onEdit` given (user messages); **Regenerate** only when `onRegenerate` given (assistant messages); **`< n/n >` sibling nav** only when `entry.siblings.length > 1`, prev/next call `onSelectSibling(branchKeyOf(message), targetId)` and disable at the ends. Ghost buttons, `text-muted` icons, visible on hover/focus-within but always keyboard-reachable (opacity, not display).
- `<EditMessageForm initial onCancel onSend(content)>` — in-place textarea prefilled with the original, Cancel/Send buttons, Escape cancels, trims + rejects empty.
- Wiring semantics: Edit → `stream.send(newContent, editedMessage.parent_message_id, modelId)` (creates a **sibling** of the edited message per phase1 spec §2.1); Regenerate → `stream.regenerate(assistantMessage.id)` (new assistant sibling). Both end in a `done` → invalidate → newest-sibling path shows the new branch automatically; `< n/n >` reaches the old one.

- [ ] **Step 1: Write failing tests**

`frontend/src/features/chat/message-actions.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { MessageOut } from '@/api/types';

import { MessageActions } from './message-actions';
import type { PathEntry } from './tree';

function entryFor(over: Partial<MessageOut>, siblings: string[] = ['m1'], position = 0): PathEntry {
  return {
    message: {
      id: 'm1',
      parent_message_id: 'p1',
      sibling_index: 0,
      role: 'user',
      content: 'the content',
      model_id: null,
      created_at: 't',
      ...over,
    } as MessageOut,
    siblings,
    position,
  };
}

test('copy writes the message content to the clipboard', async () => {
  // fireEvent, not userEvent: userEvent.setup() installs its own clipboard stub
  // which would shadow this spy.
  const { fireEvent } = await import('@testing-library/react');
  const writeText = vi.fn(async () => {});
  Object.assign(navigator, { clipboard: { writeText } });
  render(
    <MessageActions entry={entryFor({})} disabled={false} onSelectSibling={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Copy message' }));
  await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith('the content'));
});

test('edit and regenerate buttons appear only when their handlers exist', () => {
  const { rerender } = render(
    <MessageActions entry={entryFor({})} disabled={false} onSelectSibling={vi.fn()} onEdit={vi.fn()} />,
  );
  expect(screen.getByRole('button', { name: 'Edit message' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Regenerate response' })).not.toBeInTheDocument();
  rerender(
    <MessageActions
      entry={entryFor({ role: 'assistant' })}
      disabled={false}
      onSelectSibling={vi.fn()}
      onRegenerate={vi.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: 'Regenerate response' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Edit message' })).not.toBeInTheDocument();
});

test('sibling nav renders n/n and navigates by branch key', async () => {
  const onSelectSibling = vi.fn();
  const user = userEvent.setup();
  render(
    <MessageActions
      entry={entryFor({ id: 'm2', sibling_index: 1 }, ['m1', 'm2', 'm3'], 1)}
      disabled={false}
      onSelectSibling={onSelectSibling}
    />,
  );
  expect(screen.getByText('2/3')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Previous version' }));
  expect(onSelectSibling).toHaveBeenCalledWith('p1', 'm1');
  await user.click(screen.getByRole('button', { name: 'Next version' }));
  expect(onSelectSibling).toHaveBeenCalledWith('p1', 'm3');
});

test('single sibling hides the nav; ends disable their arrow', () => {
  render(
    <MessageActions
      entry={entryFor({}, ['m1', 'm2'], 0)}
      disabled={false}
      onSelectSibling={vi.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: 'Previous version' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Next version' })).toBeEnabled();
});
```

`frontend/src/features/chat/edit-message-form.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { EditMessageForm } from './edit-message-form';

test('prefills, edits, sends trimmed content', async () => {
  const onSend = vi.fn();
  const user = userEvent.setup();
  render(<EditMessageForm initial="old question" onCancel={vi.fn()} onSend={onSend} />);
  const box = screen.getByRole('textbox', { name: 'Edit message' });
  expect(box).toHaveValue('old question');
  await user.clear(box);
  await user.type(box, '  new question  ');
  await user.click(screen.getByRole('button', { name: 'Send' }));
  expect(onSend).toHaveBeenCalledWith('new question');
});

test('cancel button and Escape both cancel; empty content cannot send', async () => {
  const onCancel = vi.fn();
  const onSend = vi.fn();
  const user = userEvent.setup();
  render(<EditMessageForm initial="x" onCancel={onCancel} onSend={onSend} />);
  const box = screen.getByRole('textbox', { name: 'Edit message' });
  await user.clear(box);
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  await user.type(box, '{Escape}');
  expect(onCancel).toHaveBeenCalledTimes(2);
  expect(onSend).not.toHaveBeenCalled();
});
```

Run: `pnpm test src/features/chat/message-actions src/features/chat/edit-message-form` — Expected: FAIL.

- [ ] **Step 2: Implement**

`frontend/src/features/chat/message-actions.tsx`:

```tsx
import { ChevronLeft, ChevronRight, Copy, Pencil, RotateCcw } from 'lucide-react';

import { toast } from '@/components/ui/toaster';

import { branchKeyOf, type PathEntry } from './tree';

function ActionButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="rounded-sm p-1 text-muted hover:bg-subtle hover:text-ink disabled:opacity-40 disabled:hover:bg-transparent"
    >
      {children}
    </button>
  );
}

export function MessageActions({
  entry,
  disabled,
  onSelectSibling,
  onEdit,
  onRegenerate,
}: {
  entry: PathEntry;
  disabled: boolean;
  onSelectSibling: (branchKey: string, id: string) => void;
  onEdit?: () => void;
  onRegenerate?: () => void;
}) {
  const { message, siblings, position } = entry;
  const branchKey = branchKeyOf(message);
  const prevId = position > 0 ? siblings[position - 1] : undefined;
  const nextId = position < siblings.length - 1 ? siblings[position + 1] : undefined;

  const copy = async (): Promise<void> => {
    await navigator.clipboard.writeText(message.content);
    toast('Copied to clipboard');
  };

  return (
    <div
      className="mt-1 flex items-center gap-0.5 opacity-60 focus-within:opacity-100 hover:opacity-100"
      aria-label="Message actions"
    >
      {siblings.length > 1 ? (
        <span className="mr-1 flex items-center gap-0.5">
          <ActionButton
            label="Previous version"
            disabled={disabled || !prevId}
            onClick={() => prevId && onSelectSibling(branchKey, prevId)}
          >
            <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
          </ActionButton>
          <span className="text-[11px] tabular-nums text-muted">
            {position + 1}/{siblings.length}
          </span>
          <ActionButton
            label="Next version"
            disabled={disabled || !nextId}
            onClick={() => nextId && onSelectSibling(branchKey, nextId)}
          >
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
          </ActionButton>
        </span>
      ) : null}
      <ActionButton label="Copy message" onClick={() => void copy()}>
        <Copy className="h-3.5 w-3.5" aria-hidden />
      </ActionButton>
      {onEdit ? (
        <ActionButton label="Edit message" disabled={disabled} onClick={onEdit}>
          <Pencil className="h-3.5 w-3.5" aria-hidden />
        </ActionButton>
      ) : null}
      {onRegenerate ? (
        <ActionButton label="Regenerate response" disabled={disabled} onClick={onRegenerate}>
          <RotateCcw className="h-3.5 w-3.5" aria-hidden />
        </ActionButton>
      ) : null}
    </div>
  );
}
```

`frontend/src/features/chat/edit-message-form.tsx`:

```tsx
import { useState, type KeyboardEvent } from 'react';

import { Button } from '@/components/ui/button';

export function EditMessageForm({
  initial,
  onCancel,
  onSend,
}: {
  initial: string;
  onCancel: () => void;
  onSend: (content: string) => void;
}) {
  const [value, setValue] = useState(initial);
  const trimmed = value.trim();

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Escape') onCancel();
  };

  return (
    <div className="rounded-lg border border-line bg-subtle p-2">
      <textarea
        aria-label="Edit message"
        autoFocus
        rows={3}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        className="w-full resize-y bg-transparent px-1 py-0.5 text-[15px] leading-[1.6] text-ink outline-none"
      />
      <div className="mt-1 flex justify-end gap-2">
        <Button size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          size="sm"
          variant="primary"
          disabled={trimmed === ''}
          onClick={() => onSend(trimmed)}
        >
          Send
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Wire into `chat-page.tsx`**

Add state and replace the `path.map(...)` block:

```tsx
import { EditMessageForm } from './edit-message-form';
import { MessageActions } from './message-actions';
// inside ChatPage:
const [editingId, setEditingId] = useState<string | null>(null);

// in the JSX, replacing the previous path.map block:
{path.map((entry) => {
  const m = entry.message;
  if (m.role === 'user') {
    if (editingId === m.id) {
      return (
        <EditMessageForm
          key={m.id}
          initial={m.content}
          onCancel={() => setEditingId(null)}
          onSend={(content) => {
            setEditingId(null);
            // Sibling of the edited message: same parent (phase1 spec §2.1)
            stream.send(content, m.parent_message_id ?? null, effectiveModelId);
          }}
        />
      );
    }
    return (
      <UserMessage
        key={m.id}
        content={m.content}
        footer={
          <MessageActions
            entry={entry}
            disabled={busy}
            onSelectSibling={select}
            onEdit={() => setEditingId(m.id)}
          />
        }
      />
    );
  }
  return (
    <AssistantMessage
      key={m.id}
      content={m.content}
      sources={[]}
      footer={
        <MessageActions
          entry={entry}
          disabled={busy}
          onSelectSibling={select}
          onRegenerate={() => stream.regenerate(m.id)}
        />
      }
    />
  );
})}
```

(`busy` already exists. Reconcile persisted-message sources here if `GET /chats/{id}` exposes citations per message — see Task 10's note.)

- [ ] **Step 4: Run tests, gates, manual branch smoke**

Run: `pnpm test && pnpm lint && pnpm typecheck`
Expected: all PASS.

Manual: in a chat with an answer — edit the question → new answer streams → `2/2` appears on the user row → `<` flips back to the original question **and its original answer**; regenerate an answer → `2/2` on the assistant row; copy shows the toast.

- [ ] **Step 5: Commit**

```bash
git add frontend/
git commit -m "feat: message actions row - copy, edit-in-place siblings, regenerate, n/n navigation"
```

---

### Task 12: Documents page — drag-drop upload with progress, status table, delete

**Files:**
- Create: `frontend/src/features/documents/status.ts`, `documents/upload.ts`, `documents/queries.ts`, `documents/dropzone.tsx`, `documents/document-row.tsx`, `documents/documents-page.tsx`
- Modify: `frontend/src/app/router.tsx` (mount `DocumentsPage`)
- Test: `frontend/src/features/documents/status.test.ts`, `documents/upload.test.ts`

**Interfaces:**
- `status.ts` (pure): `statusPresentation(doc: Pick<DocumentOut,'status'>): { tone: StatusTone; label: string }` (`indexed`→success/"Indexed", `queued|processing`→accent/"Processing", `failed`→danger/"Failed"); `shouldPoll(docs: DocumentOut[] | undefined): boolean` (any queued/processing); `formatBytes(n: number): string`.
- `upload.ts`: `uploadDocuments(workspaceId, files: File[], onProgress: (pct: number) => void): Promise<void>` — **XHR, not fetch** (fetch has no upload progress); multipart field `files`; attaches the bearer token; on 401 does one `refreshAccessToken()` and retries once; rejects with the problem+json `detail` on failure.
- `queries.ts`: `useDocuments(workspaceId)` — `['documents', workspaceId]`, `refetchInterval: (q) => shouldPoll(q.state.data) ? 2500 : false` (live status while processing); `useDeleteDocument(workspaceId)` — DELETE, invalidates the list.
- `<Dropzone onFiles(files) disabled>` — drag-over highlight (`border-accent bg-accent-soft`), click/keyboard opens the file picker (PDF/DOCX/XLSX/CSV/TXT/MD accept list).

- [ ] **Step 1: Write failing tests**

`frontend/src/features/documents/status.test.ts`:

```ts
import type { DocumentOut } from '@/api/types';

import { formatBytes, shouldPoll, statusPresentation } from './status';

test.each([
  ['indexed', 'success', 'Indexed'],
  ['queued', 'accent', 'Processing'],
  ['processing', 'accent', 'Processing'],
  ['failed', 'danger', 'Failed'],
] as const)('%s → %s pill "%s"', (status, tone, label) => {
  expect(statusPresentation({ status })).toEqual({ tone, label });
});

test('shouldPoll only while something is in flight', () => {
  const doc = (status: DocumentOut['status']) => ({ status }) as DocumentOut;
  expect(shouldPoll(undefined)).toBe(false);
  expect(shouldPoll([doc('indexed'), doc('failed')])).toBe(false);
  expect(shouldPoll([doc('indexed'), doc('processing')])).toBe(true);
  expect(shouldPoll([doc('queued')])).toBe(true);
});

test('formatBytes', () => {
  expect(formatBytes(512)).toBe('512 B');
  expect(formatBytes(2048)).toBe('2.0 KB');
  expect(formatBytes(10_485_760)).toBe('10.0 MB');
});
```

`frontend/src/features/documents/upload.test.ts`:

```ts
import { setAccessToken } from '@/lib/auth-store';

import { uploadDocuments } from './upload';

class FakeXhr {
  static instances: FakeXhr[] = [];
  upload = { onprogress: null as ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  status = 201;
  responseText = '{}';
  headers: Record<string, string> = {};
  opened: [string, string] | null = null;
  body: FormData | null = null;
  open(method: string, url: string) {
    this.opened = [method, url];
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  send(body: FormData) {
    this.body = body;
    FakeXhr.instances.push(this);
  }
}

beforeEach(() => {
  FakeXhr.instances = [];
  vi.stubGlobal('XMLHttpRequest', FakeXhr as unknown as typeof XMLHttpRequest);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('sends multipart "files" with bearer token and reports progress', async () => {
  setAccessToken('tok');
  const onProgress = vi.fn();
  const promise = uploadDocuments('w1', [new File(['x'], 'a.pdf')], onProgress);
  const xhr = FakeXhr.instances[0]!;
  expect(xhr.opened).toEqual(['POST', '/api/v1/workspaces/w1/documents']);
  expect(xhr.headers.Authorization).toBe('Bearer tok');
  expect(xhr.body?.getAll('files')).toHaveLength(1);
  xhr.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
  xhr.onload?.();
  await promise;
  expect(onProgress).toHaveBeenCalledWith(50);
});

test('rejects with problem detail on failure', async () => {
  const promise = uploadDocuments('w1', [new File(['x'], 'a.pdf')], vi.fn());
  const xhr = FakeXhr.instances[0]!;
  xhr.status = 415;
  xhr.responseText = JSON.stringify({ detail: 'unsupported file type' });
  xhr.onload?.();
  await expect(promise).rejects.toThrow('unsupported file type');
});

test('401 → refresh → single retry', async () => {
  setAccessToken('stale');
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ access_token: 'fresh' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  );
  const promise = uploadDocuments('w1', [new File(['x'], 'a.pdf')], vi.fn());
  const first = FakeXhr.instances[0]!;
  first.status = 401;
  first.onload?.();
  await vi.waitFor(() => expect(FakeXhr.instances).toHaveLength(2));
  const second = FakeXhr.instances[1]!;
  expect(second.headers.Authorization).toBe('Bearer fresh');
  second.onload?.();
  await promise;
});
```

Run: `pnpm test src/features/documents` — Expected: FAIL.

- [ ] **Step 2: Implement pure helpers and upload**

`frontend/src/features/documents/status.ts`:

```ts
import type { DocumentOut } from '@/api/types';
import type { StatusTone } from '@/components/ui/status-pill';

export function statusPresentation(doc: Pick<DocumentOut, 'status'>): {
  tone: StatusTone;
  label: string;
} {
  switch (doc.status) {
    case 'indexed':
      return { tone: 'success', label: 'Indexed' };
    case 'failed':
      return { tone: 'danger', label: 'Failed' };
    default:
      return { tone: 'accent', label: 'Processing' };
  }
}

export function shouldPoll(docs: readonly DocumentOut[] | undefined): boolean {
  return (docs ?? []).some((d) => d.status === 'queued' || d.status === 'processing');
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
```

`frontend/src/features/documents/upload.ts`:

```ts
import { refreshAccessToken } from '@/api/client';
import { getAccessToken } from '@/lib/auth-store';

function attempt(workspaceId: string, form: FormData, onProgress: (pct: number) => void): Promise<number> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/v1/workspaces/${workspaceId}/documents`);
    const token = getAccessToken();
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.status);
        return;
      }
      if (xhr.status === 401) {
        resolve(401);
        return;
      }
      let detail = `upload failed (${xhr.status})`;
      try {
        const problem = JSON.parse(xhr.responseText) as { detail?: string };
        if (problem.detail) detail = problem.detail;
      } catch {
        /* keep default */
      }
      reject(new Error(detail));
    };
    xhr.onerror = () => reject(new Error('network error during upload'));
    xhr.send(form);
  });
}

/** XHR (fetch has no upload progress). Retries once after a token refresh on 401. */
export async function uploadDocuments(
  workspaceId: string,
  files: File[],
  onProgress: (pct: number) => void,
): Promise<void> {
  const form = new FormData();
  for (const file of files) form.append('files', file);
  const status = await attempt(workspaceId, form, onProgress);
  if (status === 401) {
    if (!(await refreshAccessToken())) throw new Error('session expired');
    const retryStatus = await attempt(workspaceId, form, onProgress);
    if (retryStatus === 401) throw new Error('session expired');
  }
}
```

`frontend/src/features/documents/queries.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { DocumentOut } from '@/api/types';

import { shouldPoll } from './status';

export function useDocuments(workspaceId: string | null) {
  return useQuery({
    queryKey: ['documents', workspaceId],
    enabled: workspaceId !== null,
    refetchInterval: (query) =>
      shouldPoll(query.state.data as DocumentOut[] | undefined) ? 2500 : false,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/workspaces/{workspace_id}/documents', {
        params: { path: { workspace_id: workspaceId as string } },
      });
      if (error) throw new Error('failed to load documents');
      return data;
    },
  });
}

export function useDeleteDocument(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (documentId: string) => {
      const { error } = await api.DELETE('/api/v1/documents/{document_id}', {
        params: { path: { document_id: documentId } },
      });
      if (error) throw new Error('failed to delete document');
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['documents', workspaceId] }),
  });
}
```

- [ ] **Step 3: Implement components**

`frontend/src/features/documents/dropzone.tsx`:

```tsx
import { Upload } from 'lucide-react';
import { useRef, useState, type DragEvent } from 'react';

import { cn } from '@/lib/cn';

const ACCEPT = '.pdf,.docx,.xlsx,.csv,.txt,.md';

export function Dropzone({
  onFiles,
  disabled,
}: {
  onFiles: (files: File[]) => void;
  disabled: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const onDrop = (e: DragEvent): void => {
    e.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) onFiles(files);
  };

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        aria-label="Upload documents"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={cn(
          'flex w-full flex-col items-center gap-1.5 rounded-lg border border-dashed border-line-strong bg-raised px-4 py-8 text-secondary hover:border-accent',
          dragOver && 'border-accent bg-accent-soft',
          disabled && 'opacity-50',
        )}
      >
        <Upload className="h-5 w-5 text-muted" aria-hidden />
        <span className="text-[13px] font-medium text-ink">
          Drop files here or click to upload
        </span>
        <span className="text-[12px] text-muted">PDF, DOCX, XLSX, CSV, TXT, MD</span>
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length > 0) onFiles(files);
          e.target.value = '';
        }}
      />
    </>
  );
}
```

`frontend/src/features/documents/document-row.tsx`:

```tsx
import { Trash2 } from 'lucide-react';
import { useState } from 'react';

import type { DocumentOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { StatusPill } from '@/components/ui/status-pill';
import { TD, TR } from '@/components/ui/table';

import { formatBytes, statusPresentation } from './status';

export function DocumentRow({
  doc,
  onDelete,
  deleting,
}: {
  doc: DocumentOut;
  onDelete: () => void;
  deleting: boolean;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const { tone, label } = statusPresentation(doc);
  return (
    <TR>
      <TD className="max-w-[320px] truncate font-medium">{doc.filename}</TD>
      <TD className="text-secondary">{formatBytes(doc.size_bytes)}</TD>
      <TD className="text-secondary">{doc.page_count ?? '—'}</TD>
      <TD>
        {doc.status === 'failed' ? (
          <Popover>
            <PopoverTrigger asChild>
              <button type="button" aria-label="Show failure reason">
                <StatusPill tone={tone}>{label}</StatusPill>
              </button>
            </PopoverTrigger>
            <PopoverContent>
              <p className="font-medium text-danger">Ingestion failed</p>
              <p className="mt-1 text-secondary">{doc.error ?? 'Unknown error'}</p>
            </PopoverContent>
          </Popover>
        ) : (
          <StatusPill tone={tone}>{label}</StatusPill>
        )}
      </TD>
      <TD className="text-muted">{new Date(doc.created_at).toLocaleDateString()}</TD>
      <TD className="text-right">
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Delete ${doc.filename}`}
          onClick={() => setConfirmOpen(true)}
        >
          <Trash2 className="h-4 w-4" aria-hidden />
        </Button>
        <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
          <DialogContent
            title="Delete document"
            description={`"${doc.filename}" and all its indexed chunks will be removed. This cannot be undone.`}
          >
            <DialogFooter>
              <Button onClick={() => setConfirmOpen(false)}>Cancel</Button>
              <Button
                variant="danger"
                disabled={deleting}
                onClick={() => {
                  onDelete();
                  setConfirmOpen(false);
                }}
              >
                Delete
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </TD>
    </TR>
  );
}
```

`frontend/src/features/documents/documents-page.tsx`:

```tsx
import { useState } from 'react';

import { TopBar } from '@/components/layout/top-bar';
import { Spinner } from '@/components/ui/spinner';
import { Table, TBody, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';

import { useWorkspace } from '@/features/workspaces/workspace-context';

import { DocumentRow } from './document-row';
import { Dropzone } from './dropzone';
import { useDeleteDocument, useDocuments } from './queries';
import { uploadDocuments } from './upload';

interface UploadItem {
  key: string;
  names: string;
  pct: number;
}

export function DocumentsPage() {
  const { workspaceId } = useWorkspace();
  const documents = useDocuments(workspaceId);
  const deleteDocument = useDeleteDocument(workspaceId);
  const [uploads, setUploads] = useState<UploadItem[]>([]);

  const onFiles = (files: File[]): void => {
    if (!workspaceId) return;
    const key = crypto.randomUUID();
    const names = files.map((f) => f.name).join(', ');
    setUploads((prev) => [...prev, { key, names, pct: 0 }]);
    uploadDocuments(workspaceId, files, (pct) =>
      setUploads((prev) => prev.map((u) => (u.key === key ? { ...u, pct } : u))),
    )
      .then(() => void documents.refetch())
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setUploads((prev) => prev.filter((u) => u.key !== key)));
  };

  return (
    <>
      <TopBar title="Documents" />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-4xl space-y-4">
          <Dropzone onFiles={onFiles} disabled={!workspaceId} />
          {uploads.map((u) => (
            <div key={u.key} className="rounded-md border border-line bg-raised px-3 py-2">
              <div className="mb-1 flex justify-between text-[12px]">
                <span className="truncate text-secondary">Uploading {u.names}</span>
                <span className="tabular-nums text-muted">{u.pct}%</span>
              </div>
              <div className="h-1 overflow-hidden rounded-full bg-subtle">
                <div className="h-full bg-accent transition-all" style={{ width: `${u.pct}%` }} />
              </div>
            </div>
          ))}
          {documents.isPending && workspaceId ? <Spinner label="Loading documents…" /> : null}
          {documents.data && documents.data.length > 0 ? (
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Size</TH>
                  <TH>Pages</TH>
                  <TH>Status</TH>
                  <TH>Uploaded</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {documents.data.map((doc) => (
                  <DocumentRow
                    key={doc.id}
                    doc={doc}
                    deleting={deleteDocument.isPending}
                    onDelete={() => deleteDocument.mutate(doc.id)}
                  />
                ))}
              </TBody>
            </Table>
          ) : null}
          {documents.data?.length === 0 && uploads.length === 0 ? (
            <p className="pt-4 text-center text-[13px] text-secondary">
              No documents yet — upload some to make them searchable.
            </p>
          ) : null}
        </div>
      </div>
    </>
  );
}
```

Mount in `router.tsx`: replace the Documents `ComingSoon` with `<DocumentsPage />`.

- [ ] **Step 4: Run tests, gates, manual smoke**

Run: `pnpm test && pnpm lint && pnpm typecheck`
Expected: all PASS.

Manual (full stack): upload a PDF → progress bar → row appears "Processing" (accent pill) → flips to "Indexed" without a manual reload (polling); upload an empty/broken file → "Failed" pill → click shows the reason popover; delete → confirm dialog → row disappears.

- [ ] **Step 5: Commit**

```bash
git add frontend/
git commit -m "feat: documents page with drag-drop progress upload, live status pills, delete"
```

---

### Task 13: Admin › Users — invite, deactivate, role change

**Files:**
- Create: `frontend/src/features/admin/users/queries.ts`, `users/invite-dialog.tsx`, `users/users-page.tsx`
- Modify: `frontend/src/app/router.tsx` (mount `UsersPage`)
- Test: `frontend/src/features/admin/users/invite-dialog.test.tsx`

**Interfaces:**
- `useUsers()` — `['users']` → GET `/api/v1/users`; `usePatchUser()` — PATCH `/users/{user_id}` `{active?, role?}`, invalidates `['users']`; `useInvite()` — POST `/auth/invitations` `{email, role}` → `{invite_token}`.
- `<InviteDialog open onOpenChange>` — email + role select (`user`/`admin`); after create shows the one-time invite link `${location.origin}/invite?token=…` with a copy button (backend has no mailer in Phase 1 — the admin delivers the link).
- Users table: email, role (NativeSelect inline change, disabled for superadmin rows), status pill (Active/Deactivated), deactivate/reactivate with confirm dialog.

- [ ] **Step 1: Write the failing test**

`frontend/src/features/admin/users/invite-dialog.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { InviteDialog } from './invite-dialog';

function renderDialog() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ invite_token: 'raw-tok-123' }), {
        status: 201,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <InviteDialog open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('creates an invitation and reveals the one-time link', async () => {
  const user = userEvent.setup();
  renderDialog();
  await user.type(screen.getByLabelText('Email'), 'new@acme.com');
  await user.selectOptions(screen.getByLabelText('Role'), 'admin');
  await user.click(screen.getByRole('button', { name: 'Send invite' }));
  const link = await screen.findByText(/\/invite\?token=raw-tok-123/);
  expect(link).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Copy link' })).toBeInTheDocument();
});
```

Run: `pnpm test src/features/admin/users` — Expected: FAIL.

- [ ] **Step 2: Implement queries**

`frontend/src/features/admin/users/queries.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useUsers() {
  return useQuery({
    queryKey: ['users'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/users');
      if (error) throw new Error('failed to load users');
      return data;
    },
  });
}

export function usePatchUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      userId: string;
      body: { active?: boolean; role?: 'admin' | 'user' };
    }) => {
      const { data, error } = await api.PATCH('/api/v1/users/{user_id}', {
        params: { path: { user_id: input.userId } },
        body: input.body,
      });
      if (error) throw new Error('failed to update user');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['users'] }),
  });
}

export function useInvite() {
  return useMutation({
    mutationFn: async (body: { email: string; role: 'admin' | 'user' }) => {
      const { data, error } = await api.POST('/api/v1/auth/invitations', { body });
      if (error) throw new Error('failed to create invitation');
      return data;
    },
  });
}
```

- [ ] **Step 3: Implement components**

`frontend/src/features/admin/users/invite-dialog.tsx`:

```tsx
import { useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';

import { useInvite } from './queries';

export function InviteDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const invite = useInvite();
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<'admin' | 'user'>('user');

  const close = (next: boolean): void => {
    if (!next) {
      invite.reset();
      setEmail('');
      setRole('user');
    }
    onOpenChange(next);
  };

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    invite.mutate({ email, role });
  };

  const inviteLink = invite.data
    ? `${window.location.origin}/invite?token=${invite.data.invite_token}`
    : null;

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title="Invite a user"
        description="Phase 1 has no mailer — copy the link and send it yourself. It is shown once."
      >
        {inviteLink ? (
          <div className="space-y-3">
            <p className="break-all rounded-md border border-line bg-subtle p-2 font-mono text-[12px] text-ink">
              {inviteLink}
            </p>
            <DialogFooter>
              <Button
                variant="primary"
                onClick={() => {
                  void navigator.clipboard.writeText(inviteLink);
                  toast('Invite link copied');
                }}
              >
                Copy link
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="space-y-3">
            <div>
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="invite-role">Role</Label>
              <NativeSelect
                id="invite-role"
                value={role}
                onChange={(e) => setRole(e.target.value as 'admin' | 'user')}
              >
                <option value="user">User</option>
                <option value="admin">Admin</option>
              </NativeSelect>
            </div>
            {invite.isError ? (
              <p role="alert" className="text-[12px] text-danger">
                {invite.error.message}
              </p>
            ) : null}
            <DialogFooter>
              <Button onClick={() => close(false)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={invite.isPending}>
                Send invite
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
```

`frontend/src/features/admin/users/users-page.tsx`:

```tsx
import { UserPlus } from 'lucide-react';
import { useState } from 'react';

import type { UserOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';

import { InviteDialog } from './invite-dialog';
import { usePatchUser, useUsers } from './queries';

export function UsersPage() {
  const users = useUsers();
  const patchUser = usePatchUser();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [confirmUser, setConfirmUser] = useState<UserOut | null>(null);

  return (
    <>
      <TopBar
        title="Users"
        actions={
          <Button variant="primary" size="sm" onClick={() => setInviteOpen(true)}>
            <UserPlus className="h-3.5 w-3.5" aria-hidden /> Invite
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-3xl">
          {users.isPending ? <Spinner label="Loading users…" /> : null}
          {users.data ? (
            <Table>
              <THead>
                <TR>
                  <TH>Email</TH>
                  <TH>Role</TH>
                  <TH>Status</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {users.data.map((user) => (
                  <TR key={user.id}>
                    <TD className="font-medium">{user.email}</TD>
                    <TD>
                      {user.role === 'superadmin' ? (
                        <span className="text-secondary">superadmin</span>
                      ) : (
                        <NativeSelect
                          aria-label={`Role for ${user.email}`}
                          className="w-28"
                          value={user.role}
                          disabled={patchUser.isPending}
                          onChange={(e) =>
                            patchUser.mutate({
                              userId: user.id,
                              body: { role: e.target.value as 'admin' | 'user' },
                            })
                          }
                        >
                          <option value="user">User</option>
                          <option value="admin">Admin</option>
                        </NativeSelect>
                      )}
                    </TD>
                    <TD>
                      <StatusPill tone={user.active ? 'success' : 'danger'}>
                        {user.active ? 'Active' : 'Deactivated'}
                      </StatusPill>
                    </TD>
                    <TD className="text-right">
                      {user.role !== 'superadmin' ? (
                        <Button size="sm" onClick={() => setConfirmUser(user)}>
                          {user.active ? 'Deactivate' : 'Reactivate'}
                        </Button>
                      ) : null}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : null}
        </div>
      </div>
      <InviteDialog open={inviteOpen} onOpenChange={setInviteOpen} />
      <Dialog open={confirmUser !== null} onOpenChange={(o) => !o && setConfirmUser(null)}>
        <DialogContent
          title={confirmUser?.active ? 'Deactivate user' : 'Reactivate user'}
          description={
            confirmUser?.active
              ? `${confirmUser.email} will immediately lose access.`
              : `${confirmUser?.email ?? ''} will regain access.`
          }
        >
          <DialogFooter>
            <Button onClick={() => setConfirmUser(null)}>Cancel</Button>
            <Button
              variant={confirmUser?.active ? 'danger' : 'primary'}
              onClick={() => {
                if (confirmUser) {
                  patchUser.mutate({ userId: confirmUser.id, body: { active: !confirmUser.active } });
                }
                setConfirmUser(null);
              }}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
```

Mount in `router.tsx` (inside the `RequireRole role="admin"` branch): `<UsersPage />`.

- [ ] **Step 4: Run tests, gates, manual smoke**

Run: `pnpm test && pnpm lint && pnpm typecheck`
Expected: all PASS.

Manual: invite → copy link → open in a private window → set password → sign in as the new user; back as admin: change their role, deactivate them, verify their next request bounces to login.

- [ ] **Step 5: Commit**

```bash
git add frontend/
git commit -m "feat: admin users page with invite link flow, role change, deactivate"
```

---

### Task 14: Superadmin › Models — registry CRUD with write-only key

**Files:**
- Create: `frontend/src/features/admin/models/queries.ts`, `models/model-form-dialog.tsx`, `models/models-page.tsx`
- Modify: `frontend/src/app/router.tsx` (mount `ModelsPage`)
- Test: `frontend/src/features/admin/models/model-form-dialog.test.tsx`

**Interfaces:**
- `useAdminModels()` — `['admin-models']` → GET `/api/v1/admin/models`; `useCreateModel()` / `usePatchModel()` / `useDeleteModel()` — invalidate `['admin-models']` **and** `['models']` (the chat picker).
- `<ModelFormDialog open onOpenChange>` — phase1 spec §5 fields: display name; provider kind (`openai` / `ollama` / `openai_compatible`); model id (`litellm_model_name`); **base URL field rendered only for `ollama` and `openai_compatible`**; **API key: write-only** password input for `openai`/`openai_compatible` (never pre-filled, never echoed back — after save the table shows only the fingerprint). Secrets UI is implicit here per the plan scope: the key rides the model payload; the backend stores it via the secrets module.
- Models table: display name, provider, model id, base URL, key fingerprint (`text-muted`, mono), enabled toggle (PATCH `{enabled}`), gateway `sync_status` pill (`synced`→success, `pending`→accent, `error`→danger), remove with confirm.

- [ ] **Step 1: Write failing tests**

`frontend/src/features/admin/models/model-form-dialog.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ModelFormDialog } from './model-form-dialog';

function renderDialog(fetchMock = vi.fn()) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelFormDialog open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('base URL appears only for ollama and openai_compatible', async () => {
  const user = userEvent.setup();
  renderDialog();
  expect(screen.queryByLabelText('Base URL')).not.toBeInTheDocument(); // openai default
  await user.selectOptions(screen.getByLabelText('Provider'), 'ollama');
  expect(screen.getByLabelText('Base URL')).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('Provider'), 'openai_compatible');
  expect(screen.getByLabelText('Base URL')).toBeInTheDocument();
});

test('api key is a write-only password field, absent for ollama', async () => {
  const user = userEvent.setup();
  renderDialog();
  const key = screen.getByLabelText('API key');
  expect(key).toHaveAttribute('type', 'password');
  expect(key).toHaveAttribute('autocomplete', 'off');
  await user.selectOptions(screen.getByLabelText('Provider'), 'ollama');
  expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();
});

test('submits the assembled payload', async () => {
  const fetchMock = vi.fn(async () =>
    new Response(JSON.stringify({ id: 'm1', key_fingerprint: 'ab12…ef90' }), {
      status: 201,
      headers: { 'content-type': 'application/json' },
    }),
  );
  const user = userEvent.setup();
  renderDialog(fetchMock);
  await user.type(screen.getByLabelText('Display name'), 'GPT-4o mini');
  await user.type(screen.getByLabelText('Model id'), 'gpt-4o-mini');
  await user.type(screen.getByLabelText('API key'), 'sk-test-123');
  await user.click(screen.getByRole('button', { name: 'Add model' }));
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const req = fetchMock.mock.calls[0]![0] as Request;
  const body = JSON.parse(await req.clone().text()) as Record<string, unknown>;
  expect(body).toMatchObject({
    display_name: 'GPT-4o mini',
    litellm_model_name: 'gpt-4o-mini',
    provider_kind: 'openai',
    api_key: 'sk-test-123',
  });
});
```

Run: `pnpm test src/features/admin/models` — Expected: FAIL.

- [ ] **Step 2: Implement queries**

`frontend/src/features/admin/models/queries.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';

export interface ModelCreate {
  display_name: string;
  litellm_model_name: string;
  provider_kind: 'openai' | 'ollama' | 'openai_compatible';
  base_url?: string;
  api_key?: string; // write-only: sent, never read back
}

function useInvalidateModels() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ['admin-models'] });
    void queryClient.invalidateQueries({ queryKey: ['models'] });
  };
}

export function useAdminModels() {
  return useQuery({
    queryKey: ['admin-models'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/models');
      if (error) throw new Error('failed to load models');
      return data;
    },
  });
}

export function useCreateModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async (body: ModelCreate) => {
      const { data, error } = await api.POST('/api/v1/admin/models', { body });
      if (error) throw new Error('failed to add model');
      return data;
    },
    onSuccess: invalidate,
  });
}

export function usePatchModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async (input: { modelId: string; body: { enabled?: boolean } }) => {
      const { data, error } = await api.PATCH('/api/v1/admin/models/{model_id}', {
        params: { path: { model_id: input.modelId } },
        body: input.body,
      });
      if (error) throw new Error('failed to update model');
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useDeleteModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async (modelId: string) => {
      const { error } = await api.DELETE('/api/v1/admin/models/{model_id}', {
        params: { path: { model_id: modelId } },
      });
      if (error) throw new Error('failed to remove model');
    },
    onSuccess: invalidate,
  });
}
```

- [ ] **Step 3: Implement components**

`frontend/src/features/admin/models/model-form-dialog.tsx`:

```tsx
import { useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';

import { useCreateModel, type ModelCreate } from './queries';

type ProviderKind = ModelCreate['provider_kind'];

const NEEDS_BASE_URL: ProviderKind[] = ['ollama', 'openai_compatible'];
const NEEDS_KEY: ProviderKind[] = ['openai', 'openai_compatible'];

export function ModelFormDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const create = useCreateModel();
  const [displayName, setDisplayName] = useState('');
  const [provider, setProvider] = useState<ProviderKind>('openai');
  const [modelId, setModelId] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');

  const close = (next: boolean): void => {
    if (!next) {
      setDisplayName('');
      setProvider('openai');
      setModelId('');
      setBaseUrl('');
      setApiKey(''); // key never lingers in state after close
      create.reset();
    }
    onOpenChange(next);
  };

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    const body: ModelCreate = {
      display_name: displayName,
      litellm_model_name: modelId,
      provider_kind: provider,
      ...(NEEDS_BASE_URL.includes(provider) && baseUrl ? { base_url: baseUrl } : {}),
      ...(NEEDS_KEY.includes(provider) && apiKey ? { api_key: apiKey } : {}),
    };
    create.mutate(body, {
      onSuccess: () => {
        toast('Model added — key stored, fingerprint shown in the table');
        close(false);
      },
    });
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent title="Add model" description="Synced to the LiteLLM gateway on save.">
        <form onSubmit={onSubmit} className="space-y-3">
          <div>
            <Label htmlFor="model-display">Display name</Label>
            <Input
              id="model-display"
              required
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="model-provider">Provider</Label>
            <NativeSelect
              id="model-provider"
              value={provider}
              onChange={(e) => setProvider(e.target.value as ProviderKind)}
            >
              <option value="openai">OpenAI</option>
              <option value="ollama">Ollama</option>
              <option value="openai_compatible">OpenAI-compatible URL</option>
            </NativeSelect>
          </div>
          <div>
            <Label htmlFor="model-id">Model id</Label>
            <Input
              id="model-id"
              required
              placeholder="e.g. gpt-4o-mini"
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
            />
          </div>
          {NEEDS_BASE_URL.includes(provider) ? (
            <div>
              <Label htmlFor="model-base-url">Base URL</Label>
              <Input
                id="model-base-url"
                required
                type="url"
                placeholder="http://ollama:11434"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
            </div>
          ) : null}
          {NEEDS_KEY.includes(provider) ? (
            <div>
              <Label htmlFor="model-api-key">API key</Label>
              <Input
                id="model-api-key"
                type="password"
                autoComplete="off"
                placeholder="Write-only — a fingerprint is shown after save"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>
          ) : null}
          {create.isError ? (
            <p role="alert" className="text-[12px] text-danger">
              {create.error.message}
            </p>
          ) : null}
          <DialogFooter>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={create.isPending}>
              Add model
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
```

`frontend/src/features/admin/models/models-page.tsx`:

```tsx
import { Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';

import type { ModelOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill, type StatusTone } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';

import { ModelFormDialog } from './model-form-dialog';
import { useAdminModels, useDeleteModel, usePatchModel } from './queries';

function syncTone(status: ModelOut['sync_status']): StatusTone {
  if (status === 'synced') return 'success';
  if (status === 'error') return 'danger';
  return 'accent';
}

export function ModelsPage() {
  const models = useAdminModels();
  const patchModel = usePatchModel();
  const deleteModel = useDeleteModel();
  const [addOpen, setAddOpen] = useState(false);
  const [removing, setRemoving] = useState<ModelOut | null>(null);

  return (
    <>
      <TopBar
        title="Models"
        actions={
          <Button variant="primary" size="sm" onClick={() => setAddOpen(true)}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> Add model
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-4xl">
          {models.isPending ? <Spinner label="Loading models…" /> : null}
          {models.data ? (
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Provider</TH>
                  <TH>Model id</TH>
                  <TH>Key</TH>
                  <TH>Gateway</TH>
                  <TH>Enabled</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {models.data.map((model) => (
                  <TR key={model.id}>
                    <TD className="font-medium">{model.display_name}</TD>
                    <TD className="text-secondary">{model.provider_kind}</TD>
                    <TD className="font-mono text-[12px] text-secondary">
                      {model.litellm_model_name}
                    </TD>
                    <TD className="font-mono text-[12px] text-muted">
                      {model.key_fingerprint ?? '—'}
                    </TD>
                    <TD>
                      <StatusPill tone={syncTone(model.sync_status)}>
                        {model.sync_status}
                      </StatusPill>
                    </TD>
                    <TD>
                      <input
                        type="checkbox"
                        aria-label={`Enable ${model.display_name}`}
                        checked={model.enabled}
                        disabled={patchModel.isPending}
                        onChange={(e) =>
                          patchModel.mutate({
                            modelId: model.id,
                            body: { enabled: e.target.checked },
                          })
                        }
                        className="h-4 w-4 accent-[var(--accent)]"
                      />
                    </TD>
                    <TD className="text-right">
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Remove ${model.display_name}`}
                        onClick={() => setRemoving(model)}
                      >
                        <Trash2 className="h-4 w-4" aria-hidden />
                      </Button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : null}
        </div>
      </div>
      <ModelFormDialog open={addOpen} onOpenChange={setAddOpen} />
      <Dialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <DialogContent
          title="Remove model"
          description={`"${removing?.display_name ?? ''}" will be removed from the gateway and every picker.`}
        >
          <DialogFooter>
            <Button onClick={() => setRemoving(null)}>Cancel</Button>
            <Button
              variant="danger"
              disabled={deleteModel.isPending}
              onClick={() => {
                if (removing) deleteModel.mutate(removing.id);
                setRemoving(null);
              }}
            >
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
```

Mount in `router.tsx` (inside `RequireRole role="superadmin"`): `<ModelsPage />`.

- [ ] **Step 4: Run tests, gates, manual smoke**

Run: `pnpm test && pnpm lint && pnpm typecheck`
Expected: all PASS.

Manual (as superadmin): add an OpenAI model with a key → fingerprint appears, key never re-displayed anywhere (check the network tab: GET responses carry no key material); toggle enabled off → it leaves the chat picker; remove → gone from table and picker.

- [ ] **Step 5: Commit**

```bash
git add frontend/
git commit -m "feat: superadmin models page - registry crud, write-only key, sync status"
```

---

### Task 15: Playwright E2E — the done-criteria smoke

**Files:**
- Create: `frontend/playwright.config.ts`, `frontend/e2e/smoke.spec.ts`, `frontend/e2e/fixtures/sample.pdf` (generated below)

**Interfaces / contract:** This is Phase 1 done-criteria §6 ("login → upload fixture PDF → wait Indexed → ask → streamed answer with ≥1 citation chip resolving to the uploaded doc") plus the message-controls check (edit → sibling nav appears). It runs against the **real compose stack** — no mocked backend.

**Required environment (documented in Task 16's READMEs):**

| Variable | Meaning |
|---|---|
| `E2E=1` | opt-in switch — without it the suite skips (CI-skippable tag) |
| `E2E_BASE_URL` | default `http://localhost:5173` (`pnpm dev` running) |
| `E2E_EMAIL` / `E2E_PASSWORD` | bootstrap superadmin credentials |
| `E2E_OPENAI_API_KEY` | key the test enters in the Models UI if no model exists yet |

Stack prerequisite (repo root): `docker compose -f deploy/compose.yaml up -d`, backend API + worker running, migrations + bootstrap applied.

- [ ] **Step 1: Add Playwright and generate the fixture PDF**

Run: `pnpm add -D @playwright/test@^1.48.0 && pnpm exec playwright install chromium`

Generate a real one-page PDF whose only content is a distinctive fact (uv pulls fpdf2 ephemerally — no new project dependency):

```bash
mkdir -p e2e/fixtures && uv run --with fpdf2 python -c "
from fpdf import FPDF
pdf = FPDF()
pdf.add_page()
pdf.set_font('Helvetica', size=14)
pdf.multi_cell(0, 8, 'OpenRAG E2E fixture document. The internal launch codename for the OpenRAG payroll project is ZEBRA-COMET-7. This fact appears nowhere else.')
pdf.output('e2e/fixtures/sample.pdf')
"
```

Expected: `e2e/fixtures/sample.pdf` exists (~1 KB). Commit it — the test needs a stable fixture.

- [ ] **Step 2: Create `frontend/playwright.config.ts`**

```ts
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 240_000, // ingestion + first model call are slow paths
  retries: 0,
  workers: 1, // serial: steps build on each other against one real stack
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
});
```

- [ ] **Step 3: Create `frontend/e2e/smoke.spec.ts`**

```ts
import path from 'node:path';

import { expect, test, type Page } from '@playwright/test';

// CI-skippable: only runs when explicitly opted in against a live stack.
test.skip(process.env.E2E !== '1', 'set E2E=1 with a running compose stack to run the smoke');

const EMAIL = process.env.E2E_EMAIL ?? 'root@openrag.internal';
const PASSWORD = process.env.E2E_PASSWORD ?? 'changeme123';
const FIXTURE = path.join(__dirname, 'fixtures', 'sample.pdf');
const QUESTION = 'What is the internal launch codename for the OpenRAG payroll project?';

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByLabel('Email').fill(EMAIL);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/chat/);
}

async function ensureModel(page: Page): Promise<void> {
  await page.goto('/admin/models');
  await page.getByRole('table').waitFor({ timeout: 15_000 }); // list loaded (renders even when empty)
  if ((await page.getByRole('row').count()) > 1) return; // header + at least one model
  const key = process.env.E2E_OPENAI_API_KEY;
  test.skip(!key, 'no model configured and E2E_OPENAI_API_KEY not provided');
  await page.getByRole('button', { name: 'Add model' }).click();
  await page.getByLabel('Display name').fill('GPT-4o mini (e2e)');
  await page.getByLabel('Model id').fill('gpt-4o-mini');
  await page.getByLabel('API key').fill(key as string);
  await page.getByRole('button', { name: 'Add model' }).click();
  await expect(page.getByRole('cell', { name: 'GPT-4o mini (e2e)' })).toBeVisible();
}

async function ensureWorkspace(page: Page): Promise<void> {
  await page.goto('/chat');
  await page.getByRole('button', { name: 'Switch workspace' }).click();
  const existing = page.getByRole('menuitem', { name: 'E2E Workspace' });
  if (await existing.isVisible().catch(() => false)) {
    await existing.click();
    return;
  }
  await page.getByRole('menuitem', { name: 'New workspace' }).click();
  await page.getByLabel('Name').fill('E2E Workspace');
  await page.getByRole('button', { name: 'Create' }).click();
}

test('phase 1 smoke: upload → indexed → cited streamed answer → edit sibling nav', async ({
  page,
}) => {
  await login(page);
  await ensureModel(page);
  await ensureWorkspace(page);

  // Upload the fixture and wait for Indexed (live polling, no reload).
  await page.goto('/documents');
  const fileChooserPromise = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Upload documents' }).click();
  await (await fileChooserPromise).setFiles(FIXTURE);
  const row = page.getByRole('row', { name: /sample\.pdf/ });
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(row.getByText('Indexed')).toBeVisible({ timeout: 180_000 });

  // Ask a question only the fixture can answer; expect a streamed, cited answer.
  await page.goto('/chat');
  await page.getByRole('textbox', { name: 'Message' }).fill(QUESTION);
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByText(/ZEBRA-COMET-7/).first()).toBeVisible({ timeout: 120_000 });
  const chip = page.getByRole('button', { name: /^Citation \d+$/ }).first();
  await expect(chip).toBeVisible();
  // The citation resolves to the uploaded document in the source panel.
  await chip.click();
  await expect(
    page.locator('[aria-label="Sources"]').getByText(/sample\.pdf/).first(),
  ).toBeVisible();

  // Edit the user message in place → a sibling version with < n/n > navigation.
  await page.getByRole('button', { name: 'Edit message' }).first().click();
  const editor = page.getByRole('textbox', { name: 'Edit message' });
  await editor.fill(`${QUESTION} Answer in one word.`);
  await page.getByRole('button', { name: 'Send' }).nth(0).click();
  await expect(page.getByText('2/2').first()).toBeVisible({ timeout: 120_000 });
  await page.getByRole('button', { name: 'Previous version' }).first().click();
  await expect(page.getByText('1/2').first()).toBeVisible();
});
```

- [ ] **Step 4: Run the suite both ways**

Run (skip path, CI default): `pnpm e2e`
Expected: `1 skipped`.

Run (live, full stack up + model key):
`E2E=1 E2E_EMAIL=root@openrag.internal E2E_PASSWORD=changeme123 E2E_OPENAI_API_KEY=sk-… pnpm e2e`
Expected: `1 passed` (allow several minutes: ingestion + two model round-trips). If selectors drifted from earlier tasks, fix the component (accessible names are part of their contract), not the test.

- [ ] **Step 5: Commit**

```bash
git add frontend/playwright.config.ts frontend/e2e frontend/package.json frontend/pnpm-lock.yaml
git commit -m "test: playwright smoke - upload to cited streamed answer with edit sibling nav"
```

---

### Task 16: Documentation

**Files:**
- Create: `frontend/README.md`; create or extend repo-root `README.md` with a Dev Setup section

- [ ] **Step 1: Root `README.md` — Dev Setup section** (create the file with this content if it does not exist; append the section otherwise)

````markdown
# OpenRAG

Self-hosted, multi-tenant RAG platform. Specs live in `docs/superpowers/specs/`.

## Dev Setup

Prereqs: Docker, Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20+ + pnpm.

```bash
# 1. Infrastructure
docker compose -f deploy/compose.yaml up -d

# 2. Backend (from backend/)
cd backend && uv sync
uv run alembic upgrade head
OPENRAG_BOOTSTRAP_EMAIL=root@openrag.internal OPENRAG_BOOTSTRAP_PASSWORD=changeme123 \
  uv run python -m openrag.bootstrap
uv run uvicorn --factory openrag.api.app:create_app --port 8000
# worker (second terminal, from backend/): see Plan B section of the worker README

# 3. Frontend (from frontend/)
cd frontend && pnpm install
pnpm generate:api   # regenerates src/api/schema.d.ts from the running backend
pnpm dev            # http://localhost:5173 (proxies /api → :8000)
```

Sign in with the bootstrap superadmin, add a model under Superadmin › Models, create a
workspace, upload documents, chat.
````

(The outer fence here is four backticks because the README itself contains triple-backtick blocks.)

- [ ] **Step 2: Create `frontend/README.md`**

````markdown
# OpenRAG Frontend

React 18 + Vite + TypeScript strict + Tailwind (token-driven) + TanStack Query.

## Commands (run from `frontend/`)

| Command | Purpose |
|---|---|
| `pnpm dev` | dev server on :5173, proxies `/api` → `http://localhost:8000` |
| `pnpm test` / `pnpm test:watch` | vitest unit/component tests |
| `pnpm lint` / `pnpm typecheck` / `pnpm build` | gates — all must be green before commit |
| `pnpm generate:api` | regenerate `src/api/schema.d.ts` from the backend OpenAPI (backend must be running) |
| `pnpm e2e` | Playwright smoke — **skips unless `E2E=1`** |

## Structure

- `src/features/*` — feature folders mirroring backend modules (chat, documents, admin, auth, workspaces, models)
- `src/components/` — shared UI (`ui/` primitives, `layout/`, `markdown/`)
- `src/api/` — generated schema + client (`types.ts` is the only file that touches `components['schemas']`)
- `src/lib/` — auth store, jwt, sse parser, theme, utilities
- `src/styles/tokens.css` — THE design tokens (theme spec). Dark mode = `.dark` on `<html>`.

## Rules that lint will enforce on you

- No raw Tailwind palette classes (`bg-gray-100`, `text-indigo-600`) in `src/features` or
  `src/components` — semantic token classes only (`bg-subtle`, `text-muted`, `text-accent`).
  They also generate no CSS: the palette is fully replaced in `tailwind.config.ts`.
- All server state through TanStack Query; no fetch-in-`useEffect`.
- Model output renders through `<Markdown>` only (sanitized, `skipHtml`); never
  `dangerouslySetInnerHTML`.
- `--text-muted` is for meta text only (WCAG AA).

## E2E smoke

Full compose stack + backend + worker + `pnpm dev` running, then:

```bash
E2E=1 E2E_EMAIL=root@openrag.internal E2E_PASSWORD=changeme123 \
E2E_OPENAI_API_KEY=sk-... pnpm e2e
```

Env vars: `E2E` (opt-in switch), `E2E_BASE_URL` (default `http://localhost:5173`),
`E2E_EMAIL`/`E2E_PASSWORD` (superadmin), `E2E_OPENAI_API_KEY` (used only if no model
is configured yet).
````

- [ ] **Step 3: Verify and commit**

Run: `pnpm lint && pnpm typecheck && pnpm test && pnpm build && pnpm e2e`
Expected: gates green; e2e reports `1 skipped` (no `E2E=1`).

```bash
git add README.md frontend/README.md
git commit -m "docs: dev setup and frontend readme with theme and e2e rules"
```

---

## Plan D Completion Criteria

Phase 1 spec §6 demo script, items 1–4, on a fresh `docker compose up` stack:

1. **Superadmin model management round-trip (UI):** bootstrap superadmin signs in → Superadmin › Models → adds an OpenAI model (key write-only, fingerprint shown after save) and an Ollama model (base-URL field appears conditionally) → both answer a chat round-trip → removing a model takes it out of the chat picker immediately.
2. **Invite flow:** Admin › Users → invite (email + role) → copy the one-time link → new user opens it, sets a ≥12-char password, signs in → uploads a PDF and watches queued → Processing → Indexed live (polling pills, no reload).
3. **Upload → indexed → cited answer:** a question answerable only from the uploaded PDF returns a streamed answer with ≥1 `[n]` citation chip; clicking the chip highlights the matching `filename · p. N` source chip; a keyword query also retrieves correctly (hybrid proof). Edit the question → sibling `2/2` appears with `< n/n >` navigation preserving each version's own answers; regenerate produces an assistant sibling under the same navigation.
4. **Org isolation spot-check:** two browsers/contexts — org A superadmin and an org B user — org B sees none of org A's workspaces, chats, documents anywhere, and retrieval over org B's empty workspace produces the no-answer state, never org A content.

Gates:

- `pnpm lint && pnpm typecheck && pnpm test && pnpm build` — all green from `frontend/`.
- `E2E=1 … pnpm e2e` — Playwright smoke green against the real compose stack (and `pnpm e2e` without `E2E=1` skips cleanly for CI).
- Palette-lint spot check: `grep -rE 'bg-(gray|zinc|slate|indigo)-[0-9]' frontend/src/features frontend/src/components` returns nothing.











