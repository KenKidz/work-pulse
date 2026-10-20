# 14. Leave presence semantics and the coverage seam

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

Leave (`.ai/features/leave.md`, T5) makes approved leave visible on the live
board. `on_leave` was pre-declared in the row-status vocabularies
(`contracts/realtime/board.v1.json`, the attendance/realtime schemas) but
hardcoded to `0`; attendance Decision 7 accepted that `absent` over-counts
until leave data exists. Three choices are expensive to reverse once the hop
runs:

1. **What wins** when an employee has both an APPROVED leave range and a
   same-day attendance record for a board date. Check-in is blocked from now
   on, but Phase 1/2 records can pre-date an approval, and a same-day sequence
   (record, then approval, or approval racing a capture) is possible.
2. **How attendance learns coverage** without importing leave internals.
   Domain code never imports another domain's internals; attendance reads
   employee facts through its own port (`EmployeeContext`), implemented by its
   own adapter joining `employees`/`locations`.
3. **What the realtime consumer does with `leave.*` events** (T7). The
   consumer is the wall's only input (ADR 0013) and is stateless: it sees one
   envelope and nothing else — no database, no record state.

Alternatives considered:

- **Record wins over leave.** A same-day record would keep the employee
  `present`/`checked_out` on a day they are on approved leave — and the
  stateless consumer, which never sees record state, could not reproduce that
  row: deltas and the REST snapshot would disagree. Rejected.
- **`AttendanceService` imports the leave service/repository directly.**
  Cross-domain internals import; breaks the adapter precedent and the
  "never another domain's internals" rule. Rejected.
- **Consumer emits an on-leave fact for every `leave.approved` regardless of
  dates.** A future-dated approval would mark the employee on leave *today* —
  a wrong status pushed to every wall. Rejected.
- **Consumer reads PostgreSQL at consume time to check coverage.** The event
  stops carrying the fact and the consumer gains a database dependency
  (rejected already in ADR 0013, decision D1: DB enrichment at the consumer).
  Rejected again here for the windowed mapping.

## Decision

1. **`on_leave` wins over a same-day record.** For a board date covered by an
   APPROVED leave range, the employee renders exactly one presence row:
   `status="on_leave"`, `late=false`, `minutes_late=null`, `since=null`,
   `attendance_id=null`. Any same-day record is excluded from the
   record-based counts (`present`/`remote`/`late`/`checked_out`) and from
   `absent`, but stays in the attendance history (`GET /attendance/records`)
   and its transitions stay in the activity feed — a check-in that happened,
   happened. `ON_LEAVE` and `PRESENT` are contradictory presence states
   (leave spec decision 9); with this rule the stateless consumer — which
   never sees records — can produce exactly the row the REST snapshot does.
2. **Coverage seam: attendance owns the port, the attendance adapter joins
   the table.** `AttendanceRepository` gains
   `leave_coverage(tenant_id, workday) -> set[UUID]` (board) and
   `covers_leave(tenant_id, employee_id, workday) -> bool` (check-in); the
   Postgres attendance adapter implements both by joining `leave_requests`
   (`status='approved'`, `start_date <= workday <= end_date`, tenant-scoped
   first). The    attendance domain knows only the protocol — no leave internals
   are imported by domain code — mirroring the `EmployeeContext` precedent.
   Leave's own repository is untouched. Migration 0007 adds
   `ix_leave_requests_approved_coverage` (partial on `status = 'approved'`,
   `(tenant_id, end_date, start_date)` `INCLUDE (employee_id)`), so the board
   read is a bounded index-only range scan — keyed on the date range that can
   still be running — rather than a walk over the tenant's leave history. The
   check-in read fits the existing `(tenant, employee, status, start_date)`
   index and needs no new one.
3. **One `on_leave` row per in-scope covered employee;**
   `counts.on_leave = len(on_leave rows)`. `board_detail` adds a row for every
   *visible* covered employee — with or without an attendance record, and
   without consulting employee status (the consumer cannot know either, so
   the snapshot must not disagree with a delta). This keeps the pinned
   `counts == recount(rows)` invariant and means a consumer upsert for a
   record-less employee survives the next snapshot refresh. Coverage is read
   tenant-wide in one query and intersected with the board scope in the
   service (a manager sees on-leave rows only for self + direct reports).
4. **Check-in is blocked on covered workdays; check-out stays allowed.**
   `check_in` raises `409 employee_on_leave` immediately after the
   `employee_not_active` check (symmetric with it, same workday computed in
   the location timezone). Closing an open record is always safe: an employee
   can be approved while their record is open, and a capture that never
   closes is worse than a close on a leave day. That later check-out is still
   forwarded for the activity rail, but the connection's `on_leave` row and
   counts are preserved — an attendance transition never replaces the leave
   row while the approval covers the workday.
5. **Windowed `leave.approved` mapping (T7).** The consumer maps
   `leave.approved` to an on-leave fact **only when the envelope's
   `[start_date, end_date]` covers the consume date** (else `None`, like
   every filtered event). `leave.requested`/`rejected`/`withdrawn` are
   wall-silent: `PENDING` never reaches the wall, and rejection/withdrawal
   change no wall state.
6. **Additive enum extensions, no version bump.** `app/shared/presence.py`:
   `PresenceStatus` += `"on_leave"`, `PresenceReason` += `"leave_approved"`,
   and `PresenceFact.attendance_id`/`since` become optional for the leave
   fact (`late=false`, `minutes_late=null`, `workday` = consume date).
   `contracts/realtime/board.v1.json`: the `presence-upsert-frame.reason`
   enum additively gains `"leave_approved"` — the same convention as
   ADR 0013's payload extension. No other wire shape changes: the row-status
   enum already contained `on_leave`, and the counts key set is unchanged.

## Consequences

- Attendance Decision 7's `absent` over-count is closed: approved leave moves
  employees out of `absent` and into `on_leave` in the one `board_detail`
  path, so the REST board, the realtime snapshot and (through T7) the
  consumer's frames agree by construction.
- Board reads gain one tenant-scoped indexed range query per call — a single
  set read, never a per-row check.
- **Self-heal at range/day boundaries (accepted).** A wall fact only fires at
  approval time. When an approved range ends (or the workday rolls over), no
  event fires; the on-leave row heals on the next ~60s snapshot refresh or
  reconnect resync (ADR 0011 transient delivery — the pinned recovery path).
  A leave approved while a viewer is disconnected behaves the same. v1
  accepts this; a scheduled re-projection is a later phase.
- Employees on approved leave are excluded from
  `present`/`remote`/`late`/`checked_out` even when a same-day record exists;
  the record remains in the records list and the activity feed still lists
  its transitions.
- `employee_on_leave` becomes a stable `409` code on the attendance surface
  (next to `employee_not_active`); the leave spec pins both in its shared
  conflict-code list.
- The coverage read is deliberately *not* status-filtered: a terminated
  employee with an approved range covering the date still renders
  `on_leave`, matching what the consumer's windowed mapping would push.
- **Rollout order:** deploy API readers before the realtime worker. The
  pre-leave `PresenceFact` model rejects `leave_approved` and the null
  `attendance_id`/`since` of a leave fact, so a worker-first rolling deploy
  would make old API pods fail while decoding. Readers from this change skip
  unknown facts (the subscription survives), but pods that have not received
  the reader change still require the API-first order.
