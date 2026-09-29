# End-to-end tests

Cross-surface flows: tests that exercise Next.js + FastAPI + PostgreSQL
together — the paths per-surface tests deliberately don't cover.

Run with `make check-e2e`: it brings PostgreSQL up, applies migrations and
seeds the dev database, then Playwright starts both dev servers
(`apps/api` on :8000, `apps/web` on :3000) and runs the suite in Chromium.
The suite stays out of `make check` until it is fast and stable enough to gate
every change (`.ai/testing.md` decision 3).

- `employees.spec.ts` — create → list → detail → transfer, and the duplicate
  email conflict rendered as a visible error state (employees AC12).
- `attendance.spec.ts` — check in → board counts and activity update → check
  out, and a stale second tab's duplicate check-in rendered as the visible 409
  error state (attendance AC12). A fresh employee is created per run via the
  API and impersonated by rewriting the dev-identity header (ADR 0004).
