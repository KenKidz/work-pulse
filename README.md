# WorkPulse

Real-time workforce operations & intelligence platform — every workforce fact
(check-in, leave, overtime, transfer, payroll run) is an event that is visible
live, auditable and analysable.

> Phase 1: monorepo scaffold — Next.js + FastAPI + PostgreSQL.
> Redis, Kafka, ClickHouse and friends arrive in later phases, each with its own
> spec and ADR. Roadmap: `.ai/architecture.md`. Original brief: `tasks/doc.md`.

## Quickstart

Prerequisites: Node >= 20, [pnpm](https://pnpm.io/), [uv](https://docs.astral.sh/uv/), Docker.

```sh
make install     # API (uv) + web (pnpm workspaces) dependencies
make db-up       # PostgreSQL on :5432
make dev-api     # FastAPI on http://localhost:8000   (separate terminal)
make dev-web     # Next.js on http://localhost:3000   (separate terminal)
make gen-api     # regenerate OpenAPI + typed API client
make check       # all verification gates
```

## Repo map

```
.ai/                  product · domain · architecture · conventions · security ·
                      testing · decisions/ (ADRs) · features/ (specs + backlog)
apps/web/             Next.js app (routes in app/, feature logic in features/)
apps/api/             FastAPI app (routers in app/api/v1/, logic in app/domains/)
packages/api-client/  orval-generated typed client (`make gen-api`)
contracts/            versioned event schemas + generated OpenAPI
database/postgres/    seeds and scripts
tests/e2e/            cross-surface tests
tasks/                TODO.md (backlog) · doc.md (original brief)
docs/                 appears when human-facing docs outgrow .ai/
```

## Verification

| Gate | Command | Covers |
| --- | --- | --- |
| all | `make check` | both gates below |
| API | `make check-api` | pytest (`apps/api/tests/`) |
| Web | `make check-web` | `tsc --noEmit` + ESLint + Vitest (`apps/web/`) |

Test levels, mandatory rules and future gates: `.ai/testing.md`.

## What is being built now

Phase 1 slice, in dependency order — each has a draft spec awaiting decisions:

1. **Organizations** — departments, positions, locations (`.ai/features/organizations.md`)
2. **Employees** — records, employment history, transfers (`.ai/features/employees.md`)
3. **Attendance** — check-in/out, lateness, live workforce board (`.ai/features/attendance.md`)

## Contributing (humans and agents)

Read `AGENTS.md` first. Workflow: **spec → plan → build → verify → record**.

| Step | Where |
| --- | --- |
| Spec | `.ai/features/<feature>.md` (`/spec`) |
| Plan | `## Plan` section of the spec (`/plan`) |
| Verify | `/check` → `make check` |
| Record | feature spec status, `tasks/TODO.md`, `.ai/decisions/` ADR |

## Phases

1. monorepo + Next.js + FastAPI + PostgreSQL — Organizations, Employees, Attendance — **current**
2. Redis + WebSocket (live board) · authN + RBAC
3. Kafka + transactional outbox + workers · Leave · Notifications
4. ClickHouse analytics · Payroll (simulated) · Recruitment · Performance
5. CDC / Debezium
6. OpenTelemetry, Prometheus, Grafana
7. Load testing, rate limiting, circuit breakers
8. Docker images, Kubernetes, CI/CD