# Feature specs & backlog

One file per feature, named after the feature: `employees.md`, `attendance.md`,
… — created from `_template.md`, usually via `/spec`.

A spec is a contract: the code implements the spec, the spec is not retrofitted
to the code. If reality diverges, update the spec in the same change and say why.

## Lifecycle

| Status | Meaning |
| --- | --- |
| `draft` | written, no code yet. Once its `## Decisions` are resolved it is ready for `/plan`, which sets `approved` |
| `approved` | decisions answered, plan exists; ready to implement |
| `implementing` | in progress; plan tasks are being ticked |
| `done` | acceptance criteria verified with `make check` |
| `dropped` | abandoned; write why at the top |

## Conventions

- Acceptance criteria are testable statements ("Given …, when …, then …").
- Full-stack specs pin the API contract before the acceptance criteria.
- The implementation plan lives in the same file under `## Plan` (see `/plan`).
- Update the status line in the same change that moves the status.
- Open product questions go in `## Decisions needed` with a proposed default —
  never silently resolved by the implementer. Once answered, the section becomes
  `## Decisions (PO, <date>)` holding the outcome and the rationale.

## Backlog index

Sequencing follows the phase roadmap in `.ai/architecture.md`. Specs for later
phases are written when that phase starts (`/spec <feature>`), not before — the
domain map in `.ai/domain.md` is their placeholder.

| Feature | Phase | Size | Status | Spec |
| --- | --- | --- | --- | --- |
| Organizations (departments, positions, locations) | 1 | S | done | `organizations.md` |
| Employees (records, history, transfers) | 1 | M | done | `employees.md` |
| Attendance (check-in/out, lateness, live board) | 1 | M | done | `attendance.md` |
| Identity & Access (authN + RBAC) | 2 | M | done | `identity-access.md` |
| Realtime Command Center (WebSocket board) | 2 | M | done | `realtime-command-center.md` |
| Leave (requests, approvals, balances, calendar) | 3 | M | done | `leave.md` |
| Notifications (email/Slack delivery) | 3 | M | done | `notifications.md` |
| Notification inbox (bell, read state) | 3 | M | done | `notification-inbox.md` |
| Event backbone — transactional outbox + relay (Kafka) | 3 | L | done | `outbox-relay.md` |
| Realtime consumer swap (events → wall deltas, ADR 0011) | 3 | M | done | `realtime-consumer.md` |
| Payroll (simulated runs, timesheets, payslips) | 4 | L | done | `payroll.md` |
| Overtime (requests, manager approval, HR verification) | 4 | M | done | `overtime.md` |
| Analytics & Reports (ClickHouse projections) | 4 | L | done | `analytics-reports.md` |
| Recruitment (jobs, candidates, interviews) | 4+ | L | done | `recruitment.md` |
| Internationalization (en + vi, next-intl) | cross-cutting | L | done | `i18n.md` |
| Performance (goals, reviews, feedback, skills) | 4+ | M | done | `performance.md` |
| Employee documents (presigned upload/download) | 4+ | M | done | `employee-documents.md` |
| Change Data Capture (Debezium projection pipeline) | 5 | L | done | `change-data-capture.md` |
| Observability (OTel, Prometheus, Grafana) | 6 | M | done | `observability.md` |
| Reliability (load tests, rate limiting, breakers) | 7 | M | draft | `reliability.md` |
| Deployment (images, Kubernetes, CI/CD) | 8 | M | planned | — |

Build order inside Phase 1: **Organizations → Employees → Attendance** (each
depends on the previous one's reference data).

All three Phase 1 specs have their `## Decisions` resolved (2026-09-24, ADR 0003
+ ADR 0004) and are built end-to-end (`done`); `make check-e2e` covers the
Employees and Attendance flows.