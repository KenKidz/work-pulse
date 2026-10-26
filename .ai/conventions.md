# WorkPulse — Conventions

> Code-level conventions. Authoritative siblings: `.ai/security.md` (roles,
> tenancy, PII, audit) and `.ai/testing.md` (gates, levels, mandatory rules).
> Root rules stay short in `AGENTS.md`; app-specific rules live in each app's
> `AGENTS.md`.

## Python — `apps/api`

- Environment and dependencies: **uv** (`uv sync`, `uv add`, `uv run`).
- Python 3.12+ (pinned in `.python-version`).
- Stack (ADR 0006): SQLAlchemy 2.0 async + `asyncpg` · Alembic · Pydantic v2 ·
  structlog · `uuid-utils` (UUIDv7 ids) · Ruff · Pyright · pytest +
  pytest-asyncio + httpx (`ASGITransport`) · plain `Depends` DI · stdlib
  `datetime`/`zoneinfo` (UTC, `timestamptz`).
- Commands: `uv run uvicorn app.main:app --reload` · `uv run pytest`.
- Domain module pattern — every domain package contains:

  | File            | Meaning                                              |
  | --------------- | ---------------------------------------------------- |
  | `models.py`     | persistence/domain model                             |
  | `schemas.py`    | input/output contracts                               |
  | `service.py`    | use cases (the only place business logic lives)      |
  | `repository.py` | data-access abstraction                              |
  | `events.py`     | domain events emitted by this domain                 |
  | `policies.py`   | business rules / authorization                       |
  | `exceptions.py` | domain-specific errors (`WorkPulseError` subclasses) |

- Routers translate HTTP → service calls. No business logic, no SQL, no Kafka in
  a router. Routers accept/return Pydantic schemas only — ORM models never cross
  the router boundary; repositories are the only code touching SQLAlchemy.
- Full async: `async def` from router → service → repository; sync-only
  libraries go through `anyio.to_thread` with a justification.
- Ids are UUIDv7 (`uuid-utils`) in PostgreSQL `uuid` columns; timestamps are
  timezone-aware UTC (`timestamptz`).
- Migrations (Alembic) are append-only: never edit a committed revision;
  autogenerate → review → meaningful revision message.
- Logging is structlog with `request_id` and `tenant_id` bound; never PII.
- A domain never imports another domain's internals directly; go through its
  service or a shared interface.
- **Time is a parameter, not a global.** Services that judge lateness, workdays
  or payroll periods take a clock/`now` argument — never call
  `datetime.now()` inside business logic.
- Errors: subclass `app.shared.errors.WorkPulseError` with a stable `code`
  (`snake_case`, domain-prefixed: `employee_email_taken`).
- Shared plumbing in `app/shared/`: errors, logging, pagination, audit, identity.

## TypeScript — `apps/web`

- Next.js App Router, TypeScript strict (`+ noUncheckedIndexedAccess`).
- Stack (ADR 0005): Tailwind CSS v4 · shadcn/ui (Radix + `lucide-react` +
  `sonner`) · TanStack Query v5 · orval-generated `@workpulse/api-client` ·
  react-hook-form + zod · Zustand · dayjs (`utc` + `timezone`) · ESLint +
  Prettier · Vitest + Testing Library.
- `app/` contains **routes only** — pages compose features, no business logic.
- RSC-first: components are Server Components by default; `"use client"` only on
  interactive leaves, never at page/route level.
- One folder per feature under `features/<name>/`, mirroring
  `apps/api/app/domains/<name>/`:
  `components/`, `hooks/`, `api.ts`, `types.ts`, `utils.ts` (+ `schema.ts` for
  zod form schemas).
- `components/ui/` = shared shadcn primitives (generated into the repo, then
  owned); `hooks/` = cross-feature hooks; `lib/` = app utilities
  (`lib/datetime.ts` for dayjs).
- All HTTP goes through the generated `@workpulse/api-client` — its mutator
  owns the base URL and `ApiError`; never ad-hoc `fetch`/axios in components.
- Server data lives only in the TanStack Query cache, keyed
  `[feature, ...scope, params]`; mutations invalidate by key. No shadow copies
  in component state or stores.
- Zustand stores are UI-only (filters, modals, selection), one per feature.
- **Debounce filter, search and sort inputs:** any control that refetches a list
  (search `q`, filter fields, sort order) is debounced (~300ms) before the query
  key changes — never one API call per keystroke. Discrete one-shot controls
  (select, toggle, date picker) apply immediately; a single change cannot storm
  requests. Shared hook: `hooks/use-debounced-value.ts`.
- Forms: react-hook-form + zod, schema colocated in the feature; the server
  stays the authority — map `ApiError.code` back to field errors.
- Time: the API contract is UTC ISO 8601 strings; convert only at render via
  dayjs (`lib/datetime.ts`); never raw `new Date(string)` arithmetic.
- Every data-fetching surface has loading, empty and error states; an
  `ApiError.code` maps to a user-visible message; mutation failures surface via
  toast (sonner).
- Route groups: `app/(dashboard)/` for the authenticated area, `app/(auth)/` for
  login/register.
- Naming: files `kebab-case.ts(x)` for non-component modules, `PascalCase.tsx`
  for components.
- UI rulebook: for anything visual in `apps/web/`, load the project skill
  `workpulse-ui` (`.kilo/skills/workpulse-ui/SKILL.md`) and follow it — design
  identity (tokens, type, radius), presentation rules, realtime/live surfaces,
  states, motion, copy, accessibility floor and pre-flight. It is the only UI
  skill needed for product work: it distills `ui-ux-pro-max`, `frontend-design`,
  `design-taste-frontend-v1` and `web-design-guidelines`, so those are not
  loaded for routine UI.
- Upstream escalation, only where `workpulse-ui` points there: one targeted
  `ui-ux-pro-max` search for a concern it does not cover · `frontend-design` for
  genuinely new art direction · `web-design-guidelines` + `ui-ux-pro-max
  --domain ux` for a formal UI review/audit · `design-taste-frontend` (v2)
  instead of it for marketing/landing surfaces.

### Presentation rules

- **Card chrome by role:** data panels (a table, count board, activity feed,
  check-in panel or shifts panel inside a bordered container) use
  `rounded-md border-border shadow-none`; elevated cards (destination/link
  cards, landing cards and reading cards) keep the `card.tsx` default
  `rounded-xl shadow-sm`.
- **List tables:** always pass `containerLabel` to `components/ui/table.tsx` and
  render the `sm:hidden` “Swipe sideways to see every … field.” hint above it
  (exemplar: `AttendanceRecordsTable.tsx:162-167`). Surfaces with a stacked
  mobile row rendering instead of an overflow table drop the hint
  (exemplar: `realtime/components/PresenceTable.tsx`).
- **Bounded live lists:** an uncapped live board (command-center presence rows,
  activity rail) bounds its scroll area — `max-h-[min(60dvh,36rem)]
  overflow-y-auto` — instead of growing the page one row per employee. The
  scroll region stays keyboard-reachable (`role="region"` + `aria-label` +
  `tabIndex={0}` + focus outline; built into `table.tsx` through
  `containerLabel`, replicated on custom rails). A bounded table passes
  `containerClassName="max-h-… overflow-y-auto"` and `sticky` on `TableHeader`
  (exemplar: `realtime/components/PresenceTable.tsx`).
- **Row actions:** table actions use `className="min-h-11 sm:min-h-7"` on
  `size="xs"` buttons (exemplar: `ShiftsPanel.tsx:157-158`).
- **Filter controls:** use `w-full sm:w-…` so controls stack below `sm`
  (exemplar: `AttendanceRecordsTable.tsx:63,71`).
- **Dialog field pairs:** use `grid-cols-1 items-start gap-… sm:grid-cols-2`.
  `items-start` is mandatory: a description or error message on only one field
  makes that `FormItem` taller, and without it the sibling item stretches and
  its label/control stack shifts down out of line.
- **Field hints are tooltips:** explanatory copy about one field is passed to
  `FormLabel` as `hint` and opens in a tooltip from a 24×24 info trigger beside
  the label; never render it as an always-visible `FormDescription` line.
  `FormMessage` errors stay inline below the control. Keep the hint text in the
  DOM as the control's `aria-describedby` description, keep the trigger a real
  button with an accessible name and no submit behaviour, and keep the label
  row height unchanged (the trigger uses negative vertical margins). Dialog
  intros stay in `DialogDescription`; guidance covering several fields stays
  visible at group level. Exemplar: `JobFormDialog.tsx` (`Openings`, `Description`).
- **Mono data labels:** employee codes and timezones use `font-mono`, which
  resolves to Fira Code.
- **One control language (Button / Input / Select / Textarea):** every
  interactive control comes from `components/ui/` and keeps the shared spec —
  `h-9` height, `rounded-md`, `text-sm`, `border-input shadow-xs` fields, one
  focus ring (`ring-[3px] ring-ring/50`), `aria-invalid:border-destructive`,
  `disabled:opacity-50`. Never raw `<button>/<input>/<select>/<textarea>`, and
  never a feature-local copy of these metrics.
- **Call-site `className` is layout-only:** choose `variant` + `size`;
  `className` may add width, spacing or alignment (`w-full sm:w-48`, `ml-auto`,
  `tabular-nums`) — never control metrics: no `h-*`, `size-*`, `rounded-*`,
  `text-*`, `border-*`, shadow or ring classes (exemplar:
  `DepartmentFormDialog.tsx:133,151`).
- **Touch targets use the `11` unit (44px) — the one allowed metric
  override:** `min-h-11` on wide controls (row actions `min-h-11 sm:min-h-7`,
  see Row actions), `size="icon"` + `size-11` on icon buttons (exemplar:
  `mobile-nav.tsx:38,52`). Never a fixed `h-…`/`size-…` fighting the `size`
  prop, and never a `rounded-*` override.
- **Scrollbars:** the global thin treatment in `apps/web/app/globals.css`
  (8px band, 4px `--input` pill thumb, transparent track) is the only one —
  never per-surface `::-webkit-scrollbar` or `scrollbar-*` overrides.

- shadcn/ui: load the `shadcn` skill when adding, updating or debugging
  components in `components/ui/` or composing shadcn primitives.
- Gates in `make check-web`: `tsc --noEmit` + ESLint + Vitest.

## Internationalization (i18n)

- `apps/web` is bilingual (English default, Vietnamese) through `next-intl`.
  The locale is the `wp_locale` cookie; URLs stay locale-agnostic.
- All user-facing copy — JSX text, `aria-label`, placeholder, `title`,
  validation message, toast, empty/error state, metadata — comes from
  `apps/web/messages/<locale>/<surface>.json`. No hardcoded English in
  components, schemas, label maps or error helpers.
- Client Components use `useTranslations` / `useLocale`; Server Components use
  `getTranslations` / `getLocale` / `getFormatter` from `next-intl/server`.
- Feature schemas are factories taking the translated validation copy:
  `departmentFormSchema((key) => t("...validation." + key))`. Error helpers
  take a translator and map stable API codes, never raw server text.
- Every catalog key exists in **both** locales; `tests/i18n/catalog.test.ts`
  enforces parity and non-empty values.
- Dates, numbers, percentages and money use `Intl` with the active locale.
  Pure helpers accept an explicit `locale` (e.g. `formatPayrollDate(value,
  locale)`); do not use a module-level locale.
- When adding a surface, add its JSON file to both `messages/<locale>/index.ts`
  files and to the feature spec.
- Tests render through `tests/mocks/next-intl.tsx`, which resolves the real
  English catalog; keep English assertions aligned with the catalog.

## API

- Prefix: `/api/v1`. One router module per domain:
  `apps/api/app/api/v1/<domain>.py`.
- Error shape (stable from day one):

  ```json
  { "error": { "code": "employee_email_taken", "message": "..." } }
  ```

- Pagination is uniform: `?page=&page_size=` in, `{"items": [...], "page": {...}}`
  out. `page_size` is clamped, never an error.
- Health: `GET /healthz` (liveness, no external dependencies). Readiness checks
  that depend on infrastructure get their own route when they exist.
- Identity in Phase 1 is the **dev identity**: `X-Tenant-Id`, `X-Actor-Id`,
  `X-Role` validated by a dependency in `app/dependencies.py`. Policies are
  written against it exactly as they will be against a real session (Phase 2).
- Full-stack changes pin the contract in the feature spec **before** code.

## Events — `contracts/events/`

- Every event uses the envelope: `contracts/events/common/event-envelope.v1.json`.
- Naming: type `attendance.checked_in`, file
  `contracts/events/attendance/attendance-checked-in.v1.json`.
- Changes are additive within a version. A breaking change gets a new version —
  `v1` and `v2` coexist while consumers migrate.
- Consumers must ignore unknown fields (forward compatibility) and deduplicate
  on `event_id` (idempotency).
- Payloads carry **identifiers and state**, never PII values.
- Contracts are written before producers/consumers, even if the bus arrives in a
  later phase (catalog: `.ai/domain.md`).

## Testing

Authoritative rules: `.ai/testing.md`. The conventions that affect file layout:

- `apps/api/tests/unit` — no I/O, no DB, no network.
- `apps/api/tests/integration` — real PostgreSQL, repositories + routers.
- `apps/api/tests/contract` — API shape and event payloads vs `contracts/`.
- `apps/web/tests` — feature tests (Vitest + Testing Library).
- `tests/e2e/` — cross-surface flows.
- Fixtures and seeds are deterministic and synthetic; never real people.

## Security

Authoritative rules: `.ai/security.md`. The conventions that affect code:

- Tenant scoping lives in **repositories** (`WHERE tenant_id = :tenant`), never
  in routers or services.
- Authorization lives in `domains/<name>/policies.py`; routers call it, the web
  app only hides UI.
- Cross-tenant reads return **404**, not 403.
- No PII in logs, error messages, event payloads, fixtures or analytics rows.
- Sensitive mutations write an audit entry via `app/shared/audit`.
- Secrets only via environment variables — `.env` is gitignored, keep
  `.env.example` current.

## Change hygiene

- One concern per change; the smallest diff that satisfies the plan.
- Docs move with code: spec status, `tasks/TODO.md`, and `.ai/` in the same change.
- Expensive-to-reverse decisions get an ADR in `.ai/decisions/`.
