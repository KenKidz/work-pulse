# 18. Payroll ↔ overtime seam and timesheet read ports

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

Payroll (`.ai/features/payroll.md`) consumes HR-verified overtime and reports
attendance/leave-derived timesheet facts, but it must not import the attendance,
leave or overtime domains' internals. ADR 0014 established the read-only seam
pattern: the consuming domain owns a protocol, and a PostgreSQL adapter in
`app/infrastructure/postgres/` performs the cross-table join. Payroll adds two
things that pattern has not covered:

1. A **write direction**: finalizing a run flips counted overtime rows to
   `INCLUDED_IN_PAYROLL` and records `included_in_run_id`. If this is not
   transactional and state-guarded, a row can be paid twice or never marked.
2. **Timesheet reads** (`worked_hours`, `attendance_days`, approved leave
   calendar days) that are attendance/leave data, not overtime data.

The overtime request/approval/verification workflow is its own spec
(`overtime.md`, built first — PO Decision 1). Payroll owns only the ports it
needs against that domain's eventual rows.

Alternatives considered:

- **Payroll imports overtime/attendance/leave repositories or tables.** Cross-
  domain internals import; breaks ADR 0014 and `.ai/architecture.md` rule 3.
  Rejected.
- **A shared "time facts" service owned by a fourth domain.** New domain for
  read joins, with no owner of the write flip; more surface than the seam needs.
  Rejected.
- **Flip overtime rows before/after the finalize transaction.** A crash between
  the two leaves a finalized payslip whose hours are still `HR_VERIFIED` (double
  pay risk) or included hours with no finalized run (lost pay). Rejected.
- **Sweep all verified rows at finalize by period.** A row verified after the
  calculation would be marked included without appearing on a payslip. Rejected
  in favour of flipping exactly the rows the calculation read.

## Decision

1. **Payroll owns the protocols; adapters live in infrastructure.**
   `OvertimeHoursPort` exposes `verified_hours(tenant_id, period)` and
   `mark_included(rows, run_id)`. `AttendanceHoursPort` exposes `worked_hours`
   and `attendance_days`; `LeaveDaysPort` exposes `approved_leave_days`. The
   payroll domain imports only these protocols. Postgres adapters in
   `app/infrastructure/postgres/` join the foreign tables, tenant-scoped first,
   and are wired through `app/dependencies.py` (ADR 0014 pattern).
2. **Verified-hours read is a row handle set, not just a number.**
   `verified_hours` returns the HR-verified overtime rows for the period —
   `(overtime_id, employee_id, hours)` — excluding rows already
   `INCLUDED_IN_PAYROLL`. Payroll aggregates hours per employee for the
   timesheet, but keeps the row handles for the finalize flip. A correction run
   therefore reads only hours not yet counted; a row included by an earlier
   finalized run can never be paid again.
3. **The write is an explicit, state-guarded flip in the finalize
   transaction.** `mark_included(rows, run_id)` updates exactly those rows
   `WHERE status = 'HR_VERIFIED'`, setting `INCLUDED_IN_PAYROLL` and
   `included_in_run_id = run_id`. It is idempotent for the same run (already
   included rows are a no-op) and never touches a row included by a different
   run. The payroll finalize (including the payslip flips and `payslip.issued`
   outbox rows) commits in the same database transaction: if the flip cannot
   mark every row the calculation consumed, finalize fails and rolls back
   rather than issuing a payslip for unmarked hours.
4. **At-most-one-finalized-run ownership.** Payroll's one-open-run-per-period
   constraint plus the overtime-side state guard enforce "each overtime row is
   counted by at most one `FINALIZED` run". The overtime side (P0) must prove it
   with a uniqueness/guard test; payroll adds the finalize integration test that
   a correction run pays only hours not already included.
5. **Timesheet reads are defined facts.**
   - `worked_hours` = Σ `(check_out − check_in)` over closed attendance records
     whose check-in date falls inside the period.
   - `attendance_days` = count of those closed records.
   - `approved_leave_days` = the number of distinct calendar days inside the
     period covered by `APPROVED` leave (overlapping approved ranges are a
     union, not a sum).

   All three are tenant-scoped and informational: they never alter base pay (no
   proration — PO Decision 5).
6. **No cross-domain events from the seam.** `mark_included` changes overtime
   state and its run reference only. Inclusion is a payroll accounting fact;
   payroll's finalize audit and `payslip.issued`/`payroll.calculated` events
   carry identifiers, period and counts — never amounts. The overtime domain
   may audit its own transitions, but payroll does not emit `overtime.*`.
7. **Test seam.** Unit tests use fake implementations of all three ports;
   integration tests exercise the Postgres adapters only once the overtime
   feature (P0) exists. Until then payroll's formula term is proven with fakes
   and real-hours e2e stays at `0.00`.

## Consequences

- Payroll is fully testable before overtime lands, and overtime can be built
  without importing payroll.
- The first write-direction seam is explicit and guarded; the state column and
  `included_in_run_id` are the durable proof of which run paid a row.
- A correction run naturally pays only newly verified overtime; previously
  included hours remain visible on the earlier (now superseded) payslip.
- If an overtime row is un-verified between calculation and finalize, finalize
  fails and rolls back; the run must be recalculated. No silent partial pay.
- Cross-context joins stay in infrastructure, tenant-scoped, and are covered by
  the owning feature's integration tests.
