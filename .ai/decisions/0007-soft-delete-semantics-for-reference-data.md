# 7. Soft-delete semantics for reference data

- **Status:** Accepted
- **Date:** 2026-09-24

## Context

Organizations (departments, positions, locations) is the first feature with
rows other data references: employees and attendance records will point at
departments, positions and locations, and reporting must never dangle. The
feature spec proposed soft deletion via `archived_at` and PO Decisions #1/#5
confirmed archive-blocks-while-in-use and restore. One property was left as an
explicit ADR candidate (spec Risk 2) because it is expensive to reverse once
tenant data exists: whether uniqueness of `name`/`title` per tenant includes
archived rows.

## Decision

- **Soft delete via `archived_at`** on all three collections. No DELETE routes
  exist; archive and restore are the only lifecycle transitions.
- **Uniqueness spans ALL rows, archived included**: DB unique constraints
  `(tenant_id, name)` / `(tenant_id, title)` (migration `0001`). An archived
  row keeps its name forever, so **restore can never conflict** and history
  keeps its labels.
- **Archived items stay readable** (`GET /{id}` and `include_archived=true`
  listings) but are hidden from default listings and create/update pickers.
- **Archiving a department with active employees is blocked**
  (`409 department_archived_in_use`, PO Decision #1). The check sits behind
  `DepartmentUsagePort`; Organizations ships `NullDepartmentUsage` (no
  employees table exists yet) and Employees must swap in the real count plus
  the integration test.
- **Archive/restore no-ops** (archive-of-archived, restore-of-active) return
  `200` without an audit entry; only real transitions are audited.

## Consequences

- Name reuse requires renaming the archived row first — an explicit, audited
  act instead of a silent collision. If the PO ever wants name reuse, the
  change is a partial unique index plus a restore-conflict error code:
  a migration **and** a contract change.
- Reference integrity is total: any id an employee or attendance row holds
  remains resolvable forever; `archived_at` only affects pickers and default
  listings.
- The usage-port seam keeps the archive rule testable now (unit + fake) and
  real later (Employees integration test) without touching the contract.
- Every real transition writes an append-only audit entry, so the full
  lifecycle of reference data is reconstructable.
