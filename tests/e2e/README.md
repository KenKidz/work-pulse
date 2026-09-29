# End-to-end tests

Cross-surface flows: tests that exercise Next.js + FastAPI + PostgreSQL
together — the paths per-surface tests deliberately don't cover.

Run with `make check-e2e`: it brings PostgreSQL up, applies migrations and
seeds the dev database, then Playwright starts both dev servers
(`apps/api` on :8000, `apps/web` on :3000) and runs the suite in Chromium.
The suite is part of `make check` — it earned its place on 2026-09-29 with
three consecutive green runs at ~20s each (`.ai/testing.md` decision 3).

- `organizations.spec.ts` — create a department + position → assign both to a
  new employee → the in-use department's archive renders the Risk-1
  `409 department_archived_in_use` as a visible error (organizations AC5/AC9) →
  after the employee transfers out, the archive/restore round-trip completes.
- `employees.spec.ts` — create → list → detail → transfer, and the duplicate
  email conflict rendered as a visible error state (employees AC12).
- `attendance.spec.ts` — check in → board counts and activity update → check
  out, and a stale second tab's duplicate check-in rendered as the visible 409
  error state (attendance AC12). A fresh employee is created per run via the
  API and impersonated by rewriting the dev-identity header (ADR 0004).
