# WorkPulse — Domain model

> Bounded contexts, ubiquitous language, event catalog and state machines.
> Feature specs in `.ai/features/` implement against this map; if a spec and
> this file disagree, fix this file in the same change.

## Bounded contexts

| Context | Purpose | Key aggregates | Phase |
| --- | --- | --- | --- |
| **Organization** | the structure the workforce hangs off | Department, Position, Location, Team | 1 |
| **Employees** | the employee record and its employment history | Employee, Employment | 1 |
| **Attendance** | presence in real time | AttendanceRecord, Shift | 1 |
| **Identity & Access** | users, roles, permissions, sessions | User, Session, Role, PermissionGrant | 2 |
| **Realtime Command Center** | live workforce board | PresenceState (hot state) | 2 |
| **Leave** | requests, approvals, balances, calendar | LeaveRequest, LeaveBalance, LeavePolicy | 3 |
| **Notifications** | message delivery across channels | Notification, Delivery | 3 |
| **Payroll** | deterministic salary simulation | Salary, Timesheet, PayrollRun, Payslip | 3–4 |
| **Analytics & Reports** | OLAP projections and workforce reports | analytical tables/views (no OLTP aggregates) | 4 |
| **Recruitment** | jobs → candidates → interviews → hiring | Job, Candidate, Interview | 4+ |
| **Performance** | goals, reviews, feedback, skills | Goal, Review, Skill | 4+ |

## Context map

```text
                 Identity & Access
                        │ authorizes every call
                        ▼
   Organization ──► Employees ──► Attendance ──► Realtime Command Center
        │               │               │
        │               │               └──► Leave ──► Payroll
        │               └──► Performance              ▲
        └─────────────────────────────────────────────┘
                        │
                        ▼ (all contexts publish events)
                  Notifications · Analytics & Reports
```

Rules:

- **Organization** and **Employees** are upstream: everything references them,
  they reference nothing except Identity & Access.
- Contexts integrate through **services** (Phase 1–2) and **events** (Phase 3+).
  Never through another context's tables.
- **Analytics & Reports** and **Notifications** are pure consumers: they must be
  rebuildable from the event stream without touching OLTP. For **Notifications**
  that means the `Notification`/`Delivery` rows (`.ai/features/notifications.md`)
  — recipient and content enrichment is a read-only reference lookup through
  ports (events carry no PII, `.ai/security.md`), never a projection input and
  never another context's tables directly. The one deliberate carve-out is the
  user-owned `notifications.read_at` overlay (ADR 0016): the inbox HTTP path may
  write it, the worker never does, and a rebuild may drop it.

## Ubiquitous language

| Term | Meaning |
| --- | --- |
| Tenant | one customer organization; the hard isolation boundary |
| Employee | a person employed by a tenant; the core aggregate |
| Employment | one period of engagement (position, department, location, type, effective dates). An employee has a history of employments |
| Position | job title/role definition (e.g. "Senior Nurse") |
| Department | organizational unit, may be nested |
| Location | physical site, owns a timezone |
| Shift | planned work window (start, end, grace minutes) used to judge lateness |
| Attendance record | one check-in → check-out pair for an employee on a workday |
| Presence state | derived, live status: `PRESENT`, `REMOTE`, `CHECKED_OUT`, `ABSENT`, `ON_LEAVE` with an orthogonal `late` flag (ADR 0009) |
| Leave request | a time-off ask moving through an approval state machine |
| Leave balance | entitlement per leave type per year |
| Overtime | hours beyond the shift, requiring approval before payroll counts them |
| Timesheet | approved attendance + overtime aggregated for a payroll period |
| Payroll run | one deterministic calculation of payslips for a period |
| Correction request | an employee's ask to fix a wrong attendance record |
| Audit entry | immutable record of who did what to which entity, when |

## Event catalog

Contracts are written **before** producers. "Contract" is the phase where the
versioned JSON schema lands in `contracts/events/`; "Published" is the phase
where it actually flows on the bus.

| `event_type` | Emitted when | Contract | Published | Main consumers |
| --- | --- | --- | --- | --- |
| `employee.created` | an employee record is created | 1 | 3 | Analytics, Notifications |
| `employee.updated` | profile/employment fields change | 1 | 3 | Analytics |
| `employee.transferred` | department/position/location changes | 1 | 3 | Analytics, Notifications |
| `employee.terminated` | employment ends | 1 | 3 | Analytics, Identity |
| `attendance.checked_in` | a check-in is recorded | 1 | 3 | Realtime, Analytics, Notifications |
| `attendance.checked_out` | a check-out closes a record | 1 | 3 | Realtime, Analytics |
| `attendance.late` | check-in lands after shift start + grace | 1 | 3 | Notifications, Analytics |
| `attendance.correction_requested` | an employee asks for a fix | 3 | 3 | Workflow, Notifications |
| `leave.requested` | a leave request is submitted | 3 | 3 | Workflow, Notifications |
| `leave.approved` / `leave.rejected` | a manager decides | 3 | 3 | Payroll, Notifications, Analytics |
| `leave.withdrawn` | the requester withdraws a pending request | 3 | 3 | Workflow, Notifications |
| `overtime.requested` / `overtime.approved` | overtime moves through approval | 3 | 3 | Payroll, Notifications |
| `payroll.calculated` | a payroll run completes | 4 | 4 | Notifications, Analytics |
| `payslip.issued` | a payslip becomes available | 4 | 4 | Notifications |
| `candidate.created` / `interview.completed` | recruitment moves | 4+ | 4+ | Notifications, Analytics |
| `performance.review_completed` | a review is finalized | 4+ | 4+ | Analytics |

Envelope and versioning rules: `contracts/README.md`,
`contracts/events/common/event-envelope.v1.json`.

## State machines

**Attendance (per employee, per workday)**

```text
        (no record)
             │ check_in
             ▼
   PRESENT ──┬── check_out ──► CHECKED_OUT
   or REMOTE │
   (`late` flag set at check-in if after shift start + grace; never cleared)
```

**Leave request**

```text
DRAFT ─► PENDING ─┬─► APPROVED ─► (feeds payroll + balance deduction)
                  └─► REJECTED
```

**Overtime**

```text
REQUESTED ─► MANAGER_APPROVED ─► HR_VERIFIED ─► INCLUDED_IN_PAYROLL
      └──────────────┴───────────────┴─► REJECTED
```

**Employment**

```text
ACTIVE ─► ON_LEAVE ─► ACTIVE
   └────────────► TERMINATED (terminal; record is soft-deleted, never erased)
```

Every transition arrow is an event + an audit entry.

## Invariants that must hold in code

1. `tenant_id` is set on every row and every event, and every query is scoped
   by it (enforced in repositories, never in routers).
2. An employee has at most one open attendance record at a time.
3. Employment history is append-only: a transfer adds a new employment period,
   it does not rewrite the previous one.
4. A terminated employee cannot check in, request leave, or be assigned.
5. No aggregate is mutated outside its own domain's service.
6. Sensitive fields (salary, documents, leave reasons) never appear in logs or
   in event payloads beyond identifiers.