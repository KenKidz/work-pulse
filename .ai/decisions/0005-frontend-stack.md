# 5. Frontend stack for `apps/web`

- **Status:** Accepted
- **Date:** 2026-09-24

## Context

`apps/web` runs Next.js (App Router) + TypeScript strict, but nothing else is pinned:
no styling system, no component kit, no data layer, no form standard, and the API
types would have to be mirrored by hand. The app is a workforce operations
dashboard — dense tables, forms with validation, per-tenant data — and Phase 2
adds WebSocket live updates. The API is FastAPI and publishes its contract as
OpenAPI. Repo rules: contracts before consumers, no dependency without a job,
`make check` is the gate.

## Decision

| Concern | Choice |
| --- | --- |
| Styling | **Tailwind CSS v4** (CSS-first, PostCSS plugin) |
| Components | **shadcn/ui** (Radix primitives + CVA), `lucide-react` icons, `sonner` toasts |
| Client data | **TanStack Query v5** — the only client-side server-data cache |
| API typing | **orval** codegen from `contracts/openapi/openapi.json` → `packages/api-client` (typed functions + Query hooks over a custom fetch mutator) |
| Forms | **react-hook-form + zod** (`@hookform/resolvers`) |
| UI state | **Zustand** — UI-only state (filters, modals); never server data |
| Tables | **TanStack Table v8** (lands with the first data-grid feature) |
| Dates | **dayjs** with `utc` + `timezone` plugins |
| Lint/format | **ESLint** (flat config, typescript-eslint) + **Prettier** (`prettier-plugin-tailwindcss`) |
| Tests | Vitest + Testing Library (already decided in `.ai/testing.md`) |

Deferred: charts (candidate: Recharts) until a feature spec needs them; any state
library beyond Zustand; axios (the mutator keeps native `fetch`).

## Consequences

- The HTTP transport (base URL, `ApiError` mapping) moves into
  `packages/api-client`'s mutator; `apps/web/lib/api/client.ts` is retired so
  there is exactly one client.
- Generated code under `packages/api-client/src` is owned by the generator:
  never hand-edited, excluded from lint/format, regenerated via `make gen-api`
  after every contract change (dump OpenAPI → orval).
- `make check-web` grows from `tsc --noEmit` to typecheck + ESLint + Vitest.
- Phase 2 WebSocket updates plug into the Query cache
  (`queryClient.setQueryData`), so no second caching layer appears later.
- The monorepo is a **pnpm workspace** (`pnpm-workspace.yaml`: `apps/web`,
  `packages/*`). pnpm's isolated `node_modules` is why `packages/api-client`
  declares `@tanstack/react-query` itself — generated code resolves its imports
  from its own package, never from hoisting. Every imported package must be a
  declared dependency (no phantom deps).
- Zustand and dayjs are installed now; TanStack Table lands with the first
  data-grid feature. Each dependency is only *used* when a feature spec calls
  for it.
- Reversal cost is low-to-medium: shadcn components are copied in and owned, so
  swapping or restyling them later does not fork a framework.
