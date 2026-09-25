# Feature: Organizations

- **Status:** done
- **Phase:** 1
- **Size:** S
- **Surfaces:** api+web
- **Links:** `.ai/domain.md` (Organization context) · `.ai/product.md` (MVP scope) · brief `tasks/doc.md` §1

## Problem

Employees, attendance and every future report hang off organizational structure:
which department, which position, which location (and its timezone). Without
that reference data there is nowhere to attach an employee, no timezone to judge
lateness in, and no way to group analytics later. This is the smallest slice
that unblocks Employees.

## Business rules

- A tenant owns its departments, positions and locations; nothing is shared
  across tenants (`.ai/security.md`).
- Departments may be nested (a department can have a parent). Cycles are
  rejected.
- All three collections are **soft-deleted** via `archived_at`: employees and
  attendance records keep referencing them, so history must survive deletion.
- A location has an IANA `timezone` — attendance lateness is evaluated in it.
- Names are unique per tenant within each collection.
- Archived items are hidden from create/update pickers but remain readable and
  appear in `include_archived=true` listings.
- Every create/update/archive writes an audit entry.

## Scope

- CRUD + paginated, filterable list for **departments**, **positions**,
  **locations**.
- Archive (soft delete) + restore.
- Web: one settings-style page per collection (table + create/edit form).
- Dev-identity headers enforced by policies (`.ai/security.md`).

## Out of scope

- **Teams** (brief §1 lists them; deferred until a feature needs them).
- **Shifts** — arrive with Attendance lateness (see `attendance.md`).
- Org charts, drag-and-drop hierarchy editing.
- CSV/bulk import (see `## Decisions`).
- Cost centers, legal entities, multi-company structures.

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/organizations/` |
| API router | `apps/api/app/api/v1/organizations.py` |
| Persistence | `apps/api/app/infrastructure/postgres/` (first migration) |
| Web feature | `apps/web/features/organizations/` |
| Web routes | `apps/web/app/(dashboard)/settings/organization/` |
| API tests | `apps/api/tests/unit` · `tests/integration` |
| E2E | `tests/e2e/` — **deferred to Employees** (Playwright lands with the first feature needing cross-surface automation, per `tests/e2e/README.md`) |

## Roles & access

| Capability | Employee | Manager | HR | Admin |
| --- | --- | --- | --- | --- |
| Read departments/positions/locations | Y | Y | Y | Y |
| Create / update / archive | — | — | Y | Y |

## API contract (final — pinned by T1, 2026-09-24)

Routes (mirrored for `positions` and `locations`):

| Method & path | Input | Response |
| --- | --- | --- |
| `GET /api/v1/departments` | `?q=&parent_id=&include_archived=&page=&page_size=` | `200 {"items": Department[], "page": Page}` |
| `POST /api/v1/departments` | `{"name", "parent_department_id"?}` | `201 Department` |
| `GET /api/v1/departments/{id}` | — | `200 Department` · `404` |
| `PATCH /api/v1/departments/{id}` | `{"name"?, "parent_department_id"?}` | `200 Department` |
| `POST /api/v1/departments/{id}/archive` | `{"reason"?}` | `200 Department` |
| `POST /api/v1/departments/{id}/restore` | — | `200 Department` |

Shapes:

```json
Department { "id", "tenant_id", "name", "parent_department_id": null,
             "archived_at": null, "created_at", "updated_at" }
Position   { "id", "tenant_id", "title", "description": null,
             "archived_at": null, "created_at", "updated_at" }
Location   { "id", "tenant_id", "name", "address": null, "timezone": "Asia/Ho_Chi_Minh",
             "archived_at": null, "created_at", "updated_at" }
```

Error codes: `department_not_found` · `department_name_taken` ·
`department_cycle` · `department_archived_in_use` · `position_not_found` ·
`position_title_taken` · `location_not_found` · `location_name_taken` ·
`invalid_timezone`.

### Pinned details

- **Pagination**: `?page=` default 1 (clamp ≥ 1); `?page_size=` default 20,
  max 100 (clamped, never an error). Response `page` object:
  `{"page", "page_size", "total_items", "total_pages"}`. Default order:
  `name`/`title` ascending.
- **Filters**: `q` = case-insensitive substring of `name`/`title`; `parent_id`
  (departments only, exact UUID); `include_archived` (bool, default `false`).
  `GET /{id}` returns archived resources (they stay readable); `PATCH /{id}`
  on an archived resource is allowed.
- **PATCH semantics**: absent field = unchanged; explicit `null` for
  `parent_department_id` moves the department to the root.
- **Archive/restore**: archive body `{"reason"?}` — the reason is stored on the
  audit entry only, not the resource. Archive-of-archived and
  restore-of-active are `200` no-ops (no audit entry); real transitions audit.
  No DELETE routes exist.
- **Uniqueness**: DB unique constraints `(tenant_id, name)` / `(tenant_id, title)`
  across **all** rows, archived included — an archived row keeps its name and
  restore can never conflict (Risk 2, ADR candidate).
- **Parent rules**: parent must exist in the tenant (else `404
  department_not_found`); self or descendant → `400 department_cycle`; the
  archived state of a parent is **not** checked (Risk 3).
- **Timezone**: validated in the service via `zoneinfo.ZoneInfo` →
  `422 invalid_timezone` in the uniform error shape (never FastAPI's `detail`
  shape). Required on location create, optional on patch.
- **Identity** (ADR 0004): `X-Tenant-Id`, `X-Actor-Id` (UUIDs), `X-Role`
  (case-insensitive, one of `employee|manager|hr|finance|admin|super_admin`).
  Missing/malformed → `400 invalid_identity`; unknown role → `403 forbidden`
  (fail closed); policy denial → `403 forbidden`.
- **Roles**: read = any valid role; manage (create/update/archive/restore) =
  `hr`, `admin`, `super_admin` — the `.ai/security.md` matrix governs where it
  is wider than this spec's 4-column table.
- **Error code → status**: `department_not_found` / `position_not_found` /
  `location_not_found` 404 · `department_name_taken` / `position_title_taken` /
  `location_name_taken` / `department_archived_in_use` 409 · `department_cycle`
  400 · `invalid_timezone` 422 · `forbidden` 403 · `invalid_identity` 400 ·
  `validation_error` 422 (uniform handler for FastAPI RequestValidationError).
  Body always `{"error": {"code", "message"}}`; messages never echo field values.
- **operationIds** (drive orval hook names): `listDepartments`,
  `createDepartment`, `getDepartment`, `updateDepartment`, `archiveDepartment`,
  `restoreDepartment` — same six for `…Positions` and `…Locations` (18 routes).
- **Ids/time**: UUIDv7 ids generated in the service (`uuid-utils`);
  `created_at`/`updated_at` are DB defaults; `archived_at` and audit
  `occurred_at` come from a `now` parameter injected at the router edge (time
  is a parameter, never `datetime.now()` inside business logic).
- Read schemas are titled exactly `Department`, `Position`, `Location`;
  positions/locations mirror the departments route table at `/api/v1/positions`
  and `/api/v1/locations`.

## Events

None in Phase 1. Organization reference data changes are **audited**, not
broadcast. Revisit if a downstream consumer (analytics dimension tables) needs
them in Phase 4.

## Edge cases

- Create a department whose parent is itself or a descendant → `400 department_cycle`.
- Duplicate name in the same tenant → `409 department_name_taken`.
- Archive a department that still has active employees → `409 department_archived_in_use` (PO Decision #1: block).
- Tenant A requests tenant B's id → `404` (never `403` — no existence leak).
- Invalid/unknown IANA timezone → `422 invalid_timezone`.
- `page_size` above the maximum → clamped, not an error.

## Acceptance criteria

- [x] API: `POST /departments` with a valid payload returns `201` and the created resource.
- [x] API: `GET /departments` paginates and filters by `q` / `parent_id`.
- [x] API (failure): duplicate department name in one tenant → `409 department_name_taken`.
- [x] API (failure): cycle in the department tree → `400 department_cycle`.
- [x] API (failure): archiving a department with active employees → `409 department_archived_in_use` (unit-proven via `DepartmentUsagePort` fake; integration proof lands with Employees — Risk 1).
- [x] API (tenant isolation): tenant B reading tenant A's department → `404 department_not_found`.
- [x] API (denied role): an `Employee`-role request to `POST /departments` → `403`.
- [x] Audit: every create/update/archive produced an audit entry.
- [x] Web: the organization page lists, creates, edits and archives a department; a `409` renders as a visible error state.
- [x] Migrations: schema is created by Alembic, not by hand.

## Decisions (PO, 2026-09-24)

1. **Archiving blocks while active employees reference the department** →
   `409 department_archived_in_use`. People are transferred first, so reporting
   lines and history never dangle.
2. **Positions are tenant-global**, not owned by a department: the same title
   spans departments, and an employee links department + position separately.
3. **No CSV/bulk import in Phase 1.** Seeds cover dev needs; import deserves its
   own feature (validation, partial failure, error reporting).
4. **No Teams in Phase 1.** Departments + locations are sufficient; revisit when
   Phase 2 RBAC needs a "team" that is not a department.
5. **Restore is in scope** — the trivial inverse of archive, and it removes any
   incentive to work around a wrong archive by deleting data.

## Plan

Planned 2026-09-24 (planner). **Contract-first:** T1 pins the API contract in
this spec; the backend (T2–T7) is built against it; the frontend (T8–T9)
consumes only the regenerated `@workpulse/api-client` — no hand-written HTTP on
either side, so the surfaces cannot drift. This feature also lands the
persistence foundation listed in `tasks/TODO.md` "Next" (ADR 0006 installs the
backend stack with the first persistence feature). Every task ends with
`make check` green. No event contracts: Phase 1 organization changes are
audited, not broadcast (see `## Events`).

### Acceptance criteria → where they are satisfied

| # | Criterion (restated) | Changes that satisfy it | Proof |
| --- | --- | --- | --- |
| AC1 | `POST /api/v1/departments` valid payload → `201` + created Department | T4 `DepartmentService.create` · T6 route `createDepartment` | `tests/integration/test_departments_api.py` |
| AC2 | `GET /api/v1/departments` paginates, filters `q` / `parent_id` | T5 repository (ILIKE, parent filter, count, clamp) · T6 route | `test_departments_api.py` |
| AC3 | duplicate name in tenant → `409 department_name_taken` | T4 uniqueness rule; T5 DB unique constraint as backstop (IntegrityError mapped) | unit `test_service_departments.py` · int `test_departments_failures.py` |
| AC4 | cycle in department tree → `400 department_cycle` | T4 ancestor walk on create/update (self-parent and descendant-parent) | unit + int as above |
| AC5 | archive department with active employees → `409 department_archived_in_use` | T4 `DepartmentUsagePort` check inside `archive`; T6 wires `NullDepartmentUsage` (no employees table exists yet) | unit with fake port; integration proof **deferred to `employees.md`** (Risk 1) |
| AC6 | tenant B reads tenant A's department → `404 department_not_found` | T5 tenant-scoped repositories (`WHERE tenant_id = :tenant`) | `test_tenant_isolation.py` |
| AC7 | `Employee`-role `POST /departments` → `403` | T3 identity dep · T4 `policies.ensure_can_manage` (hr/admin/super_admin) · T6 routes call policy | unit `test_policies.py` · int `test_roles_api.py` |
| AC8 | every create/update/archive wrote an audit entry | T3 `AuditWriter` port · T5 `audit_entries` table + Postgres writer · T4 services emit | `test_audit_integration.py` |
| AC9 | web page lists/creates/edits/archives a department; `409` renders visibly | T8 departments page, hooks, `ApiError.code` → field-error mapping | `apps/web/tests/organizations/departments.test.tsx` (gate `make check-web`) |
| AC10 | schema created by Alembic, not by hand | T5 Alembic init + revision `0001_organizations` | `test_migrations.py` (upgrade head, downgrade base) |

### T1 — Pin the API contract (docs only)

Rewrite `## API contract (proposed)` as final: drop every "proposed" marker
(the soft-delete rule, the archive-block edge case — PO Decisions #1/#5 already
confirm both), note the Surfaces-table E2E row as deferred to Employees
(Risk 4), and add these pinned deltas verbatim:

- **Pagination**: `?page=` default 1 (clamp ≥ 1); `?page_size=` default 20,
  max 100 (clamped, never an error). Response `page` object:
  `{"page", "page_size", "total_items", "total_pages"}`. Default order:
  `name`/`title` ascending.
- **Filters**: `q` = case-insensitive substring of `name`/`title`; `parent_id`
  (departments only, exact UUID); `include_archived` (bool, default `false`).
  `GET /{id}` returns archived resources (they stay readable); `PATCH /{id}`
  on an archived resource is allowed.
- **PATCH semantics**: absent field = unchanged; explicit `null` for
  `parent_department_id` moves the department to the root.
- **Archive/restore**: archive body `{"reason"?}` — the reason is stored on the
  audit entry only, not the resource. Archive-of-archived and
  restore-of-active are `200` no-ops (no audit entry); real transitions audit.
  No DELETE routes exist.
- **Uniqueness**: DB unique constraints `(tenant_id, name)` / `(tenant_id, title)`
  across **all** rows, archived included — an archived row keeps its name and
  restore can never conflict (Risk 2, ADR candidate).
- **Parent rules**: parent must exist in the tenant (else `404
  department_not_found`); self or descendant → `400 department_cycle`; the
  archived state of a parent is **not** checked (Risk 3).
- **Timezone**: validated in the service via `zoneinfo.ZoneInfo` →
  `422 invalid_timezone` in the uniform error shape (never FastAPI's `detail`
  shape). Required on location create, optional on patch.
- **Identity** (ADR 0004): `X-Tenant-Id`, `X-Actor-Id` (UUIDs), `X-Role`
  (case-insensitive, one of `employee|manager|hr|finance|admin|super_admin`).
  Missing/malformed → `400 invalid_identity`; unknown role → `403 forbidden`
  (fail closed); policy denial → `403 forbidden`.
- **Roles**: read = any valid role; manage (create/update/archive/restore) =
  `hr`, `admin`, `super_admin` — the `.ai/security.md` matrix governs where it
  is wider than this spec's 4-column table.
- **Error code → status**: `department_not_found` / `position_not_found` /
  `location_not_found` 404 · `department_name_taken` / `position_title_taken` /
  `location_name_taken` / `department_archived_in_use` 409 · `department_cycle`
  400 · `invalid_timezone` 422 · `forbidden` 403 · `invalid_identity` 400 ·
  `validation_error` 422 (uniform handler for FastAPI RequestValidationError).
  Body always `{"error": {"code", "message"}}`; messages never echo field values.
- **operationIds** (drive orval hook names): `listDepartments`,
  `createDepartment`, `getDepartment`, `updateDepartment`, `archiveDepartment`,
  `restoreDepartment` — same six for `…Positions` and `…Locations` (18 routes).
- **Ids/time**: UUIDv7 ids generated in the service (`uuid-utils`);
  `created_at`/`updated_at` are DB defaults; `archived_at` and audit
  `occurred_at` come from a `now` parameter injected at the router edge (time
  is a parameter, never `datetime.now()` inside business logic).
- Read schemas are titled exactly `Department`, `Position`, `Location`;
  positions/locations mirror the departments route table at `/api/v1/positions`
  and `/api/v1/locations`.

**Verify**: review only — every acceptance criterion and edge case maps to a
pinned route and code; no code touched, `make check` unchanged.

### T2 — API tooling & persistence foundation

- `apps/api/pyproject.toml` (+ `uv.lock` via `uv add`): runtime deps
  `sqlalchemy[asyncio]>=2.0`, `asyncpg`, `alembic`, `structlog`, `uuid-utils`;
  dev deps `ruff`, `pyright`, `pytest-asyncio`; config `[tool.ruff]` (py312,
  lint select `E,F,I,UP`), `[tool.pyright]` (`typeCheckingMode = "standard"`,
  include `app`/`tests`/`alembic`/`scripts`, `venvPath = "."`, `venv = ".venv"`),
  `[tool.pytest.ini_options] asyncio_mode = "auto"`.
- `docker-compose.yml`: add `postgres-test` (postgres:16, host port **5433**,
  DB `workpulse_test`, user/pass `workpulse`, tmpfs data dir, healthcheck).
- `Makefile`: new `db-test-up` (`docker compose up -d --wait postgres-test`);
  `check-api` depends on it and runs `ruff check` + `ruff format --check` +
  `pyright` + `pytest` (ADR 0006, `apps/api/AGENTS.md`).
- `.env.example` + `apps/api/app/config.py`: `DATABASE_TEST_URL` /
  `database_test_url` (`postgresql+asyncpg://workpulse:workpulse@localhost:5433/workpulse_test`).
- `apps/api/app/shared/logging.py` + `apps/api/app/main.py`: structlog setup
  (pretty console in dev, JSON otherwise) + middleware binding `request_id`
  and `tenant_id`; never PII.
- `apps/api/app/infrastructure/postgres/{__init__,base,engine,session}.py`:
  `Base` (DeclarativeBase + constraint naming convention), lazily-created
  async engine/session factory from `settings.database_url`, `get_session`
  (session-per-request, commit/rollback).
- `apps/api/app/dependencies.py`: `SessionDep`.

**Verify**: `make db-up` brings up both services; `make check-api` runs the
hardened gate green (fix any ruff/pyright findings in existing scaffold files
in this task; `test_health.py` still passes).

### T3 — Shared plumbing: identity · pagination · audit port · uniform errors

- `apps/api/app/shared/identity.py`: `Role` str-enum (six pinned values),
  `DevIdentity` (`tenant_id: UUID`, `actor_id: UUID`, `role: Role`),
  `IdentityDep` parsing the three headers exactly per the pinned contract
  (fail closed).
- `apps/api/app/shared/pagination.py`: `PageParams` dependency (clamping per
  contract), `Page` model `{page, page_size, total_items, total_pages}`.
- `apps/api/app/shared/audit.py`: `AuditEntryCreate` (`tenant_id`, `actor_id`,
  `action`, `entity_type`, `entity_id`, `occurred_at`, `reason?`, `changes?`)
  and `AuditWriter` protocol (single async `write`); append-only — no
  update/delete path anywhere.
- `apps/api/app/shared/errors.py`: add `ForbiddenError` (403 `forbidden`),
  `InvalidIdentityError` (400 `invalid_identity`), and a
  `RequestValidationError` handler returning `422 validation_error` in the
  uniform shape.

**Tests** (unit, no I/O — `make check-api`): `tests/unit/test_identity.py`
(valid headers; role case-insensitive; missing header → `invalid_identity`;
unknown role → `forbidden`), `test_pagination.py` (clamps: `page=0`,
`page_size=10_000`), `test_audit_entry.py` (entry carries exactly the pinned
fields — no PII slots), `test_errors.py` (malformed JSON body → uniform
`validation_error` shape).

### T4 — Organizations domain core (pure, no I/O)

`apps/api/app/domains/organizations/`:

- `models.py` — SQLAlchemy models `Department`, `Position`, `Location` on the
  infra `Base`: UUIDv7 pk default, `tenant_id`, `name`/`title`,
  `parent_department_id` (self-FK, nullable), `description`/`address`,
  `timezone`, `archived_at`, timestamps, the pinned unique constraints.
- `schemas.py` — Pydantic wire models titled `Department`/`Position`/
  `Location` (read), `*Create`, `*Update` (all fields optional; explicit-null
  semantics for parent), `ArchiveRequest`, `*ListPage {items, page}`.
- `exceptions.py` — one `WorkPulseError` subclass per pinned code/status.
- `policies.py` — `ensure_can_read(identity)` (any valid role),
  `ensure_can_manage(identity)` (hr/admin/super_admin else `ForbiddenError`).
- `repository.py` — `DepartmentRepository` / `PositionRepository` /
  `LocationRepository` protocols (tenant_id is an explicit argument on every
  method; list methods take filters + page params and return items + total),
  plus `DepartmentUsagePort` (`count_active_employees(tenant_id, department_id)
  -> int`) and `NullDepartmentUsage` (returns 0) — the seam Employees replaces.
- `service.py` — `DepartmentService`, `PositionService`, `LocationService`.
  All mutations take `now: datetime`. Rules: uniqueness → `*_taken`; parent
  existence → `department_not_found`; cycle via tenant-scoped ancestor walk →
  `department_cycle`; archive checks `DepartmentUsagePort` →
  `department_archived_in_use`; archive/restore no-op semantics per contract;
  timezone via `zoneinfo` → `invalid_timezone`; every real transition writes an
  `AuditEntryCreate` (`department.created|updated|archived|restored`, etc.;
  `changes` = old/new values — org reference data is non-PII; archive `reason`
  passed through).

**Tests** (unit — must pass with `postgres-test` stopped, testing.md rule 4):
`tests/unit/organizations/fakes.py` (in-memory repos honoring tenant scope +
filters, settable usage-port fake, audit recorder), `test_service_departments.py`
(create/get/list/update/archive/restore happy paths; duplicate name; self- and
descendant-parent cycle; archived-in-use via fake port; audit recorded per real
transition and **not** on no-ops), `test_service_positions_locations.py`
(happy paths; `position_title_taken`; `location_name_taken`; `invalid_timezone`
e.g. `Mars/Olympus_Mons`), `test_policies.py` (employee denied, manager denied,
hr/admin/super_admin allowed, read allowed for all).

**Verify**: `make check-api`.

### T5 — Migration 0001 + Postgres repositories + audit writer

- `apps/api/alembic.ini`, `apps/api/alembic/env.py` (async; imports
  `Base.metadata`, `app.domains.organizations.models`, and the audit model so
  autogenerate sees everything), `alembic/script.py.mako`,
  `alembic/versions/0001_organizations.py` (revision `0001`, autogenerate →
  review): tables `departments`, `positions`, `locations` (pinned constraints
  and indexes: unique `(tenant_id, name|title)`, FK parent, index `tenant_id`,
  index `parent_department_id`) and `audit_entries` (+ index tenant/entity).
- `apps/api/app/infrastructure/postgres/models_audit.py` — `AuditEntry` ORM.
- `apps/api/app/infrastructure/postgres/audit.py` — `PostgresAuditWriter`
  (insert-only).
- `apps/api/app/infrastructure/postgres/repositories/organizations.py` —
  the three protocol implementations: every statement carries
  `WHERE tenant_id = :tenant`; list applies `q` (ILIKE), `parent_id`,
  `include_archived`, name/title ASC, LIMIT/OFFSET + total count; create maps
  unique-violation `IntegrityError` → the domain `*_taken` error.
- `apps/api/tests/integration/conftest.py` — sets `DATABASE_URL` from
  `DATABASE_TEST_URL` **before** importing `app.*`; runs `alembic upgrade head`
  once per session; truncates all tables between tests; fixtures: session,
  fully-wired services (real repos + `NullDepartmentUsage` + audit writer),
  `client_factory(headers)` → `httpx.AsyncClient(ASGITransport(app))` with
  dev-identity headers.

**Tests** (integration, gate `make check-api`): `test_migrations.py` — against
a throwaway database created on the `postgres-test` server
(`workpulse_migration_check`): `upgrade head` creates the four tables,
`downgrade base` removes them cleanly (AC10). `test_repositories_organizations.py`
— tenant scoping both directions, `q`/`parent_id`/`include_archived` filters,
pagination totals, duplicate insert → `*_taken`.

### T6 — Routers + DI + HTTP tests + contract regeneration

- `apps/api/app/api/v1/organizations.py` — 18 routes (6 × 3 collections) with
  pinned operationIds and response models; thin: `IdentityDep` → policy →
  service, `now=datetime.now(timezone.utc)` taken at this edge only.
- `apps/api/app/api/v1/__init__.py` — mount the routers.
- `apps/api/app/dependencies.py` — service dependencies wiring session →
  repositories → audit writer → `NullDepartmentUsage` → services.
- Run `make gen-api` — regenerates `contracts/openapi/openapi.json` and
  `packages/api-client/src/{index.ts,index.schemas.ts}` (hooks
  `useListDepartments`, `useCreateDepartment`, …).

**Tests** (integration): `test_departments_api.py` (AC1 `201` + exact body
shape; AC2 default list excludes archived, `q`, `parent_id`, 25-row pagination,
`page_size=1000` clamped to 100; get/patch rename/re-parent/null-to-root;
archive → `archived_at` set, hidden from default list, still `GET`-able;
restore; no-op archive/restore `200`), `test_departments_failures.py` (AC3 409
— including an archived row holding its name; AC4 400 self-parent and
descendant-parent; unknown id 404; parent not in tenant 404),
`test_tenant_isolation.py` (AC6: B get/patch/archive/restore A's department,
position, location → 404 with the `*_not_found` code; B's lists contain none of
A's rows; the same name in two tenants is allowed), `test_roles_api.py` (AC7:
employee POST → `403 forbidden`; manager POST → 403; hr POST → 201; admin
archive → 200; missing headers → `400 invalid_identity`; `X-Role: ceo` →
403), `test_audit_integration.py` (AC8: create/patch/archive/restore each
wrote one row with correct `actor_id`/`action`/`entity_type`/`entity_id`;
archive `reason` recorded; no-op writes no row; `changes` holds old/new name),
`test_positions_api.py` (CRUD happy + `position_title_taken` 409),
`test_locations_api.py` (CRUD happy + `location_name_taken` 409 +
`invalid_timezone` 422).
**Contract test**: `apps/api/tests/contract/test_openapi_drift.py` —
`app.openapi()` deep-equals the checked-in `contracts/openapi/openapi.json`;
a stale contract is a red gate, so web and API cannot drift.

**Verify**: `make check-api` green; re-running `make gen-api` produces no
further diff; `make check-web` still green (client growth is additive).

### T7 — Seeds

- `database/postgres/seeds/001_organizations.sql` — fixed UUIDs (dev tenant
  `00000000-0000-4000-8000-000000000001`, dev actor `…0002`), ~6 nested
  departments, ~5 positions, ~3 locations (one `Asia/Ho_Chi_Minh`), synthetic
  only, `INSERT … ON CONFLICT DO NOTHING` (idempotent).
- `Makefile` — `db-seed` (psql into the dev `postgres` service);
  `database/postgres/README.md` — usage note.

**Verify**: `make db-up && make db-seed && make db-seed` (second run is a
no-op); with `make dev-api`, `curl` `GET /api/v1/departments` with the three
dev-identity headers returns the seeded items.

### T8 — Web: dev-identity transport + departments page

- `packages/api-client/src/mutator/custom-fetch.ts` — inject `X-Tenant-Id`,
  `X-Actor-Id`, `X-Role` from `NEXT_PUBLIC_DEV_TENANT_ID` /
  `NEXT_PUBLIC_DEV_ACTOR_ID` / `NEXT_PUBLIC_DEV_ROLE`, defaulting to the seed
  tenant/actor and role `admin` (temporary trust boundary per ADR 0004 —
  removed with Phase 2 authN). `.env.example` gains the three variables.
- `apps/web/components/ui/select.tsx` — added via the shadcn CLI (uses the
  existing `radix-ui` dependency; no new package) for the parent picker.
- `apps/web/features/organizations/` — `api.ts` (query-key prefixes +
  `invalidateDepartments/Positions/Locations(queryClient)` matching generated
  URL-based keys), `types.ts` (re-export generated `Department`, `Page`, …),
  `schema.ts` (zod department form: name required, parent optional
  UUID-or-null), `hooks/use-departments.ts` (wrap the generated list/create/
  update/archive/restore hooks: invalidate + sonner toasts on success;
  `ApiError.code` → field-error map, `department_name_taken` → `name`),
  `components/DepartmentsTable.tsx` and `components/DepartmentFormDialog.tsx`
  (client leaves; table has loading/empty/error states, `include_archived`
  toggle, row actions Edit / Archive / Restore; dialog is RHF + zod and renders
  server errors as visible field errors).
- Routes: `apps/web/app/(dashboard)/settings/organization/layout.tsx` (server,
  renders a small client tab-nav leaf), `…/organization/page.tsx` (redirect →
  `departments`), `…/organization/departments/page.tsx`;
  `apps/web/app/(dashboard)/layout.tsx` gains an "Organization" nav link.

**Tests** (gate `make check-web`): extend
`apps/web/tests/api/custom-fetch.test.ts` (dev-identity headers sent by
default; env overrides win); `apps/web/tests/organizations/departments.test.tsx`
with the api-client module mocked — renders fetched rows; create success
invalidates the list; **create failing with `409 department_name_taken` shows a
visible field error + toast (AC9 failure case)**; empty state; list-error
state; archive action round-trip.

**Verify**: `make check-web`; optional manual smoke against `make dev-api` +
seeded DB.

### T9 — Web: positions & locations pages + docs sync

- Positions: `components/PositionsTable.tsx`, `components/PositionFormDialog.tsx`
  (title + description; add `components/ui/textarea.tsx` via shadcn),
  `hooks/use-positions.ts`, zod schema; route
  `…/settings/organization/positions/page.tsx`.
- Locations: `components/LocationsTable.tsx`, `components/LocationFormDialog.tsx`
  (name, address, timezone text input), `hooks/use-locations.ts`; route
  `…/settings/organization/locations/page.tsx`; `invalid_timezone` maps to a
  timezone field error.
- **Tests**: `apps/web/tests/organizations/positions.test.tsx` (list + create
  happy path; `position_title_taken` 409 → visible error),
  `locations.test.tsx` (happy path; `invalid_timezone` → field error).
- **Docs sync (same change)**: tick this spec's acceptance criteria and set
  `Status: done`; `tasks/TODO.md` — move the organizations item and the landed
  "Next" items (persistence foundation, dev-identity dependency, audit log,
  seeds, gen-api re-run) to Done; add Next item: "Employees: replace
  `NullDepartmentUsage` with the real usage check and add the integration test
  for `department_archived_in_use`".

**Verify**: `make check` fully green; docs match the diff.

### Test strategy

Gates are the repo's real ones: `make check-api` covers
`apps/api/tests/{unit,integration,contract}`; `make check-web` covers
`apps/web/tests`; `tests/e2e/` gets nothing in this feature (Risk 4).

| Gate / level | Added | Failure cases covered |
| --- | --- | --- |
| `check-api` · unit | identity, pagination, audit entry, errors, service ×2, policies | duplicate name · cycle (self + descendant) · archived-in-use (fake port) · invalid timezone · employee/manager denied · unknown role fail-closed · missing headers · page_size clamp · malformed body → uniform `validation_error` |
| `check-api` · integration (postgres-test) | migrations, repositories, departments api, departments failures, roles, tenant isolation, audit, positions api, locations api | 409 taken (all three collections) · 400 cycle · 404 cross-tenant on get/patch/archive/restore/list · 403 employee + manager · 422 invalid_timezone · no-op archive writes no audit row |
| `check-api` · contract | `test_openapi_drift.py` | stale `contracts/openapi/openapi.json` → red (surfaces cannot drift) |
| `check-web` | custom-fetch identity, departments / positions / locations feature tests | 409 → visible field error + toast (AC9) · invalid_timezone → field error · loading / empty / error states |

Fixture rules: fixed UUIDs, injected `now`, synthetic names only; unit tests
never touch the database; assertions are on behavior and stable error codes.

### Risks · unknowns · ADR candidates

1. **AC5 cannot be integration-tested until Employees exists.** The rule is
   proven at unit level through `DepartmentUsagePort` + fake; production wires
   `NullDepartmentUsage` (returns 0, so archive succeeds). Acceptable because
   no employees table exists yet; `employees.md` must swap in the real check
   and add the integration test (tracked in `tasks/TODO.md` by T9).
2. **Uniqueness includes archived rows** — an archived department keeps its
   name forever (restore can never conflict; literal reading of "unique per
   tenant"). If the PO wants name reuse, that is a partial unique index +
   restore-conflict code → migration + contract change. **ADR candidate 0007:
   soft-delete semantics for reference data** (`archived_at` + uniqueness +
   restore) — expensive to reverse once tenant data exists.
3. **Spec gap — archived parent**: creating/moving a department under an
   archived parent is allowed (the spec requires only existence + acyclicity).
   Flagged for PO confirmation; adding the check later is service-only, no
   schema change.
4. **E2E conflict**: this spec's Surfaces table lists `tests/e2e/`, but
   `tests/e2e/README.md` and `tasks/TODO.md` pin Playwright to Employees (the
   first feature that needs cross-surface automation). The plan follows the
   README — no Playwright dependency is added here (no dependency without spec
   justification); T1 records the deferral in the spec.
5. **Gate hardening may flag existing scaffold**: switching `check-api` to
   ruff + pyright can surface findings in current files; T2 fixes them in-task
   (expected small).
6. **Alembic autogenerate drift**: `env.py` must import every model module;
   the migration test plus the OpenAPI-drift contract test are the guard rails.
7. **Dev-identity headers in the web mutator** are a temporary trust boundary
   (ADR 0004): env-overridable so CI/e2e can switch roles, removed with Phase 2
   authN.
