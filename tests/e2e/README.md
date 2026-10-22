# End-to-end tests

Cross-surface flows: tests that exercise Next.js + FastAPI + PostgreSQL
together — the paths per-surface tests deliberately don't cover.

Run with `make check-e2e`: it brings PostgreSQL, Redis and Kafka
(`kafka-test`, :9093) up, applies migrations and seeds the dev database,
then `full-hop.sh` starts the outbox relay, the realtime consumer and the
payroll calculation worker — the full event hop (ADR 0013) plus the
database-backed payroll job — before Playwright starts both dev servers
(`apps/api` on :8000, `apps/web` on :3000) and runs the suite in Chromium.
The command-center live-delta test is measured through that hop: the bus is
the wall's only input.
The suite is part of `make check` — it earned its place on 2026-09-29 with
three consecutive green runs at ~20s each (`.ai/testing.md` decision 3).
Run one `make check-e2e` at a time per dev database: the specs share the
seeded users, and `attendance.spec.ts` races on the one-open-record invariant
if two runs overlap (Playwright's `workers: 1` serializes within a run only).
The hop runner also refuses to start while a `make dev-relay` /
`make dev-realtime` / `make dev-payroll` worker is running, or while the dev
`event_outbox` has unpublished rows: both would race the gate's own
relay/consumer/job loop and make the <2s live-delta assertion measure the
wrong thing. Drain the outbox with `make dev-relay` and stop the dev workers
before running the gate.

- `organizations.spec.ts` — create a department + position → assign both to a
  new employee → the in-use department's archive renders the Risk-1
  `409 department_archived_in_use` as a visible error (organizations AC5/AC9) →
  after the employee transfers out, the archive/restore round-trip completes.
- `employees.spec.ts` — create → list → detail → transfer, and the duplicate
  email conflict rendered as a visible error state (employees AC12).
- `attendance.spec.ts` — check in → the open record and its row appear →
  check out, and a stale second tab's duplicate check-in rendered as the
  visible 409 error state (attendance AC12). The browser logs in as the seeded
  worker user (linked to the synthetic seeded employee) — real sessions
  replaced the dev-identity headers in the Phase 2 swap (ADR 0004).
- `leave.spec.ts` — the worker submits an unpaid request covering the wall's
  UTC workday → "My requests" shows it PENDING, and the same form renders the
  `409 leave_overlaps` for a second overlapping range; the manager approves it
  from the team inbox while a second page watches the wall, which flips the
  worker to `on_leave` and out of `absent` within 2s without a reload (leave
  T9 / ADR 0014).
- `notification-inbox.spec.ts` — the seeded worker's header bell shows the
  seeded unread count → the dropdown lists the recent rows and links to
  `/notifications` → marking one read drops the badge by one, and mark-all
  takes it to zero with every row showing its read state, without a reload.
  Rows come from `full-hop.sh`'s notification fixture reset; the notification
  worker stays out of the hop (its delivery contract is proven at the worker
  level).
- `payroll.spec.ts` — the seeded finance user configures payroll, adds salary
  records for the worker and manager, starts a closed-month run, watches the
  worker move it `queued → calculated`, reviews the provisional payslip lines
  and finalizes; the worker then logs in, sees only their own issued payslip in
  "My payslips" (with the expected net) and a foreign payslip id renders the
  not-found state. `full-hop.sh` resets the tenant's payroll ledger first and
  runs the payroll worker alongside the relay/consumer so the transition is
  real, not a test double. The spec invokes the same reset script before its
  finance test so a Playwright retry starts clean too.

`full-hop.sh` resets only the seeded worker's leave requests/balances,
notification rows and the seeded tenant's payroll ledger before Playwright
starts: an approved leave request is terminal (there is no cancel/delete API)
and repeats would otherwise hit `409 leave_overlaps` on the wall date the live
assertion needs; the inbox spec asserts an exact badge count, so it starts from
three unread fixture rows; the payroll spec asserts exact net amounts, so it
starts from an empty payroll ledger. Leave types stay — the service provisions
them per tenant.
