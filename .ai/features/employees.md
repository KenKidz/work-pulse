# Feature: Employees

- **Status:** draft
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
- Authentication and real user accounts (Phase 2) — Phase 1 uses the dev identity.
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

## API contract (proposed)

| Method & path | Input | Response |
| --- | --- | --- |
| `GET /api/v1/employees` | `?q=&department_id=&position_id=&location_id=&status=&manager_id=&page=&page_size=` | `200 {"items": EmployeeSummary[], "page": Page}` |
| `POST /api/v1/employees` | `CreateEmployee` | `201 Employee` |
| `GET /api/v1/employees/me` | — | `200 Employee` |
| `GET /api/v1/employees/{id}` | — | `200 EmployeeDetail` (current employment + history) · `404` |
| `PATCH /api/v1/employees/{id}` | `UpdateEmployee` (profile fields) | `200 Employee` |
| `POST /api/v1/employees/{id}/transfers` | `{"department_id", "position_id"?, "location_id"?, "manager_id"?, "effective_from", "reason"?}` | `200 Employment` |
| `POST /api/v1/employees/{id}/termination` | `{"effective_date", "reason"}` | `200 Employee` |
| `GET /api/v1/employees/{id}/employments` | — | `200 Employment[]` |

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
```

Error codes: `employee_not_found` · `employee_email_taken` ·
`employee_code_taken` · `employee_manager_invalid` · `employee_self_manager` ·
`employee_already_terminated` · `employment_period_overlap` ·
`transfer_target_archived` · `department_not_found` · `position_not_found` ·
`location_not_found`.

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

- [ ] API: `POST /employees` with valid references returns `201` and persists an initial employment period.
- [ ] API: `GET /employees` filters by department/status and paginates.
- [ ] API: a transfer appends a new employment period and closes the previous one (history is intact).
- [ ] API (failure): duplicate email → `409 employee_email_taken`.
- [ ] API (failure): self-manager → `400 employee_self_manager`.
- [ ] API (failure): transfer to an archived department → `409 transfer_target_archived`.
- [ ] API (tenant isolation): cross-tenant read/update → `404`.
- [ ] API (denied role): a `Manager`-role create request → `403`.
- [ ] Contracts: the four event schemas exist under `contracts/events/employees/` and validate the payloads the service produces.
- [ ] Audit: create / update / transfer / terminate each wrote an audit entry with no PII values.
- [ ] Logs: no name, email or phone appears in any log line produced by these flows.
- [ ] Web: list → detail → transfer flow works; a `409` shows a visible error state.

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
7. Identity in Phase 1 is the **dev identity** (ADR 0004), so `/employees/me`
   resolves from `X-Actor-Id`; role checks are real code, not placeholders.

## Plan

_Not planned yet._