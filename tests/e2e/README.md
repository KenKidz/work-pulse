# End-to-end tests

Cross-surface flows: tests that exercise Next.js + FastAPI + PostgreSQL
together — the paths per-surface tests deliberately don't cover.

Run with `make check-e2e`: it brings PostgreSQL, Redis, Kafka
(`kafka-test`, :9093) and Kafka Connect (`kafka-connect-test`, :8084) up,
applies migrations and seeds the dev database, then `full-hop.sh` starts the
outbox relay, the realtime consumer, the payroll calculation worker and the
CDC sink — the full event hop (ADR 0013), the database-backed payroll job and
the Phase-5 Debezium projection — before Playwright starts both dev servers
(`apps/api` on :8000, `apps/web` on :3000) and runs the suite in Chromium.
The command-center live-delta test is measured through that hop: the bus is
the wall's only input. After the suite, the hop compares the serving (fold)
and CDC shadow projections through the production report repository and fails
on any aggregate drift; `reports.spec.ts` itself stays unchanged.
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
- `documents.spec.ts` — HR uploads a document through the two-step presigned
  flow on the seeded worker's employee detail (file → private MinIO staging →
  confirm → `document.uploaded` outbox row); the worker sees it under
  "My documents", downloads it through a fresh presigned GET that resolves to
  the private object, then HR deletes it and the row disappears from both
  surfaces while a direct download request answers `404 document_not_found`.
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
- `overtime.spec.ts` — the seeded worker submits a claim through `/overtime` →
  "My requests" shows it `requested`; a second same-date claim renders the
  pinned `409 overtime_overlaps` on the field. The manager approves from the
  manager inbox and the worker's still-open page follows it `manager_approved`
  through list polling (no reload); a decision dialog held open across a
  same-session API approve proves the pinned wrong-state
  `409 overtime_not_requested` message. HR verifies from the verification
  queue → the row shows `hr_verified` in HR's verified section and on the
  worker's open page; HR then un-verifies it (ADR 0018's finalize-rollback
  window) and a stale reject dialog renders `409 overtime_not_open`. The
  manager's own claim is listed without actions (self-decision is withheld)
  and a same-session API approve returns the pinned `forbidden` `403`; the
  claim is then withdrawn through the UI.
- `payroll.spec.ts` — the seeded finance user configures payroll, adds salary
  records for the worker and manager, seeds one `hr_verified` overtime claim
  for the run's month through the real worker → manager → HR API chain, starts
  a closed-month run, watches the worker move it `queued → calculated`, sees
  the claim's hours in the run timesheet's `overtime_hours`, reviews the
  provisional payslip lines (net extended by the overtime pay) and finalizes,
  which flips the claim to `included_in_payroll` with the run handle; the
  worker then logs in, sees only their own issued payslip in "My payslips"
  (with the expected net), a foreign payslip id renders the not-found state,
  and `/overtime` shows the claim `included_in_payroll`. `full-hop.sh` resets
  the tenant's payroll ledger and the seeded overtime rows first and runs the
  payroll worker alongside the relay/consumer so the transition is real, not a
  test double. The spec invokes the same reset scripts before its finance test
  so a Playwright retry starts clean too.
- `performance.spec.ts` — the manager starts an ad-hoc review for the seeded
  worker and nominates the deterministic peer created by
  `reset_performance_fixtures.py`; the draft is invisible on the worker's
  "My reviews" and the Employee surface has no review/catalog management.
  Completing without a rating renders the pinned `409 review_incomplete`
  sentence; the manager adds the rating and text, the peer files one comment
  from "Feedback requested" and a second submission renders the pinned
  `409 feedback_already_submitted` copy with the typed text kept. After the
  manager completes it, the worker's "My reviews" shows the rating and sections
  and never the peer's feedback text.

`full-hop.sh` resets only the seeded worker's leave requests/balances,
notification rows, the seeded tenant's payroll ledger, the seeded
worker's/manager's overtime rows, the seeded tenant's performance rows (plus the
deterministic peer employee/login the feedback hop nominates) and produces the
reports projection fixtures before Playwright starts: an approved leave
request is terminal (there is no cancel/delete API) and repeats would otherwise
hit `409 leave_overlaps` on the wall date the live assertion needs; the inbox
spec asserts an exact badge count, so it starts from three unread fixture rows;
the payroll spec asserts exact net amounts, so it starts from an empty payroll
ledger; approved/verified overtime claims are terminal too, so the overtime
and seam fixtures need a free work date and exact countable hours; and
performance reviews/feedback/catalog names are mutable across runs. Leave types
stay — the service provisions them per tenant.

The reports fixture is special after T7: `reset_reports_fixtures` writes the
People Ops fixture as **both** the synthetic envelopes the fold worker consumes
and the matching OLTP rows Debezium captures (`tests/e2e/full-hop.sh`), with the
shadow projection truncated first and a bounded wait for the annual leave-type
row before the sink starts. The post-suite parity step then proves the two
independent projections agree for the fixture employee; recorded real Debezium
messages live under `apps/api/tests/contract/fixtures/cdc/recorded/`.
