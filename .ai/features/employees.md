# Feature: Employees

- **Status:** done
- **Phase:** 1
- **Size:** M
- **Surfaces:** api+web+events (contracts only in Phase 1)
- **Links:** `.ai/domain.md` (Employees context) · `.ai/security.md` (PII, audit) · depends on `organizations.md`

## Problem

The employee record is the aggregate every other context references: attendance
belongs to an employee, leave is requested by one, payroll pays one, analytics
groups by one. Phase 1 needs a trustworthy record with employment history,
because "who works where, in what role, since when" is the question the rest of
the product answers.

## Business rules

- `employee_code` and `email` are unique **per tenant**.
- An employee references a department, a position and a location — all must
  exist, belong to the tenant, and not be archived.
- `manager_id` must be an active employee of the same tenant and must not be the
  employee themself.
- **Employment history is append-only**: a transfer creates a new employment
  period with `effective_from`; the previous period is closed, never rewritten.
  No two periods for one employee may overlap.
- Status lifecycle: `active` ⇄ `on_leave` → `terminated` (terminal).
- A terminated employee cannot be assigned as a manager, cannot check in
  (enforced by Attendance), and cannot be transferred.
- Termination is a soft state change with `effective_date` + `reason`; records
  are never hard-deleted.
- PII rules from `.ai/security.md` are mandatory: no names/emails/phones in
  logs, error messages or event payloads; every mutation writes an audit entry.

## Scope

- CRUD + paginated, filterable, searchable employee list.
- Employee detail with current employment + full history.
- Transfer (department / position / location / manager change with effective date).
- Termination.
- Events **contracts** under `contracts/events/employees/` (published in Phase 3).
- Web: employee list, detail page, create/edit form, transfer + termination dialogs.

## Out of scope

- **Documents** (presigned upload/download — Phase 4+, security review first).
- **Skills** (belongs with Performance).
- Authentication and real user accounts (landed in Phase 2, `.ai/features/identity-access.md`) — this feature consumes the session-resolved identity.
- Re-hire of a terminated employee (see `## Decisions`).
- Bulk import / CSV, org-chart views, self-service profile editing beyond `me`.

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/employees/` |
| API router | `apps/api/app/api/v1/employees.py` |
| Events | `contracts/events/employees/employee-*.v1.json` |
| Web feature | `apps/web/features/employees/` |
| Web routes | `apps/web/app/(dashboard)/employees/` |
| API tests | `apps/api/tests/unit` · `tests/integration` · `tests/contract` |
| E2E | `tests/e2e/` |

## Roles & access

| Capability | Employee | Manager | HR | Admin |
| --- | --- | --- | --- | --- |
| `GET /employees/me` | own | own | own | own |
| List / read employees | — | team | Y | Y |
| Create / update | — | — | Y | Y |
| Transfer / terminate | — | — | Y | Y |

## API contract (final — pinned by T1, 2026-09-28)

| Method & path | Input | Response |
| --- | --- | --- |
| `GET /api/v1/employees` | `?q=&department_id=&position_id=&location_id=&status=&manager_id=&page=&page_size=` | `200 {"items": EmployeeSummary[], "page": Page}` |
| `POST /api/v1/employees` | `CreateEmployee` | `201 Employee` |
| `GET /api/v1/employees/me` | — | `200 Employee` |
| `GET /api/v1/employees/{id}` | — | `200 EmployeeDetail` (current employment + history) · `404` |
| `PATCH /api/v1/employees/{id}` | `UpdateEmployee` (profile fields) | `200 Employee` |
| `POST /api/v1/employees/{id}/transfers` | `TransferRequest` | `200 Employment` (the new period) |
| `POST /api/v1/employees/{id}/termination` | `TerminationRequest` | `200 Employee` |
| `GET /api/v1/employees/{id}/employments` | — | `200 Employment[]` (newest first) |

```json
CreateEmployee {
  "first_name", "last_name", "email", "phone"?,
  "department_id", "position_id", "location_id", "manager_id"?,
  "employment_type": "full_time|part_time|contract|intern",
  "hired_at", "employee_code"?
}

Employee {
  "id", "tenant_id", "employee_code", "first_name", "last_name", "email",
  "phone", "department_id", "position_id", "location_id", "manager_id",
  "employment_type", "status", "hired_at", "terminated_at",
  "created_at", "updated_at"
}

Employment {
  "id", "tenant_id", "employee_id", "department_id", "position_id",
  "location_id", "employment_type",
  "effective_from", "effective_to": null,
  "created_at"
}

EmployeeSummary {
  "id", "employee_code", "first_name", "last_name", "email",
  "department_id", "position_id", "location_id", "manager_id",
  "employment_type", "status", "hired_at", "terminated_at"
}

EmployeeDetail = Employee + {
  "current_employment": Employment,
  "employments": Employment[]
}

UpdateEmployee { "first_name"?, "last_name"?, "email"?, "phone"? }

TransferRequest {
  "department_id", "position_id"?, "location_id"?, "manager_id"?,
  "effective_from", "reason"?
}

TerminationRequest { "effective_date", "reason" }
```

Error codes: `employee_not_found` · `employee_email_taken` ·
`employee_code_taken` · `employee_manager_invalid` · `employee_self_manager` ·
`employee_already_terminated` · `employment_period_overlap` ·
`employee_transfer_effective_invalid` · `transfer_target_archived` ·
`department_not_found` · `position_not_found` · `location_not_found`.

### Pinned details

- **Dates**: `hired_at`, `effective_from`, `effective_to`, `effective_date`,
  `terminated_at` are `date` (`YYYY-MM-DD`); `created_at`/`updated_at` are
  `date-time`; `terminated_at` = the termination `effective_date`.
- **PATCH scope**: profile fields only (name/email/phone). Manager, type and
  assignment changes go through transfers (they carry `effective_from`).
  PATCH on a terminated employee is allowed (record corrections; history is
  untouched). Absent field = unchanged; `phone` may be set to `null`.
- **Assignment references**: `department_id`/`position_id`/`location_id` must
  exist in the tenant and not be archived, on create **and** transfer;
  `manager_id` must be an active employee of the tenant and ≠ the employee.
  Omitted transfer fields inherit the current period's values (department is
  the one required field).
- **Transfer semantics** (Decision #1 made concrete): periods are half-open
  `[effective_from, effective_to)`; `effective_to` `null` = open. A transfer
  closes the current period at `effective_from` and appends a new open one in
  one transaction — rows are appended, never rewritten. `effective_from` must
  be `>= hired_at`, `>= current.effective_from` (else the new period would
  overlap a closed one), and `<= today` (no future-dated scheduling in
  Phase 1). Transfer of a terminated employee → `409
  employee_already_terminated`.
- **Termination**: sets `status=terminated`, `terminated_at=effective_date`,
  closes the open period at `effective_date`, and nulls `manager_id` on the
  employee's direct reports (one audit entry per report). The reason is
  stored on the audit entry only (like archive `reason`), never on events.
- **Clarifications recorded during the T3–T6 build** (implicit in the pinned
  rules above): `EmployeeDetail.current_employment` is `null` when no period is
  open — a terminated employee still reads `200` with the closed history.
  Termination backdating past the open period's `effective_from` is rejected
  with `409 employment_period_overlap` (the same period invariant transfers
  enforce: a period may never close before it starts). Email uniqueness is
  case-insensitive (functional unique index on `(tenant_id, lower(email))`),
  and the DB enforces at most one open period per employee (partial unique
  index) as the backstop behind close+append transfers.
- **`employee_code`** (Decision #2): auto `EMP-00001` per tenant (max numeric
  suffix + 1, zero-padded to 5) when omitted; overridable on create; unique
  per tenant across all rows → `409 employee_code_taken`.
- **`q` & ordering** (Decision #6): `q` = case-insensitive substring over
  `first_name`, `last_name`, `email`, `employee_code`. Default order
  `last_name`, `first_name` ascending (stable pagination); `page`/`page_size`
  and the `Page` object reuse the shape pinned in organizations. `GET /{id}`
  of a terminated employee still returns `200`.
- **Roles** (Decision #7, ADR 0004's Phase 2 swap; identity as pinned in
  organizations): `/employees/me` resolves the employee linked to the acting
  user (`identity.employee_id`, any role) → `404 employee_not_found` when the
  user has no employee link. `manager` role: list is server-scoped to direct
  reports (`manager_id == identity.employee_id`; an unlinked manager sees
  nobody), detail only for own reports (otherwise `404 employee_not_found`,
  never 403 — don't leak existence); create/update/transfer/terminate are
  `hr`/`admin`/`super_admin` only (`403 forbidden`). `employee` role: `/me`
  only. `.ai/security.md` governs where wider.
- **Error code → status**: `employee_not_found` / `department_not_found` /
  `position_not_found` / `location_not_found` 404 · `employee_email_taken` /
  `employee_code_taken` / `employee_already_terminated` /
  `employment_period_overlap` / `transfer_target_archived` 409 ·
  `employee_manager_invalid` / `employee_self_manager` 400 ·
  `employee_transfer_effective_invalid` 422 (before `hired_at` or in the
  future) · create-with-archived reference reuses `transfer_target_archived`;
  transfer-of-terminated reuses `employee_already_terminated`; backdating
  past `current.effective_from` is `employment_period_overlap` · `forbidden`
  403 · `unauthenticated` 401 · `validation_error` 422 (uniform handler).
  Body always `{"error": {"code", "message"}}`; messages are static strings —
  they never echo field values.
- **operationIds** (drive orval hook names): `listEmployees`,
  `createEmployee`, `getCurrentEmployee`, `getEmployee`, `updateEmployee`,
  `transferEmployee`, `terminateEmployee`, `listEmployments` — 8 routes total.
- **Ids/time**: UUIDv7 ids and `employee_code` resolved in the service;
  `now` is a router-injected parameter (conventions). Status enum
  `active|on_leave|terminated` (`on_leave` is reserved for Attendance — no
  endpoint in this feature); `employment_type`
  `full_time|part_time|contract|intern`.

## Events

Contracts written in Phase 1, published in Phase 3 (envelope:
`contracts/events/common/event-envelope.v1.json`):

| `event_type` | Emitted when | Payload (identifiers only) |
| --- | --- | --- |
| `employee.created` | record created | `employee_id`, `department_id`, `position_id`, `location_id`, `employment_type` |
| `employee.updated` | profile fields change | `employee_id`, `changed_fields[]` (names, not values) |
| `employee.transferred` | new employment period starts | `employee_id`, `from_department_id`, `to_department_id`, `effective_from` |
| `employee.terminated` | employment ends | `employee_id`, `effective_date` |

No names, emails or salary in any payload.

## Edge cases

- Duplicate email within the tenant → `409 employee_email_taken`.
- Manager set to self → `400 employee_self_manager`.
- Manager is terminated or in another tenant → `400 employee_manager_invalid`.
- Transfer to an archived department → `409 transfer_target_archived`.
- Transfer with `effective_from` before `hired_at` → `422`.
- Overlapping employment periods → `409 employment_period_overlap`.
- Terminate an already-terminated employee → `409 employee_already_terminated`.
- Terminate a manager who has direct reports → `manager_id` is nulled on each report (see `## Decisions` #4).
- Tenant A reads tenant B's employee → `404 employee_not_found`.
- List filters combined with pagination stay stable across pages.

## Acceptance criteria

- [x] API: `POST /employees` with valid references returns `201` and persists an initial employment period.
- [x] API: `GET /employees` filters by department/status and paginates.
- [x] API: a transfer appends a new employment period and closes the previous one (history is intact).
- [x] API (failure): duplicate email → `409 employee_email_taken`.
- [x] API (failure): self-manager → `400 employee_self_manager`.
- [x] API (failure): transfer to an archived department → `409 transfer_target_archived`.
- [x] API (tenant isolation): cross-tenant read/update → `404`.
- [x] API (denied role): a `Manager`-role create request → `403`.
- [x] Contracts: the four event schemas exist under `contracts/events/employees/` and validate the payloads the service produces.
- [x] Audit: create / update / transfer / terminate each wrote an audit entry with no PII values.
- [x] Logs: no name, email or phone appears in any log line produced by these flows.
- [x] Web: list → detail → transfer flow works; a `409` shows a visible error state.

## Decisions (PO, 2026-09-24)

1. **Append-only employment periods.** A transfer closes the current period and
   opens a new one; nothing is rewritten. This is what makes "who was in which
   department in month X" answerable for analytics and payroll.
2. **`employee_code` is auto-generated per tenant** (`EMP-00001`, zero-padded,
   monotonic) and may be overridden on create for HR-supplied codes. Uniqueness
   is per tenant.
3. **Documents deferred** to Phase 4+ (presigned URLs + a security review first);
   **skills deferred** to the Performance context.
4. **Terminating a manager nulls `manager_id` on their direct reports**, with an
   audit entry per change. Blocking termination would stall offboarding; an
   explicitly empty manager is visible and fixable.
5. **No re-hire in Phase 1.** The append-only history model supports adding a new
   employment period to a terminated employee later without a migration.
6. **`q` searches first name, last name, email and `employee_code`** — nothing
   free-text beyond those fields.
7. Identity is the **session-resolved identity** (Phase 2, ADR 0004's swap —
   `.ai/features/identity-access.md` T5), so `/employees/me` resolves from
   `identity.employee_id` (Phase 1's dev-identity header source was deleted);
   role checks are real code, not placeholders.

## Plan

Planned 2026-09-28. **Contract-first:** T1 pins the API contract in this spec,
T2 writes the event contracts (before any producer/consumer), the backend
(T3–T6) is built against them, the web (T7) consumes only the regenerated
`@workpulse/api-client`, and T8 lands the e2e layer this feature promised.
Every task ends with `make check` green. This feature also lands the two items
`tasks/TODO.md` already reserves for it: the real `DepartmentUsagePort` check
(T6, organizations Risk 1 / ADR 0007) and Playwright in `tests/e2e/` behind
`make check-e2e` (T8, `.ai/testing.md` decision 3).

### Acceptance criteria → where they are satisfied

| # | Criterion (restated) | Changes that satisfy it | Proof |
| --- | --- | --- | --- |
| AC1 | `POST /api/v1/employees` valid payload → `201`, persists initial employment period | T3 models (`employees` + `employments` rows); T4 `EmployeeService.create` opens period `[hired_at, ∞)`; T5 route `createEmployee` | `tests/integration/test_employees_api.py` (exact pinned body + period row asserted) |
| AC2 | `GET /api/v1/employees` filters by department/status and paginates | T3 repository filters (`q`, `department_id`, `position_id`, `location_id`, `status`, `manager_id`) + `PageParams`; T5 route | `test_employees_api.py` (combined filters stable across pages) |
| AC3 | a transfer appends a new employment period, closes the previous, history intact | T4 `EmployeeService.transfer` (append-only, half-open periods); T3 employment rows never updated except closing `effective_to` | unit `test_service_employees.py` + integration history assertion |
| AC4 | duplicate email in tenant → `409 employee_email_taken` | T4 pre-check + T3 unique `(tenant_id, email)` backstop (IntegrityError mapped) | `test_employees_failures.py` |
| AC5 | manager set to self → `400 employee_self_manager` | T4 manager validation (create + transfer) | unit + `test_employees_failures.py` |
| AC6 | transfer to archived department → `409 transfer_target_archived` | T4 assignment-target check (exists, same tenant, not archived) on create + transfer | `test_employees_failures.py` |
| AC7 | cross-tenant read/update → `404 employee_not_found` | T3 tenant-scoped repositories; T4 get-by-tenant before every mutation | `test_employees_tenant_isolation.py` |
| AC8 | `Manager`-role create → `403` | T4 `policies.ensure_can_manage_employees` (hr/admin/super_admin); T5 routes call it | unit `test_policies.py` · `test_employees_roles.py` |
| AC9 | the four event schemas exist under `contracts/events/employees/` and validate the payloads the service produces | T2 JSON Schemas + `domains/employees/events.py` pure builders (the exact payload source Phase 3's publisher will call) + contract test validating builders against schemas | `tests/contract/test_employee_events.py` |
| AC10 | create / update / transfer / terminate each wrote an audit entry, no PII values | T4 audit writes (`action` `employee.created/updated/transferred/terminated` + per-report `employee.manager_cleared`); `changes` carries ids/state only, never names/emails/phones | `test_employees_audit.py` |
| AC11 | no name, email or phone in any log line of these flows | T4: static error messages, no value logging (conventions already forbid it); T5 test captures structlog around the flows | `test_employees_logs.py` (`structlog.testing.capture_logs`) |
| AC12 | web list → detail → transfer works; `409` shows a visible error state | T7 list/detail/transfer UI + `ApiError.code` → visible error mapping; T8 e2e proves the whole flow | `apps/web/tests/employees/*` (gate `make check-web`) · `tests/e2e/employees.spec.ts` (gate `make check-e2e`) |

### T1 — Pin the API contract (docs only)

Rewrite `## API contract (proposed)` as final — drop "proposed", and pin these
deltas (they are contract surface; review here, not during the build):

- **Shapes** (read schemas titled exactly these names):

  ```jsonc
  Employment {
    "id", "tenant_id", "employee_id", "department_id", "position_id",
    "location_id", "employment_type",
    "effective_from": date, "effective_to": date|null,   // null = open
    "created_at"
  }
  EmployeeSummary {   // list rows — Employee minus tenant_id, phone,
    // created_at, updated_at
    "id", "employee_code", "first_name", "last_name", "email",
    "department_id", "position_id", "location_id", "manager_id",
    "employment_type", "status", "hired_at", "terminated_at"
  }
  EmployeeDetail {    // = Employee + "current_employment": Employment
                      //   + "employments": Employment[] (newest first)
  }
  UpdateEmployee { "first_name"?, "last_name"?, "email"?, "phone"? }
  TransferRequest { "department_id", "position_id"?, "location_id"?,
    "manager_id"?, "effective_from": date, "reason"? }
  TerminationRequest { "effective_date": date, "reason" }
  ```

  `GET /employees/{id}/employments` → `200 Employment[]` newest first.
  Transfers return `200 Employment` (the new period) as proposed.
- **Dates**: `hired_at`, `effective_from`, `effective_to`, `effective_date`,
  `terminated_at` are `date` (`YYYY-MM-DD`); `created_at`/`updated_at` are
  `date-time`. `terminated_at` = the termination `effective_date`.
- **PATCH scope**: profile fields only (name/email/phone). Manager, type and
  assignment changes go through transfers (they carry `effective_from`).
  PATCH on a terminated employee is allowed (record corrections; history is
  untouched). Absent field = unchanged; `phone` may be set to `null`.
- **Assignment references**: `department_id`/`position_id`/`location_id` must
  exist in the tenant and not be archived, on create **and** transfer;
  `manager_id` must be an active employee of the tenant and ≠ the employee.
  Omitted transfer fields inherit the current period's values (department is
  the one required field).
- **Transfer semantics** (Decision #1 made concrete): periods are half-open
  `[effective_from, effective_to)`; a transfer closes the current period at
  `effective_from` and appends a new open one in one transaction — rows are
  appended/never rewritten. `effective_from` must be `>= hired_at`,
  `>= current.effective_from` (else the new period would overlap a closed one),
  and `<= today` (no future-dated scheduling in Phase 1). Transfer of a
  terminated employee → `409 employee_already_terminated`.
- **Termination**: sets `status=terminated`, `terminated_at=effective_date`,
  closes the open period at `effective_date`, and nulls `manager_id` on the
  employee's direct reports (one audit entry per report). Termination reason is
  stored on the audit entry only (like archive `reason`), never on events.
- **`employee_code`** (Decision #2): auto `EMP-00001` per tenant (max numeric
  suffix + 1, zero-padded to 5) when omitted; overridable on create; unique per
  tenant across all rows → `409 employee_code_taken`.
- **`q`** (Decision #6): case-insensitive substring over `first_name`,
  `last_name`, `email`, `employee_code`. Default order `last_name`,
  `first_name` ascending (stable pagination). `GET /{id}` of a terminated
  employee still returns `200`.
- **Roles** (Decision #7, ADR 0004's Phase 2 swap): `/employees/me` resolves
  the employee linked to the acting user (`identity.employee_id`, any role) →
  `404 employee_not_found` when the user has no employee link. `manager` role:
  list is server-scoped to direct reports (`manager_id == identity.employee_id`;
  an unlinked manager sees nobody), detail only for own reports (otherwise
  `404 employee_not_found`, never 403 — don't leak existence); create/update/
  transfer/terminate are HR/admin/super_admin only (`403 forbidden`).
  `employee` role: `/me` only. `.ai/security.md` governs where wider.
- **Error code → status** additions to the table above:
  `employee_transfer_effective_invalid` 422 (before `hired_at` or in the
  future) · create-with-archived reference reuses `transfer_target_archived`
  409 · transfer-of-terminated reuses `employee_already_terminated` 409 ·
  `employment_period_overlap` 409 (backdating past `current.effective_from`) ·
  `employee_manager_invalid` / `employee_self_manager` 400 · rest as listed.
  Messages are static strings; they never echo field values.
- **operationIds** (drive orval hook names): `listEmployees`, `createEmployee`,
  `getCurrentEmployee`, `getEmployee`, `updateEmployee`, `transferEmployee`,
  `terminateEmployee`, `listEmployments` — 8 routes total.
- **Ids/time**: UUIDv7 ids and `employee_code` resolved in the service;
  `now` is a router-injected parameter (conventions); status enum
  `active|on_leave|terminated` (`on_leave` is reserved for Attendance — no
  endpoint in this feature); `employment_type`
  `full_time|part_time|contract|intern`.

Verify: docs-only diff; `make check` still green.

### T2 — Event contracts + Phase-1 payload builders

Contracts before any producer/consumer (rule 4):

- `contracts/events/employees/employee-created.v1.json` — `data`:
  `employee_id`, `department_id`, `position_id`, `location_id`,
  `employment_type`.
- `contracts/events/employees/employee-updated.v1.json` — `data`:
  `employee_id`, `changed_fields[]` (field **names** only).
- `contracts/events/employees/employee-transferred.v1.json` — `data`:
  `employee_id`, `from_department_id`, `to_department_id`, `effective_from`.
- `contracts/events/employees/employee-terminated.v1.json` — `data`:
  `employee_id`, `effective_date`.

All four are JSON Schema draft 2020-12, `additionalProperties: false`,
identifiers only (validated by the contract test: no key outside the
identifier whitelist may exist).

- `apps/api/app/domains/employees/events.py` — pure builders
  (`employee_created(...) -> dict` etc.) returning full envelope-shaped dicts
  (`event_id` from `uuid7`, `version: 1`, envelope fields + `data`). This is
  the exact payload source Phase 3's publisher will call; **no publishing in
  Phase 1** (services may call the builders or not — the contract test proves
  the shapes either way; wiring them into the service's outbox is Phase 3).
- `apps/api/tests/contract/test_employee_events.py` — builds one event per
  builder and validates `data` against the domain schema and the whole event
  against `common/event-envelope.v1.json`; asserts no PII-shaped keys
  (name/email/phone/salary) appear in any payload.
- Dependency: `jsonschema` as a **dev/test dependency** of `apps/api`
  (`uv add --dev jsonschema`) — justified by AC9 (rule 10); nothing runtime
  depends on it.

Verify: `make check-api` (contract tests pass; no other test touched).

### T3 — Persistence: models · migration 0002 · repositories

- `apps/api/app/domains/employees/models.py` — ORM (organizations pattern,
  no mixins):
  - `Employee` → `employees`: `id` (uuid7 PK), `tenant_id`, `employee_code`,
    `first_name`, `last_name`, `email`, `phone?`, `employment_type`, `status`,
    `hired_at`, `terminated_at?`, `manager_id?` (FK `employees.id`),
    `created_at`, `updated_at`. Constraints: unique `(tenant_id, email)`,
    unique `(tenant_id, employee_code)` across all rows; indexes on
    `tenant_id`, `status`, `department_id`-era lookups live on `employments`.
  - `Employment` → `employments`: `id`, `tenant_id`, `employee_id` (FK),
    `department_id`, `position_id`, `location_id` (FKs to organizations
    tables), `employment_type`, `effective_from`, `effective_to?`,
    `created_at`. Check `effective_to IS NULL OR effective_to >= effective_from`;
    indexes on `(tenant_id, employee_id)`, `(tenant_id, department_id)`.
    Note: **no FK to departments/positions/locations is required to stay
    non-archived** — that is a service rule (archiving is soft).
- `apps/api/app/domains/employees/exceptions.py` — `WorkPulseError` subclasses
  for every code in the pinned table (statuses as pinned in T1).
- `apps/api/app/domains/employees/schemas.py` — Pydantic v2 I/O schemas exactly
  as pinned in T1 (`Employee`, `EmployeeSummary`, `EmployeeDetail`,
  `Employment`, `CreateEmployee`, `UpdateEmployee`, `TransferRequest`,
  `TerminationRequest`, `EmployeeListPage`, `EmploymentList`).
- `apps/api/app/domains/employees/repository.py` — Protocols only (no infra
  imports): `EmployeeRepository` (get/list/add/update/`email_taken`/
  `code_taken`/`next_employee_code`/`direct_reports`/`clear_manager`), 
  `EmploymentRepository` (current period, history, `add`, `close`) — every
  method takes `tenant_id` first, `list()` returns
  `tuple[Sequence[Schema], int]`.
- `apps/api/app/infrastructure/postgres/repositories/employees.py` — Postgres
  adapters (tenant `WHERE` on every statement; `IntegrityError` → `*_taken`).
- Alembic `apps/api/alembic/versions/0002_employees.py` (append-only; autogenerate → review).
- Tests: `tests/integration/test_employees_repository.py` (tenant scoping,
  filters+pagination, code generation monotonic per tenant, close/append) and
  the migration test pattern from organizations (`upgrade head` / `downgrade`).
- `tests/integration/conftest.py`: add `employees`, `employments` to `ALL_TABLES`
  truncation + service fixtures.

Verify: `make check-api` green (organizations tests untouched and green).

### T4 — Employees domain core (pure, no I/O)

- `apps/api/app/domains/employees/service.py` — `EmployeeService` (repo ports +
  `AuditWriter` + `now: datetime` params), mirroring `DepartmentService`:
  - `create`: refs exist/tenant/not-archived; manager valid (active, ≠ self);
    `employee_code` = payload or `next_employee_code` (monotonic per tenant,
    Decision #2); insert employee + initial employment period `[hired_at, ∞)`
    in one transaction; audit `employee.created` (ids/type only).
  - `list`/`get`/`current` (`/me`): filters as pinned; manager-role scoping
    applied in the service (repository gets `manager_id=identity.employee_id`
    forced; an unlinked manager sees nobody).
  - `update`: `exclude_unset` diffing (organizations pattern); email change →
    `email_taken` check; audit `employee.updated` with `changed_fields` names
    only (AC10/AC11 — never values).
  - `transfer`: terminated → `employee_already_terminated`; target refs
    checked (archived → `transfer_target_archived`); manager rules
    (`employee_self_manager` / `employee_manager_invalid`); effective-date
    rules (`employee_transfer_effective_invalid`, `employment_period_overlap`);
    close current period at `effective_from`, append new period with inherited
    omitted fields, update employee's current refs; audit
    `employee.transferred` (ids + date).
  - `terminate`: already terminated → `employee_already_terminated`; close open
    period at `effective_date`; `status=terminated`,
    `terminated_at=effective_date`; null `manager_id` on each direct report
    with an audit entry per change (Decision #4); audit `employee.terminated`
    (id + date + reason on the audit entry).
- `apps/api/app/domains/employees/policies.py` —
  `ensure_can_manage_employees` (hr/admin/super_admin),
  `ensure_can_read_employees` (hr/admin/super_admin = all; manager = own
  reports; employee = none), `ensure_can_read_self` (all roles).
- Unit tests `tests/unit/employees/` (`fakes.py` in-memory repos/writer;
  `test_service_employees.py` covering every business rule and edge case in
  this spec; `test_policies.py` covering the roles matrix incl. the
  manager-team row; a denied-role case per mutating use case).

Verify: `make check-api` green (pure unit; no DB).

### T5 — Routers + DI + HTTP tests + contract regeneration

- `apps/api/app/api/v1/employees.py` — the 8 routes as pinned (thin:
  `IdentityDep` + policy call + `_now()` + service call), `operation_id`s as
  pinned; register in `apps/api/app/api/v1/__init__.py`; providers +
  `EmployeeServiceDep` in `apps/api/app/dependencies.py`.
- Integration tests:
  - `tests/integration/test_employees_api.py` — AC1 (201 + exact
    `PINNED_EMPLOYEE_KEYS` body + initial period), AC2 (combined filters,
    pagination stability), `/me`, detail (current + history order), PATCH,
    transfer appends history (AC3), termination closes period + nulls reports.
  - `tests/integration/test_employees_failures.py` — AC4 409 email, AC5 400
    self-manager, AC6 409 archived target, plus `employee_manager_invalid`,
    `employee_code_taken`, `employee_already_terminated`,
    `employment_period_overlap`, `employee_transfer_effective_invalid`.
  - `tests/integration/test_employees_tenant_isolation.py` — AC7: tenant B
    get/patch/transfer/terminate of A's employee → all `404
    employee_not_found`.
  - `tests/integration/test_employees_roles.py` — AC8 (manager create → 403),
    manager list/detail scoping to reports, employee role `/me` only.
  - `tests/integration/test_employees_audit.py` — AC10: an audit row per
    mutation (`employee.created/updated/transferred/terminated` +
    `employee.manager_cleared`), and `changes` contains no name/email/phone
    values.
  - `tests/integration/test_employees_logs.py` — AC11: run create/update/
    transfer/terminate under `structlog.testing.capture_logs()` with synthetic
    PII fixtures; assert none of the values appear in captured events.
- `make gen-api` → regenerated `contracts/openapi/openapi.json` +
  `@workpulse/api-client` (drift test stays green; web compiles against the
  new hooks).

Verify: `make check` green (API + web against regenerated client).

### T6 — Replace `NullDepartmentUsage` (organizations Risk 1)

- `apps/api/app/infrastructure/postgres/employee_usage.py` —
  `PostgresEmployeeUsage` implementing the existing
  `domains/organizations/repository.py::DepartmentUsagePort`
  (`count_active_employees(tenant_id, department_id)` over `employments` ⋈
  `employees.status IN ('active','on_leave')` with open period). Infrastructure
  implements the shared port — no domain imports another domain's internals.
- `apps/api/app/dependencies.py`: wire it in place of `NullDepartmentUsage`
  (the null class stays as the unit-test fake).
- Integration test: archive a department that has an active employee →
  `409 department_archived_in_use`; archive succeeds again after termination.

Verify: `make check-api` green (organizations suite included).

### T7 — Web: employees list · detail · create/edit · transfer · termination

All HTTP via regenerated `@workpulse/api-client` hooks (no hand-written fetch):

- `apps/web/features/employees/` — `types.ts` (re-export generated wire
  types), `api.ts` (query keys + invalidation), `schema.ts` (zod form schemas),
  `hooks/use-employees.ts` (queries + mutations with toast/error mapping),
  `components/`: `EmployeesTable.tsx` (filter bar `q`/department/position/
  location/status/manager + loading/empty/error states),
  `EmployeeFormDialog.tsx` (create/edit, RHF+zod, server errors →
  `form.setError` per `ApiError.code`
  (`employee_email_taken` → email field, etc.) + visible `rootError`),
  `TransferDialog.tsx`, `TerminateDialog.tsx` (confirm-style),
  `EmploymentHistory.tsx` (periods, newest first, open period marked).
- Routes: `apps/web/app/(dashboard)/employees/page.tsx` (list),
  `app/(dashboard)/employees/[id]/page.tsx` (detail = profile + current
  employment + history + transfer/terminate actions, disabled for terminated);
  nav entry for `/employees` in the dashboard shell, role-gated to the read
  policy (manager direct reports, HR/Admin/Super Admin the tenant) so employee
  and finance sessions never mount the list queries; the create action is
  HR/Admin/Super Admin only. Pages compose features only (rule 8).
- Web tests `apps/web/tests/employees/` (Vitest + Testing Library, mocked
  api-client + sonner): table loading/empty/error; create dialog happy path +
  **409 duplicate email → visible error state** (AC12); transfer dialog happy
  path + archived-target 409 → visible error; terminate confirm.

Verify: `make check-web` green.

### T8 — E2E: Playwright in `tests/e2e/` behind `make check-e2e`

- `playwright.config.ts`: `testDir: './tests/e2e'`, `baseURL:
  'http://localhost:3000'`, `webServer` entries for the API (:8000) and web
  (:3000); delete the `tests/example.spec.ts` playwright.dev demo.
- `Makefile`: `check-e2e` (depends on `db-up` + `db-seed`; runs
  `npx playwright test`) — per `.ai/testing.md` decision 3 it does **not**
  join `make check` until it is fast and stable.
- `tests/e2e/employees.spec.ts` — one cross-surface flow against seeded
  organizations data: create employee (UI) → appears in list → open detail →
  transfer → new period visible in history; second create with a duplicate
  email → **visible 409 error state** (AC12). Deterministic synthetic data
  only (`.ai/testing.md` rule 6).

Built 2026-09-28, three deviations recorded:

1. **Data is run-scoped, not fixed** — employments are append-only and
   employees are never hard-deleted, so a fixed email makes the suite fail on
   re-run. Each run namespaces one synthetic employee with a short suffix
   (`e2e.avery.<run>@workpulse.test`); the flow and organization data it reads
   stay deterministic. Proven by running the suite twice against one database.
2. **`check-e2e` gained a `db-migrate` prerequisite** — the gate cannot be
   green on a database at revision `0001`; the new target applies
   `alembic upgrade head` before `db-seed` (which is also now part of the
   documented dev workflow).
3. **The e2e run surfaced a transport contract bug**: `customFetch` returned
   the raw JSON body while orval's generated response unions (and every
   consumer's `data.status === 200` check) expect `{data, status, headers}`.
   Vitest's mocked client had hidden it; the mutator now fulfils the generated
   type and its unit test asserts the wrapper. This also repairs the
   Organizations UI runtime, which was reading empty lists the same way.

Verify: `make check-e2e` green locally (db + both dev servers via webServer).

### T9 — Record

- `.ai/decisions/0008-employment-history-model.md` — ADR (see risks).
- This spec: `Status: done`, tick every acceptance criterion.
- `tasks/TODO.md`: move `employees.md` item, the `NullDepartmentUsage` item and
  the Playwright item to Done.
- `.ai/testing.md`: gates table E2E row → `make check-e2e` (real command).

Verify: `make check` green; docs consistent (a reviewer can follow the diff
alone).

### Test strategy

| Level | Tests (added/changed) | Gate | Failure cases covered |
| --- | --- | --- | --- |
| Unit | `tests/unit/employees/test_service_employees.py`, `test_policies.py` | `make check-api` | duplicate email/code, self-manager, invalid/terminated manager, archived target, terminated-cannot-transfer, effective-date rules, period overlap, double termination, manager-report nulling; denied role per mutating use case |
| Integration (repo/API) | `test_employees_{api,failures,tenant_isolation,roles,audit,logs}.py`, `test_employees_repository.py`, conftest `ALL_TABLES` | `make check-api` | AC4–AC8, AC10, AC11 as named above; tenant isolation expects 404 not 403 |
| Contract | `tests/contract/test_employee_events.py` (+ existing OpenAPI drift test via `make gen-api`) | `make check-api` | schema rejects unknown/PII keys; builders' outputs validate; routes exist with pinned shapes |
| Web | `apps/web/tests/employees/{employees-table,employee-form-dialog,transfer-dialog}.test.tsx` | `make check-web` | loading/empty/error states; 409 visible (duplicate email, archived target); toast on mutation failure |
| E2E | `tests/e2e/employees.spec.ts` | `make check-e2e` (joined `make check` 2026-09-29) | full list → detail → transfer flow; 409 rendered visibly end-to-end |

Cross-cutting: audit assertions on every mutating test group (rule 3);
`.ai/security.md` PII rules proven by `test_employees_audit.py` +
`test_employees_logs.py`; the event-payload whitelist in the contract test
proves the "no PII in events" invariant for when Phase 3 starts publishing.

### Risks · unknowns · ADR candidates

1. **Contract deltas invented at plan time** (T1) — one new error code
   (`employee_transfer_effective_invalid` 422), reuse of
   `transfer_target_archived` for create-with-archived reference, reuse of
   `employee_already_terminated` for transfer-of-terminated, rejection of
   future-dated transfers, PATCH allowed on terminated employees, and the
   manager non-team read → 404. All are behavior the spec left implicit;
   review at T1 before any code.
2. **`on_leave` is unreachable in Phase 1** — the enum exists but no endpoint
   sets it (Attendance owns those transitions later). The service models the
   lifecycle without shipping dead endpoints; flagged so Attendance reuses the
   state machine instead of inventing one.
3. **`employee_code` generation race** — two concurrent creates can pick the
   same code; the unique constraint maps the race to `409
   employee_code_taken`. Acceptable for Phase 1 (HR-driven create volume);
   a retry loop is a later refinement, not a schema change.
4. **Same-day repeated transfers** produce a zero-length closed period
   (half-open `[d,d)`) — accepted; the overlap invariant holds. Guarded by the
   `effective_from >= current.effective_from` rule.
5. **`NullDepartmentUsage` replacement changes organizations behavior** —
   archiving a department with active employees now 409s (previously a
   no-op success). Intended: `tasks/TODO.md` "Next" + organizations Risk 1.
6. **PII-in-logs test** is a regression guard: these flows log almost nothing,
   so it can pass trivially — it still fails the moment someone logs a value.
7. **E2E gate is not in `make check`** (`.ai/testing.md` decision 3 — flaky
   gates get ignored). It joins once stable; until then CI runs it as its own
   job. *(Resolved 2026-09-29: joined `make check` once fast + stable.)*
8. **ADR 0008 (recommended) — employment history model**: append-only
   `employments` periods (half-open) + denormalized current assignment refs on
   `employees` (for filtering). Expensive to reverse — payroll/analytics will
   read the periods directly. Write it in T9. The "event payload builders
   without a bus" pattern is cheap to reverse (Phase 3 swaps in the outbox) —
   recorded here, no separate ADR.