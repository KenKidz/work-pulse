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

  | File | Meaning |
  | --- | --- |
  | `models.py` | persistence/domain model |
  | `schemas.py` | input/output contracts |
  | `service.py` | use cases (the only place business logic lives) |
  | `repository.py` | data-access abstraction |
  | `events.py` | domain events emitted by this domain |
  | `policies.py` | business rules / authorization |
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
- Gates in `make check-web`: `tsc --noEmit` + ESLint + Vitest.

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