# WorkPulse — Architecture

> How the system fits together and why. Expensive-to-reverse decisions live in
> `.ai/decisions/`. Conventions: `.ai/conventions.md`. Domain model:
> `.ai/domain.md`. Security: `.ai/security.md`.

## Mental model

```
PostgreSQL   = source of truth / transactions
Redis        = hot state / cache / realtime fan-out   (Phase 2)
Kafka        = durable event backbone                 (Phase 3)
ClickHouse   = analytical projection                  (Phase 4)
```

One fact is written once (PostgreSQL) and projected outward as versioned events.
Everything downstream — realtime state, analytics, notifications, audit — is
derivable from that stream.

| Store | Question it answers | Example |
| --- | --- | --- |
| PostgreSQL | transactional state | "Which team does Employee A belong to?" |
| ClickHouse | OLAP / aggregations | "Overtime rate by team, location and seniority over the last 6 months?" |
| Redis | what is true right now | "employee_183 = PRESENT since 09:01" |
| Kafka | what happened, in order, replayably | `attendance.checked_in` |

## Phase 1 — modular monolith (current)

```
apps/web (Next.js, App Router)
        │  HTTP /api/v1  (JSON)
        ▼
apps/api (FastAPI — domain-first modular monolith)
        │
        ▼
PostgreSQL (docker compose)
```

- No microservices yet. One deployable API, organized by domain
  (`apps/api/app/domains/<name>/`) so a future extraction is *possible* but not
  *required* (ADR 0002).
- Routers are thin: parse HTTP → call a domain service → return a schema.
- Domains own business logic; `infrastructure/` owns I/O; `shared/` owns
  plumbing (errors, logging, pagination, audit).
- Identity in Phase 1 is a **dev identity** (headers), but policies are written
  as if it were real — see `.ai/security.md`.

## Target — event-driven (Phase 3+)

```
                         NEXT.JS
                            │
                    REST / WebSocket
                            │
                         FASTAPI
                            │
             ┌──────────────┼───────────────┐
             ▼              ▼               ▼
        PostgreSQL        Redis      Object Storage
             │              │
          Outbox            │
             │              │
             ▼              │
           Kafka ◄──────────┘
             │
      ┌──────┼──────────────┬───────────────┐
      ▼      ▼              ▼               ▼
 Attendance Workflow   Notification     Analytics
 Consumer   Consumer      Worker          Worker
      │        │             │               │
      ▼        ▼             ▼               ▼
   Redis   PostgreSQL     Email/Slack    ClickHouse
                                             │
                                             ▼
                                        BI / Reports
```

### The flow that defines the product

```
Employee check-in
      ▼
   FastAPI ──► PostgreSQL (+ outbox row, same transaction)
      ▼
    Kafka
      ├──► Realtime consumer ──► Redis ("employee_183 = PRESENT")
      │                              ▼
      │                          WebSocket ──► Manager dashboard
      ├──► Workflow consumer ──► tardiness rules, approvals
      ├──► Notification worker ──► email / Slack / push
      └──► Analytics worker ──► ClickHouse
```

A single `attendance.checked_in` event simultaneously updates the live board,
notifies the manager, feeds the warehouse, runs tardiness rules, and leaves an
immutable audit record. That fan-out is the reason the architecture is
event-driven.

### Outbox, not dual writes

The API writes the row and the outbox event **in one PostgreSQL transaction**; a
relay publishes to Kafka. No "write to DB then publish and hope".

### Redis has a job, not a logo

Session/user cache · employee presence · online users · current attendance state
· API rate limiting · temporary approval state · distributed locks · realtime
fan-out. Redis Streams could replace Kafka for small loads; we keep Redis for hot
state and Kafka as the durable backbone so the separation stays honest.

### Later infrastructure

- **CDC (Phase 5):** `PostgreSQL ──► Debezium ──► Kafka ──► ClickHouse` —
  replaces hand-written analytics projections.
- **Observability (Phase 6):** `OpenTelemetry ──► Prometheus ──► Grafana`.

## Phase roadmap

`Phase N` == `VN` in the source brief (`tasks/doc.md` §11).

| Phase | Adds | Adopted because |
| --- | --- | --- |
| 1 | monorepo, Next.js, FastAPI, PostgreSQL — Organizations, Employees, Attendance | first features need a home and a source of truth |
| 2 | Redis + WebSocket, authN + RBAC | managers need a live board; approvals need identity |
| 3 | Kafka + workers + transactional outbox, Leave, Notifications | fan-out and approvals need decoupling and replay |
| 4 | ClickHouse + analytics engine, Payroll | aggregations must not scan OLTP; payslips need timesheets |
| 5 | CDC / Debezium | projections move from application code to the database |
| 6 | OpenTelemetry, Prometheus, Grafana | we need to see failures and latency across services |
| 7 | load testing, rate limiting, circuit breakers | reliability becomes a requirement, not a hope |
| 8 | Docker images, Kubernetes, CI/CD | deployment and operations become real problems |

Rule: a technology is added when WorkPulse has a requirement for it — with a spec
and an ADR — not because it looks good on a CV.

## Where things live

| Concern | Location |
| --- | --- |
| Product definition | `.ai/product.md` |
| Domain model, events, state machines | `.ai/domain.md` |
| Architecture + roadmap | `.ai/architecture.md` |
| Code conventions | `.ai/conventions.md` |
| Security & access control | `.ai/security.md` |
| Test strategy & gates | `.ai/testing.md` |
| Feature specs | `.ai/features/<feature>.md` |
| Decisions (ADRs) | `.ai/decisions/` |
| Web app | `apps/web/` |
| API app | `apps/api/` |
| Background workers | `workers/` (Phase 3) |
| Versioned contracts (OpenAPI, events) | `contracts/` |
| Database assets (seeds, scripts, migrations) | `database/` |
| Cross-surface tests | `tests/` |
| Verification entry point | `make check` |

## Tracing one feature

```
.ai/features/attendance.md
  └─► apps/api/app/domains/attendance/        (business logic)
  └─► apps/api/app/api/v1/attendance.py       (thin router)
  └─► contracts/events/attendance/            (versioned events)
  └─► workers/realtime/                       (Phase 2+)
  └─► apps/web/features/attendance/           (feature logic)
  └─► apps/web/app/(dashboard)/attendance/    (route)
  └─► apps/api/tests/ + tests/e2e/            (test flow)
```

An agent given "implement employee check-in" must be able to follow that chain
without searching the repository for what "attendance" means.