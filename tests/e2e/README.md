# End-to-end tests

Cross-surface flows: tests that exercise Next.js + FastAPI + PostgreSQL
together — the paths per-surface tests deliberately don't cover.

Run with `make check-e2e`: it brings PostgreSQL up, applies migrations and
seeds the dev database, then Playwright starts both dev servers
(`apps/api` on :8000, `apps/web` on :3000) and runs the suite in Chromium.
The suite is part of `make check` — it earned its place on 2026-09-29 with
three consecutive green runs at ~20s each (`.ai/testing.md` decision 3).
Run one `make check-e2e` at a time per dev database: the specs share the
seeded users, and `attendance.spec.ts` races on the one-open-record invariant
if two runs overlap (Playwright's `workers: 1` serializes within a run only).

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
