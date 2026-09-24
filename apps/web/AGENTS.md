# WorkPulse Web — local rules

Next.js App Router + TypeScript (strict). The root `/AGENTS.md` rules still apply.
Stack and rationale: ADR 0005 (`.ai/decisions/0005-frontend-stack.md`).

## Stack

Tailwind CSS v4 · shadcn/ui (Radix + lucide-react + sonner) · TanStack Query v5 ·
orval-generated `@workpulse/api-client` · react-hook-form + zod · Zustand ·
dayjs (`utc` + `timezone`) · ESLint + Prettier · Vitest + Testing Library.

## Commands

pnpm workspace member — `pnpm install` runs from the repo root (`make install`).

| Command              | Purpose                                                         |
| -------------------- | --------------------------------------------------------------- |
| `pnpm install`       | install workspace dependencies (from the repo root)             |
| `pnpm run dev`       | dev server on :3000                                             |
| `pnpm run typecheck` | TypeScript gate used by `make check`                            |
| `pnpm run lint`      | ESLint gate used by `make check`                                |
| `pnpm run format`    | Prettier write                                                  |
| `pnpm run test`      | Vitest run (gate) · `pnpm run test:watch` for dev               |
| `pnpm run gen:api`   | regenerate `@workpulse/api-client` (needs `make gen-api` first) |
| `pnpm run build`     | production build                                                |

## Layout

- `app/` — routes only (App Router mirrors the URL structure)
- `app/(auth)/` — login/register route group
- `app/(dashboard)/` — authenticated-area route group
- `app/providers.tsx` — QueryClientProvider + Toaster (client boundary)
- `features/<name>/` — feature logic, mirroring `apps/api/app/domains/<name>/`
- `components/ui/` — shared shadcn primitives (generated, then owned)
- `lib/` — app utilities (`lib/datetime.ts` = dayjs)
- `hooks/` — cross-feature hooks
- `../../packages/api-client/src/` — generated typed client + Query hooks (do not hand-edit)

## Rules

- No business logic in `app/` — pages compose features.
- One feature = one folder under `features/`; new feature = spec in
  `.ai/features/<name>.md` first.
- All HTTP goes through `@workpulse/api-client` (alias import) — never ad-hoc
  `fetch`/axios in components. Regenerate, don't hand-edit, generated code.
- Server data lives only in the Query cache; Zustand is UI-only state.
- Forms: react-hook-form + zod, schema colocated in the feature.
- Times: UTC ISO 8601 from the API; render through `lib/datetime.ts`.
- `"use client"` only on interactive leaves; Server Components by default.
- Strict TypeScript; no new dependencies without a spec.
- pnpm's strict `node_modules`: declare every package you import in
  `apps/web/package.json` — no relying on hoisted transitive deps.
