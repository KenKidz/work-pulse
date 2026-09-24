# WorkPulse — Conventions

> Code-level conventions. Authoritative siblings: `.ai/security.md` (roles,
> tenancy, PII, audit) and `.ai/testing.md` (gates, levels, mandatory rules).
> Root rules stay short in `AGENTS.md`; app-specific rules live in each app's
> `AGENTS.md`.

## Python — `apps/api`

- Environment and dependencies: **uv** (`uv sync`, `uv add`, `uv run`).
- Python 3.12+ (pinned in `.python-version`).
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
  a router.
- A domain never imports another domain's internals directly; go through its
  service or a shared interface.
- **Time is a parameter, not a global.** Services that judge lateness, workdays
  or payroll periods take a clock/`now` argument — never call
  `datetime.now()` inside business logic.
- Errors: subclass `app.shared.errors.WorkPulseError` with a stable `code`
  (`snake_case`, domain-prefixed: `employee_email_taken`).
- Shared plumbing in `app/shared/`: errors, logging, pagination, audit, identity.

## TypeScript — `apps/web`

- Next.js App Router, TypeScript strict.
- `app/` contains **routes only** — pages compose features, no business logic.
- One folder per feature under `features/<name>/`, mirroring
  `apps/api/app/domains/<name>/`:
  `components/`, `hooks/`, `api.ts`, `types.ts`, `utils.ts`.
- `components/ui/` = shared primitives; `lib/api/` = API glue; `hooks/` =
  cross-feature hooks.
- All HTTP calls go through `lib/api` — never `fetch` ad hoc in components.
- Every data-fetching surface has loading, empty and error states; an
  `ApiError.code` maps to a user-visible message.
- Route groups: `app/(dashboard)/` for the authenticated area, `app/(auth)/` for
  login/register.
- Naming: files `kebab-case.ts(x)` for non-component modules, `PascalCase.tsx`
  for components.

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
- `apps/web/tests` — feature tests (runner TBD, see `.ai/testing.md`).
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