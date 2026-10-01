# 9. Attendance Phase-1 semantics

- **Status:** Accepted
- **Date:** 2026-09-29

## Context

Attendance is the feature that turns a workforce event into a durable record, a
presence state and a lateness judgement — and the input the realtime (Phase 2)
and analytics (Phase 4) phases consume. The API contract was pinned in
`.ai/features/attendance.md` (T1, 2026-09-28). This ADR records the semantics
behind it: they are expensive to reverse because the web, the versioned event
contracts and every later projection depend on them.

Alternatives considered:

- **Mutually exclusive board buckets.** Intuitive ("one person, one bucket"),
  but `late` is orthogonal to presence and `remote` is a work mode; any
  partition needs precedence rules the domain does not have.
- **A tenant-level timezone for the board date.** There is no `tenants` table in
  Phase 1; a per-tenant default timezone is a Phase 2 settings candidate with
  its own ADR.
- **Per-employee shifts / rosters.** The eventual model for scheduling; deferred
  to Phase 2–3 — not needed to make lateness real.

## Decision

- **The workday belongs to the check-in's location timezone.** `workday` = the
  calendar date of `checked_in_at` in `employees.location_id →
  locations.timezone` (`zoneinfo`). A record keeps its workday across midnight;
  check-out never recomputes it. Server time is authoritative — no client
  timestamp is accepted anywhere (Decision #3).
- **Lateness has an exclusive boundary.** The shift resolves as the employee's
  location's shift, else the tenant default (`location_id IS NULL`), else none →
  never late. `late = checked_in_at > shift.start_time + grace_minutes`, on the
  recorded workday in the location timezone; exactly at `start + grace` is not
  late. `minutes_late` (event payload only) is floor whole minutes past the
  boundary. The flag is set once at check-in and never cleared.
- **At most one shift per scope per tenant** — one per `location_id`, plus at
  most one tenant default. Enforced by partial unique indexes (a second one is
  `409 shift_scope_taken`). Shifts are reference data: updated, never deleted; a
  client-supplied `location_id` outside the tenant is `404 location_not_found`;
  an archived location remains resolvable so lateness keeps working.
- **Board counts overlap — they are not a partition.** A late employee appears
  in `present`/`remote` **and** `late`; each count takes one contribution per
  employee from their current (newest) record for the date, so a re-check-in
  replaces its earlier record's contribution instead of counting twice (the
  Phase-2 realtime projection recounts over exactly these rows); `on_leave` is
  always `0` until Leave lands (the field stays so the contract never changes).
  `absent` counts active visible employees past `start + grace` with no record
  at all for the date, and over-counts rest days until rosters/Leave exist
  (Decision #7 — the board UI says so).
- **The board date defaults to the server's UTC date.** `?date=` is optional;
  the web always computes and sends an explicit date. There is no tenant-level
  timezone to read a "tenant today" from.
- **At most one open record per employee, enforced in the database.** A partial
  unique index on `(tenant_id, employee_id) WHERE checked_out_at IS NULL` backs
  the service pre-check: two concurrent check-ins (two tabs) produce exactly one
  record and one `409 attendance_already_checked_in`.
- **Terminated employees cannot check in or out** (`409 employee_not_active`).
  A record left open at termination stays open and keeps counting until Phase 3
  corrections (or an auto-close hook on terminate) resolve it — accepted for
  Phase 1.
- **Notes are PII: stored, never echoed.** `check_in_note`/`check_out_note` live
  only on the record; logs, event payloads and audit `changes` carry ids and
  flags only — note values never appear.

## Consequences

- Events (`attendance.checked_in` / `.checked_out` / `.late`) are built from
  these facts and validated against the versioned contracts now, published in
  Phase 3 (outbox). Their payloads already carry `workday`, `late` and
  `duration_minutes`, so projections never need a model change to replay.
- Phase 2's PresenceState (Redis) recomputes the board from the same record
  rules; until then the board is request/response and clients may poll.
- Relaxing shift scoping to real rosters is a scheduling change — new tables,
  same resolution order (employee → location → tenant default).
- Board query cost grows with the active-employee count (per-employee timezone
  math). Accepted for Phase 1; do not pre-optimize.
