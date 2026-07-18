# OpenRAG Frontend Theme

**Date:** 2026-07-18
**Status:** Approved (validated visually via mockups; see `.superpowers/brainstorm/` session)
**Inherits:** `2026-07-18-openrag-engineering-foundation-design.md` (stack: React + Vite + TS strict + Tailwind + shadcn/ui). This spec defines the design language those tools implement.

## 1. Direction

Minimalistic, OpenWebUI-style: white surfaces, hairline borders, neutral grays, generous whitespace, one quiet accent color. The product should read as a calm professional tool, not a chatbot demo. Decisions below were each approved against rendered mockups (chat page, document library, dark mode, typography, density).

| Decision | Choice |
|---|---|
| Accent treatment | Grayscale chrome + **one quiet indigo accent**, reserved for citations, links, active states, and the logo mark. Rebrandable per org (PRD REND-5) |
| Dark mode | **Soft charcoal**: elevated gray surfaces (`#181818` base, lighter panels), not flat near-black |
| Typography | **Inter everywhere**, self-hosted (air-gapped installs must not fetch fonts) |
| Density | **Comfortable**: 8–16px radii, generous padding, pill-shaped chips; one spacing scale for chat and admin alike |

## 2. Design Tokens

Tokens live in `frontend/src/styles/tokens.css` as CSS custom properties, consumed by Tailwind (shadcn convention). Dark mode via a `.dark` class on `<html>`; both themes ship at v1.

### 2.1 Color — light

| Token | Value | Use |
|---|---|---|
| `--bg` | `#ffffff` | main surface |
| `--bg-sidebar` | `#f9f9f9` | sidebar, muted panels |
| `--bg-subtle` | `#f4f4f5` | user bubbles, code inline, hover fills |
| `--bg-raised` | `#fafafa` | table headers, secondary bars |
| `--border` | `#ececec` | standard hairline |
| `--border-faint` | `#f1f1f1` | intra-component separators |
| `--text` | `#171717` | primary text |
| `--text-secondary` | `#555555` | supporting text |
| `--text-muted` | `#8a8a8a` | meta/decorative only — below AA for body copy, never for essential reading text |
| `--accent` | `#4f46e5` (indigo-600) | links, citations, active nav, focus rings |
| `--accent-soft` | `#eef2ff` | citation chip / active backgrounds |
| `--success` / `--success-soft` | `#059669` / `#ecfdf5` | indexed, healthy |
| `--danger` / `--danger-soft` | `#dc2626` / `#fef2f2` | failed, destructive |
| `--warning` / `--warning-soft` | `#b45309` / `#fffbeb` | thresholds, soft limits |

### 2.2 Color — dark (soft charcoal)

| Token | Value |
|---|---|
| `--bg` | `#181818` |
| `--bg-sidebar` | `#111113` |
| `--bg-subtle` | `#26262a` |
| `--bg-raised` | `#1d1d20` |
| `--border` | `#26262a` (strong: `#313136`) |
| `--text` | `#ececec` |
| `--text-secondary` | `#a7a7ad` |
| `--text-muted` | `#7a7a80` (meta only) |
| `--accent` | `#818cf8` (text on accent-soft: `#a5b4fc`) |
| `--accent-soft` | `rgba(129,140,248,.18)` |
| status colors | same hues, lightened one step; soft backgrounds as ~15% alpha overlays |

Primary buttons invert: near-black fill / white text in light mode, light fill / dark text in dark mode. The accent is **not** used for primary buttons — it stays scarce.

### 2.3 Typography

- **Family:** Inter (self-hosted via `@fontsource/inter`, packaged in the build — no external font CDN, per air-gapped requirement). Monospace: `ui-monospace, 'JetBrains Mono', monospace` for code.
- **Scale:** UI base 14px; chat prose 15px / 1.6; small/meta 12px; section titles 16px/650; page titles 18–20px/650 with `-0.01em` tracking. `font-variant-numeric: tabular-nums` on all tables and usage meters.

### 2.4 Shape & spacing (comfortable)

- Radii: `--r-sm: 6px` (inline chips), `--r-md: 8px` (buttons, inputs, nav items), `--r-lg: 10px` (cards, tables), `--r-xl: 16px` (chat input pill), `9999px` (status pills, avatars).
- Spacing: 4px grid; table rows ≥ 10px vertical padding; chat thread max-width 720px, centered.
- Elevation: borders do the separation; shadows minimal (`0 1px 2px rgba(0,0,0,.03)` on the input pill and popovers only).

## 3. Signature Components

- **Citation chip:** inline `[n]` as a 15px rounded chip, `--accent-soft` bg / `--accent` text; below the answer, source chips: `[n] filename · p. N` on `--bg-raised` with hairline border. Attachment-origin citations get a distinct icon (PRD CHAT-4). Clicking scrolls/opens the source panel.
- **Status pills:** pill radius, soft bg + strong text (`Indexed` green, `Processing` accent, `Failed · reason` red).
- **Chat input:** full-width pill (`--r-xl`), attach button left, send as a 26px circle in `--text` color, model selector and usage meter in the thin top bar — never inside the input.
- **Sidebar:** `--bg-sidebar`, 215–260px, chat list with ellipsis truncation, active item on `--bg-subtle`; user card + usage summary pinned to the footer.

## 4. White-labeling (PRD REND-5)

Org branding = logo, product name, and accent override. The accent override rewrites `--accent`/`--accent-soft` (and dark variants) from one org-level setting; everything brandable is therefore a token, and no component references raw accent hexes. Neutral chrome is not brandable — that keeps every deployment recognizably calm.

## 5. Accessibility

- WCAG 2.1 AA contrast for all text; `--text-muted` is restricted to non-essential meta (enforced in review).
- Visible focus rings (`--accent`, 2px offset) on all interactive elements; full keyboard reachability.
- Dark/light respects `prefers-color-scheme` by default; manual toggle persists per user.
- Status never communicated by color alone — pills always carry text.

## 6. Implementation Notes

- shadcn/ui components are themed exclusively through the token variables; no per-component color overrides.
- Tailwind config maps semantic names (`bg-subtle`, `text-muted`, `accent`) to the CSS variables; raw palette classes (`bg-gray-100`, `text-indigo-600`) are lint-blocked in app code.
- Streaming markdown renderer (PRD REND-1) inherits prose tokens; tables get sticky headers, zebra on `--bg-raised`, numeric right-align with tabular-nums.
- Reference mockups for all four decisions persist in `.superpowers/brainstorm/` (gitignored) — regenerate from this spec if lost.
