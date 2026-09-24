# Feature: Organizations

- **Status:** draft
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
- All three collections are **soft-deleted** via `archived_at` (proposed):
  employees and attendance records keep referencing them, so history must
  survive deletion.
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
| E2E | `tests/e2e/` |

## Roles & access

| Capability | Employee | Manager | HR | Admin |
| --- | --- | --- | --- | --- |
| Read departments/positions/locations | Y | Y | Y | Y |
| Create / update / archive | — | — | Y | Y |

## API contract (proposed)

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

## Events

None in Phase 1. Organization reference data changes are **audited**, not
broadcast. Revisit if a downstream consumer (analytics dimension tables) needs
them in Phase 4.

## Edge cases

- Create a department whose parent is itself or a descendant → `400 department_cycle`.
- Duplicate name in the same tenant → `409 department_name_taken`.
- Archive a department that still has active employees → `409 department_archived_in_use` (proposed: block).
- Tenant A requests tenant B's id → `404` (never `403` — no existence leak).
- Invalid/unknown IANA timezone → `422 invalid_timezone`.
- `page_size` above the maximum → clamped, not an error.

## Acceptance criteria

- [ ] API: `POST /departments` with a valid payload returns `201` and the created resource.
- [ ] API: `GET /departments` paginates and filters by `q` / `parent_id`.
- [ ] API (failure): duplicate department name in one tenant → `409 department_name_taken`.
- [ ] API (failure): cycle in the department tree → `400 department_cycle`.
- [ ] API (failure): archiving a department with active employees → `409 department_archived_in_use`.
- [ ] API (tenant isolation): tenant B reading tenant A's department → `404 department_not_found`.
- [ ] API (denied role): an `Employee`-role request to `POST /departments` → `403`.
- [ ] Audit: every create/update/archive produced an audit entry.
- [ ] Web: the organization page lists, creates, edits and archives a department; a `409` renders as a visible error state.
- [ ] Migrations: schema is created by Alembic, not by hand.

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

_Not planned yet._