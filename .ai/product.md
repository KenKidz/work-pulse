# WorkPulse — Product

> Source of truth for **what** we build. Original brief: `tasks/doc.md`.
> Domain model + ubiquitous language: `.ai/domain.md` · Access & data protection:
> `.ai/security.md` · System shape + roadmap: `.ai/architecture.md` ·
> Test strategy: `.ai/testing.md` · Features: `.ai/features/<feature>.md`.

## Vision

WorkPulse is a **real-time workforce operations & intelligence platform** — not a
CRUD HR system. Every workforce fact (check-in, leave, overtime, transfer,
payroll run) is an event that is visible live, auditable, and analysable.

## Problem

Shift-based organizations run their workforce on tools that lag behind reality:
a check-in lands in a spreadsheet, managers learn who is working hours later,
leave and overtime approvals live in email threads, and analytics is a monthly
export. The consequences:

- No live picture of who is working, where, and whether they are late.
- No trustworthy audit trail for approvals and changes to employee records.
- No way to answer "overtime rate by team, location and seniority over six
  months" without a separate data project.

## Users & access levels

| Role | Jobs to be done |
| --- | --- |
| **Employee** | Check in/out, request leave and overtime, see own records and payslips |
| **Manager** | Watch the team live, approve leave/overtime, run reviews, react to exceptions |
| **HR** | Maintain employee records, run recruitment, read company-wide reports, verify payroll inputs |
| **Finance** | Configure payroll, review disbursements, financial audit |
| **Admin** | Configure the organization, permissions, platform rules |
| **Super Admin** | Provision tenants, cross-tenant operations |

Permission matrix and enforcement rules: `.ai/security.md`.

## Goals

1. **Live workforce visibility** — a check-in reaches the manager's board within
   seconds (Phase 2).
2. **One fact, one event** — every state transition emits a versioned domain
   event; realtime state, analytics and audit are projections of it.
3. **Workflow-grade approvals** — leave and overtime move through explicit,
   audited state machines.
4. **Analytics without a data project** — operational questions are answered by
   the analytics store, not by scanning OLTP tables.
5. **Agent-ready** — a feature can be traced spec → domain → API → event → UI →
   test without searching the repo.

## Non-goals

- A certified tax/payroll compliance engine. Payroll is a deterministic
  simulation (ADR 0003).
- Native mobile apps (responsive web first).
- Microservices. Modular monolith until a domain must scale independently
  (ADR 0002).
- Generic BI tooling — we serve workforce questions, not arbitrary SQL.

## MVP scope (Phase 1)

The smallest slice that delivers the product's core value:

1. **Organizations** — departments, positions, locations (with timezone).
2. **Employees** — records, employment history, transfers, termination.
3. **Attendance** — check-in/check-out, lateness, live workforce board.

**Phase 1 exit criteria**

- All three features work end-to-end: web → API → PostgreSQL.
- Every feature has a spec with ticked acceptance criteria, tests on each
  surface, and `make check` green.
- Tenant isolation and audit logging are proven by tests, not by convention.
- Event contracts for `employee.*` and `attendance.*` exist under
  `contracts/events/` even though the bus arrives in Phase 3.

Explicitly **not** in Phase 1: authentication, realtime push, leave, payroll,
recruitment, performance, analytics store, notifications.

## Domain coverage (target)

```text
WorkPulse
├── Organization        departments · teams · positions · locations
├── Employees           profile · employment · documents · skills · history
├── Attendance          check-in · check-out · late · overtime · shift
├── Leave               request · approval · balance · calendar
├── Payroll             salary · timesheet · overtime · payslip
├── Recruitment         jobs · candidates · interviews · hiring
├── Performance         goals · reviews · feedback · skills
├── Notifications       delivery across channels
├── Reports             workforce analytics
└── Real-time Command Center   live workforce board
```

Detailed context boundaries, aggregates and the event catalog: `.ai/domain.md`.

## Product principles

1. **Every state transition is an event.** If it changes a workflow state, it
   publishes a versioned event and writes an audit entry.
2. **Realtime is a surface, not a feature.** The live board is first-class from
   the moment attendance exists.
3. **Payroll is simulated, never improvised.** Deterministic formula first,
   regional tax/compliance never (ADR 0003).
4. **Security is structural.** Tenant scoping lives in repositories, PII rules
   are enforced by convention *and* tests, access checks live in `policies.py`.
5. **One feature → one spec → one domain → one API flow → one event flow → one
   test flow.** No feature is "done" while any link in that chain is missing.
6. **Boring first.** Each technology is adopted in the phase where a requirement
   demands it, with an ADR.

## Success metrics (proposed — confirm)

| Phase | Metric |
| --- | --- |
| 1 | All three MVP features pass `make check`; tenant-isolation + audit tests exist for every domain |
| 2 | Check-in visible on the manager board ≤ 2s after the API responds (p95) |
| 3 | 100% of domain state transitions publish a versioned event; consumers are replay-safe (idempotent on `event_id`) |
| 4 | "Overtime by team/location/seniority, last 6 months" answers in < 1s |
| 6 | Every request traced; p95 latency + error rate dashboards live |
| 7 | Load test sustains target check-in throughput with p95 < 300ms API latency |

## Constraints

- Phase 1 stack: Next.js (App Router) + TypeScript, FastAPI + Python (uv),
  PostgreSQL. Later phases per `.ai/architecture.md`.
- Multi-tenant data model from day one (`tenant_id` on every row and event).
- Sensitive employee data must never be logged.
- Solo maintainer + AI agents: prefer small, reversible steps over big designs.

## Decisions taken (2026-09-24)

1. **Multi-tenant from day one** — `tenant_id` on every row and event, one seeded
   dev tenant, a tenant-isolation test per domain (ADR 0004).
2. **Phase 1 identity = dev headers** (`X-Tenant-Id`, `X-Actor-Id`, `X-Role`),
   with real authN + RBAC in Phase 2 (ADR 0004). Phase 1 endpoints must not be
   exposed publicly.
3. **Attendance capture in Phase 1 = web button only.** QR, geo-fencing and
   hardware terminals are deferred.
4. **Employee documents & skills deferred** — documents to Phase 4+ (presigned
   URLs after a security review), skills to the Performance context.
5. **Attendance Phase 1 includes minimal Shifts**, so lateness is real from the
   first release; correction requests wait for the Phase 3 workflow.
6. **Test tooling: Vitest + Testing Library** for `apps/web` and **Playwright**
   for `tests/e2e/` — see `.ai/testing.md`.
7. **Payroll stays a deterministic simulation** (ADR 0003).

Feature-level decisions are recorded in each spec's `## Decisions` section; all
Phase 1 specs are resolved and ready for `/plan`.