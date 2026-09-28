# 8. Employment history model

- **Status:** Accepted
- **Date:** 2026-09-28

## Context

The employee record is what attendance, leave, payroll and analytics reference,
and the product question it must answer is "who works where, in what role, since
when". The employees spec (PO Decision #1) pinned append-only employment
periods; this ADR records the model that implements it, because payroll and
analytics will read the periods directly and reversing the shape later means
migrating tenant history. Two alternatives were rejected:

- **Overwrite the current assignment on the employee row.** Cheapest to read,
  but it destroys history — the exact question the product exists to answer.
- **Full bitemporal tables.** Maximum flexibility, far more machinery than
  Phase 1 needs; half-open periods cover the product's queries.

## Decision

- **`employments` is an append-only period log.** Each row is a half-open
  period `[effective_from, effective_to)`; `effective_to IS NULL` marks the one
  open period. A transfer closes the current period at `effective_from` and
  appends a new open one **in the same transaction**; a termination closes the
  open period at `effective_date`. Rows are never rewritten except closing
  `effective_to`; nothing is ever hard-deleted.
  - Invariants: no overlapping periods for one employee, enforced by the
    service rule `effective_from >= current.effective_from` (with
    `>= hired_at`, `<= today`) and backed by a partial unique index allowing at
    most one open period per employee.
- **`employees` carries the denormalized current assignment refs**
  (`department_id`, `position_id`, `location_id`, `manager_id`) for cheap list
  filtering and reads. The service updates them with the open period in one
  transaction; the open `employments` row remains the source of truth for
  history.
- **Status lifecycle lives on `employees`** (`active ⇄ on_leave → terminated`,
  `terminated` terminal). Termination also nulls `manager_id` on direct reports
  (one audit entry per report, PO Decision #4).
- **Events are built, not published.** `domains/employees/events.py` produces
  envelope-shaped payloads validated against the versioned schemas in
  `contracts/events/employees/`; Phase 3 wires them into the transactional
  outbox without a model change. Identifiers only — no names, emails or salary.

## Consequences

- Payroll and analytics can reconstruct any month's organization by reading the
  periods directly; **re-hire needs no migration** (Decision #5: append a new
  period to a terminated employee).
- Two representations of "current" (the employee row and the open period) must
  move together — every mutation goes through `EmployeeService`, and the
  invariant is covered by unit + integration tests.
- Same-day repeated transfers produce a zero-length closed period `[d, d)`.
  Accepted: the overlap invariant holds and the history stays honest.
- `employee_code` generation (`EMP-00001`, max suffix + 1 per tenant) can race
  under concurrent creates; the unique constraint maps the race to
  `409 employee_code_taken`. Acceptable at Phase 1 HR-driven volumes; a retry
  loop is a service refinement, not a schema change.
- The future Attendance feature reuses this state machine for `on_leave` rather
  than inventing a parallel one; `on_leave` has no endpoint in Phase 1.
