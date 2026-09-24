# WorkPulse Web — local rules

Next.js App Router + TypeScript (strict). The root `/AGENTS.md` rules still apply.

## Commands

| Command | Purpose |
| --- | --- |
| `npm install` | install dependencies |
| `npm run dev` | dev server on :3000 |
| `npm run typecheck` | TypeScript gate used by `make check` |
| `npm run build` | production build |

## Layout

- `app/` — routes only (App Router mirrors the URL structure)
- `app/(auth)/` — login/register route group
- `app/(dashboard)/` — authenticated-area route group
- `features/<name>/` — feature logic, mirroring `apps/api/app/domains/<name>/`
- `components/ui/` — shared primitives
- `lib/api/` — API client glue (talks to `apps/api`)
- `hooks/` — cross-feature hooks

## Rules

- No business logic in `app/` — pages compose features.
- One feature = one folder under `features/`; new feature = spec in
  `.ai/features/<name>.md` first.
- All HTTP calls go through `lib/api` — never `fetch` ad hoc in components.
- Strict TypeScript; no new dependencies without a spec.