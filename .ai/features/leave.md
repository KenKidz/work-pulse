# Feature: Leave

- **Status:** approved
- **Phase:** 3
- **Size:** M
- **Surfaces:** api+web+events+workers
- **Links:** `.ai/domain.md` (Leave context, Leave request state machine, invariants 1–6) · `attendance.md` (board counts, Decisions 7 — `absent` must subtract leave) · `realtime-consumer.md` + ADR 0013 (consumer as sole `PresenceFact` producer) · ADR 0012 (outbox transport) · `.ai/security.md` (roles matrix, PII)

## Problem

Leave and its approval live in email threads (`.ai/product.md`): an employee has
no structured way to ask for time off, a manager decides without seeing the team
picture, and nobody can answer "who is off this week?" from the system. The live
board compounds this — attendance Decision 7 accepted that `absent` over-counts
because leave data does not exist. This feature gives leave a real home: requests
move through an explicit approval state machine, approvals deduct from per-year
balances, and approved leave is reflected on the live board.

## Business rules

- **State machine** (extends `.ai/domain.md` with `WITHDRAWN`; every transition
  is an event + an audit entry):

  ```text
  (submit) ─► PENDING ─┬─► APPROVED
                       ├─► REJECTED
                       └─► WITHDRAWN (requester only, from PENDING)
  ```

  `APPROVED`, `REJECTED` and `WITHDRAWN` are terminal. There is no `DRAFT`
  state in v1 (see Out of scope).
- **Requesting is own-only** (security matrix): a user may create and view their
  own requests via `identity.employee_id`. A terminated employee cannot request
  leave (invariant 4 → `409 employee_not_active`).
- **Deciding is never self-approval**: the requester can never approve or reject
  their own request (`403`), whatever their role. Otherwise: the employee's team
  manager, or HR / Admin / Super Admin (security matrix: *Approve leave =
  team/HR*). Employees have no decide capability at all.
- **Single-step decision** — one `approve` or `reject` with an optional
  `decision_note` closes the request. No HR re-verification step.
- **Whole days, inclusive**: `days = (end_date - start_date) + 1`;
  `start_date <= end_date`, both `YYYY-MM-DD`. Half-days do not exist in v1.
- **Overlaps are blocked**: a new request whose `[start_date, end_date]`
  intersects an existing `PENDING` or `APPROVED` request of the same employee
  (inclusive) → `409 leave_overlaps`. `REJECTED`/`WITHDRAWN` requests never
  block.
- **Start dates**: `annual`/`unpaid` must start today or later; `sick` may
  start up to 7 days in the past (server date at submit). Anything older →
  `409 leave_start_too_old`.
- **Balances**: `LeaveBalance` is per `(tenant, employee, leave_type, year)`.
  Seeded leave types: `annual`, `sick`, `unpaid`; `unpaid` does not touch a
  balance. `annual`/`sick` deduct `days` from the year of `start_date` on
  `APPROVED`. v1 has no path back out of `APPROVED` (cancel is out of scope),
  so no restore path exists.
- **Insufficient balance** refuses the request at submit
  (`409 leave_insufficient_balance`) and is re-checked at approve time; a
  balance never goes negative.
- **Check-in is blocked while `APPROVED` leave covers the workday**
  (`409 employee_on_leave`), symmetric with `employee_not_active` — `ON_LEAVE`
  and `PRESENT` are contradictory presence states. Attendance enforces this via
  a leave read interface (no import of leave internals).
- **Board integration**: for a given board date, an employee with `APPROVED`
  leave covering that date is `ON_LEAVE` — counted in the board's `on_leave`
  bucket and excluded from `absent` (closes attendance Decision 7), and this
  holds even when a same-day attendance record exists (`on_leave` wins; the
  record stays in the attendance history). The live wall reflects an approval
  within 2s through the relay → realtime-consumer hop — the only wall-affecting
  transition, since `PENDING` never reaches the wall and rejection/withdrawal
  therefore need no delta.
- **PII**: `reason` is sensitive (`.ai/security.md`) — never in logs, event
  payloads, error messages or audit `changes` values. Events carry identifiers
  and dates only.
- Tenant isolation (invariant 1) and policy placement in
  `domains/leave/policies.py` (AGENTS.md rule 12) hold as everywhere else.

## Scope

- Leave requests: create (submit to `PENDING`), list/detail with role-scoped
  visibility, approve, reject, withdraw (own `PENDING` only).
- Seeded leave types (`annual`, `sick`, `unpaid`) + per-year `LeaveBalance`
  with deduction on approval and an employee-visible balances read.
- Event contracts `leave.requested` / `leave.approved` / `leave.rejected` /
  `leave.withdrawn` written first, produced through the `EventOutbox` port in
  the same transaction as the mutation (ADR 0012).
- Live board `on_leave` bucket fed by the realtime consumer, `absent` corrected.

## Out of scope

- `DRAFT` (save-for-later) requests and editing a submitted request — withdraw
  and re-request instead.
- Cancelling an `APPROVED` request (balance restore) — a manager/HR cancel flow
  with balance restore is a follow-up if needed.
- Half-days, working-day/holiday calendars, time-of-day leave — whole calendar
  days only.
- Leave types or entitlements management UI (admin CRUD, carry-over, proration) —
  types and entitlements are seeded/migrated data.
- Team leave calendar view.
- Notifications (email/Slack) — the events exist for the Notifications feature
  to consume; no delivery here.
- Payroll integration of approved leave (Phase 4 reads `leave.approved`).

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/leave/` (models · schemas · service · repository · events · policies · exceptions) |
| API router | `apps/api/app/api/v1/leave.py` |
| Events | `contracts/events/leave/` (`requested` · `approved` · `rejected` · `withdrawn`, v1) |
| Workers | `workers/realtime/` (extend the consumer with `leave.*` → board delta) |
| Web feature | `apps/web/features/leave/` |
| Web routes | `apps/web/app/(dashboard)/leave/` |
| API tests | `apps/api/tests/unit` · `tests/integration` · `tests/contract` |
| E2E | `tests/e2e/leave.spec.ts` |

## Roles & access

| Capability | Employee | Manager | HR | Finance | Admin | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| Create request / view own requests & balances | own | own | own | own | own | own |
| View team requests | — | team | Y | — | Y | Y |
| Approve / reject | — | team | Y | — | Y | Y |
| Withdraw own `PENDING` request | own | own | own | own | own | own |

Out-of-scope viewers get `404` (existence is not leaked); a viewer with standing
who attempts a forbidden transition gets `403`.

## API contract (final — pinned by T1, 2026-10-19)

| Method & path | operationId (orval hook) | Input | Response |
| --- | --- | --- | --- |
| `POST /api/v1/leave/requests` | `createLeaveRequest` | `LeaveRequestCreate` | `201 LeaveRequest` (`PENDING`) |
| `GET /api/v1/leave/requests` | `listLeaveRequests` | `?employee_id=&status=&from=&to=&page=&page_size=` | `200 {"items": LeaveRequest[], "page": Page}` |
| `GET /api/v1/leave/requests/{id}` | `getLeaveRequest` | — | `200 LeaveRequest` |
| `POST /api/v1/leave/requests/{id}/approve` | `approveLeaveRequest` | `LeaveDecision` | `200 LeaveRequest` |
| `POST /api/v1/leave/requests/{id}/reject` | `rejectLeaveRequest` | `LeaveDecision` | `200 LeaveRequest` |
| `POST /api/v1/leave/requests/{id}/withdraw` | `withdrawLeaveRequest` | — | `200 LeaveRequest` |
| `GET /api/v1/leave/types` | `listLeaveTypes` | — | `200 LeaveType[]` (bare, unpaged — seeded) |
| `GET /api/v1/leave/balances` | `listLeaveBalances` | `?employee_id=&year=` | `200 LeaveBalance[]` |

Pinned error codes for the mutating flows (`409` conflict responses carry these
stable `code` values): `leave_overlaps`, `leave_not_pending`,
`leave_insufficient_balance`, `leave_start_too_old`, `employee_not_active`.
JSON `status` values are lowercase (`pending|approved|rejected|withdrawn`),
matching `LeaveRequest.status` below.

```jsonc
LeaveRequest {
  "id", "tenant_id", "employee_id", "leave_type_id": uuid,
  "leave_type_code": "annual|sick|unpaid",
  "start_date": date, "end_date": date, "days": int,
  "status": "pending|approved|rejected|withdrawn",
  "reason": string|null,          // PII — visible to own requester + deciders only
  "decision_note": string|null,   // PII — same visibility rule
  "decided_by": uuid|null,        // actor user id (ADR 0010)
  "decided_at": date-time|null,
  "created_at", "updated_at"
}

LeaveType   { "id", "code": "annual|sick|unpaid", "name",
              "counts_against_balance": bool }
LeaveBalance { "tenant_id", "employee_id", "leave_type_id", "year": int,
               "entitlement_days": int, "used_days": int, "remaining_days": int }

LeaveRequestCreate { "leave_type_id": uuid, "start_date": date,
                     "end_date": date, "reason"?: string }   // reason 1..500
LeaveDecision      { "decision_note"?: string }               // 1..500
```

Dates are `YYYY-MM-DD`. `reason`/`decision_note` are never logged, never in
event payloads, never in audit `changes` values.

## Events

Contract before producer (AGENTS.md rule 4); written in T2, produced through the
`EventOutbox` port in the same transaction as the transition (ADR 0012), relayed
verbatim to `workpulse.events`:

| `event_type` | Emitted when | `data` payload |
| --- | --- | --- |
| `leave.requested` | submit to `PENDING` | `leave_request_id`, `employee_id`, `leave_type_code`, `start_date`, `end_date`, `days` |
| `leave.approved` | approve | `leave_request_id`, `employee_id`, `leave_type_code`, `start_date`, `end_date`, `days`, `decided_by` |
| `leave.rejected` | reject | `leave_request_id`, `employee_id`, `leave_type_code`, `start_date`, `end_date`, `decided_by` |
| `leave.withdrawn` | withdraw (own, `PENDING`) | `leave_request_id`, `employee_id`, `start_date`, `end_date` |

No reason or note text — identifiers, dates and state only.

## Edge cases

- Approve/reject/withdraw a request that is not `PENDING` → `409 leave_not_pending`.
- Approve/reject own request → `403` (never self-approval), even for HR/Admin.
- Request with `end_date < start_date` → `422` (schema) or `409` at service level.
- Overlap against own `PENDING`/`APPROVED` range (inclusive endpoints) →
  `409 leave_overlaps`; against `REJECTED`/`WITHDRAWN` → allowed.
- Days exceed remaining balance at submit or at approve →
  `409 leave_insufficient_balance`; balance never goes negative.
- `annual`/`unpaid` starting before today, or `sick` starting more than 7 days
  in the past → `409 leave_start_too_old`.
- Check-in on a day covered by `APPROVED` leave → `409 employee_on_leave`.
- `unpaid` request ignores balance entirely.
- Request spanning a year boundary deducts the full `days` from the year of
  `start_date` (v1 limitation, documented).
- Two concurrent approves of the same request → exactly one transition; the
  other `409 leave_not_pending`.
- Cross-tenant request id → `404`; a manager requesting another team's request
  detail → `404`.
- Board date inside an `APPROVED` range → `on_leave`; a range ending yesterday
  or starting tomorrow does not count.
- Withdraw restores no balance (nothing was deducted before approval).

## Acceptance criteria

- [ ] API: submitting a request returns `201` in `PENDING` with the correct inclusive `days`, and the balance is untouched.
- [ ] API: approving a request moves it to `APPROVED`, deducts `days` from the right `(leave_type, year)` balance, writes an audit entry and produces a valid `leave.approved` envelope.
- [ ] API: rejecting leaves the balance untouched and produces `leave.rejected`.
- [ ] API: withdrawing own `PENDING` request → `WITHDRAWN` + `leave.withdrawn`.
- [ ] API (failure): an overlapping `PENDING`/`APPROVED` range → `409 leave_overlaps`.
- [ ] API (failure): approving/rejecting a non-`PENDING` request → `409 leave_not_pending`.
- [ ] API (failure): days beyond the remaining balance → `409 leave_insufficient_balance`; `unpaid` bypasses the check.
- [ ] API (failure): a `sick` request starting more than 7 days in the past (or any `annual`/`unpaid` request starting before today) → `409 leave_start_too_old`.
- [ ] API (failure): check-in on a workday covered by `APPROVED` leave → `409 employee_on_leave`.
- [ ] API (failure): a terminated employee submitting → `409 employee_not_active`.
- [ ] API (denied role): an `Employee`-role approve attempt → `403`; self-approval → `403` even for HR/Admin.
- [ ] API (tenant isolation): tenant B sees none of tenant A's requests or balances (assert `404`/empty).
- [ ] Contracts: the four `leave.*` v1 schemas exist, validate produced payloads, and contain no `reason`/`decision_note` text.
- [ ] Unit tests use an injected clock — no wall-time assertions (date rules take `now` as a parameter).
- [ ] Workers: a `leave.approved` envelope through relay → consumer flips the employee's board bucket to `on_leave` and removes them from `absent` within 2s; other event types remain filtered.
- [ ] Web: an employee submits a request from the leave page and sees it in "My requests" as `PENDING`.
- [ ] Web: a manager sees their team's pending requests and can approve/reject; a self-approval attempt shows the error state.
- [ ] E2E: request → approve → the live board shows the employee `on_leave` (and out of `absent`) without a manual refresh.

## Decisions (PO, 2026-10-19)

Scope answers (feature kickoff):

1. **v1 = requests + approvals + balances.** Team calendar view waits.
2. **Seeded types + entitlements** — `annual`/`sick`/`unpaid`; HR-set per-year
   entitlement days; `unpaid` bypasses balances. No policy CRUD in v1.
3. **Single-step approval** — one decision closes the request (matches the
   domain state machine; no HR re-verification state).
4. **Overlaps blocked** — `409 leave_overlaps` against own `PENDING`/`APPROVED`.
5. **Withdraw `PENDING` only** — no edit-after-submit (re-request), no cancel of
   `APPROVED` in v1.
6. **Board integration in scope** — approved leave feeds `on_leave`/`absent`
   through the relay → realtime-consumer hop (closes attendance Decision 7).

Detail answers (spec review):

7. **Whole inclusive calendar days** for `days` and balance deduction in v1 —
   working-day/holiday calendars are out of scope. Rationale: deterministic and
   testable without a holiday table; revisit with Payroll (Phase 4), which owns
   working-day questions.
8. **Backdating: `sick` may start up to 7 days in the past; `annual`/`unpaid`
   must start today or later.** Rationale: sickness is discovered after the
   fact; planned leave is not. The 7-day window is a plain business rule, no
   policy table.
9. **Check-in is blocked while `APPROVED` leave covers the workday**
   (`409 employee_on_leave`), symmetric with `employee_not_active`. Rationale:
   `ON_LEAVE` and `PRESENT` are contradictory presence states; an approved
   leave day is not a worked day. (Attendance service enforces this via the
   leave read interface; it does not import leave internals.)
10. **Balances never go negative**: submit refuses with
    `409 leave_insufficient_balance`, and approve re-checks (concurrent
    requests) and refuses the same way. Rationale: silent negative balances are
    a payroll surprise; the error is actionable at the form.

## Plan

Planned 2026-10-19. **Contract-first:** T1 finalizes the API contract in this
spec (its deltas are decided there, not during the build), T2 writes the four
`leave.*` event contracts before any producer or consumer code, the backend
(T3–T6) builds against them, T7 extends the realtime consumer, and the web (T8)
consumes only the regenerated `@workpulse/api-client`; T9 lands the e2e flow and
records. Every task ends with `make check` green. Two contract clarifications
were recorded at plan time (business rules above): the wall delta is needed only
for `leave.approved`, and `on_leave` wins over a same-day attendance record —
the only reading under which the stateless consumer's full-row upserts and the
REST snapshot can never disagree (ADR 0014, T5).

### Acceptance criteria → where they are satisfied

| AC | Landed by | Proof |
| --- | --- | --- |
| Submit → `201 PENDING`, correct `days`, balance untouched | T4, T6 | `tests/integration/test_leave_requests.py` |
| Approve → `APPROVED` + deduction + audit + valid `leave.approved` | T4, T6 | `test_leave_requests.py` (outbox envelope + audit row asserted) |
| Reject → balance untouched + `leave.rejected` | T4, T6 | `test_leave_requests.py` |
| Withdraw own `PENDING` → `WITHDRAWN` + `leave.withdrawn` | T4, T6 | `test_leave_requests.py` |
| Overlap → `409 leave_overlaps` | T4, T6 | `test_leave_requests.py` |
| Non-`PENDING` transition → `409 leave_not_pending` (incl. concurrent double-approve) | T4, T6 | unit + `test_leave_requests.py` |
| Insufficient balance → `409 leave_insufficient_balance`; `unpaid` bypasses | T4, T6 | unit + `test_leave_requests.py` |
| Backdating violation → `409 leave_start_too_old` | T4, T6 | unit + `test_leave_requests.py` |
| Check-in on covered workday → `409 employee_on_leave` | T5 | `tests/integration/test_attendance_*.py` |
| Terminated requester → `409 employee_not_active` | T4, T6 | `test_leave_requests.py` |
| `Employee` approve → `403`; self-approval → `403` even for HR/Admin | T4, T6 | `tests/unit/leave/test_policies.py` + `tests/integration/test_leave_roles.py` |
| Tenant isolation → `404`/empty | T6 | `tests/integration/test_leave_tenant_isolation.py` |
| Four schemas validate payloads, no PII keys | T2 | `tests/contract/test_leave_events.py` |
| Injected clock everywhere | T4 (convention) | `tests/unit/leave/conftest.py` fixed `NOW`; no wall-time asserts |
| `leave.approved` → board `on_leave` / out of `absent` ≤2s; other types filtered | T5, T7 | `tests/integration/test_realtime_consumer.py` + relay-hop test |
| Web: submit → "My requests" `PENDING` | T8, T9 | `apps/web/tests/leave/` + `tests/e2e/leave.spec.ts` |
| Web: manager approve/reject; self-approval error state | T8 | `apps/web/tests/leave/` |
| E2E: request → approve → live board `on_leave` without refresh | T9 | `tests/e2e/leave.spec.ts` (<2s elapsed assert) |

### T1 — Pin the API contract (docs only)

Finalize `## API contract` in this spec: header → "final — pinned by T1,
2026-10-19"; pin the eight operationIds (orval hook names): `createLeaveRequest`,
`listLeaveRequests`, `getLeaveRequest`, `approveLeaveRequest`,
`rejectLeaveRequest`, `withdrawLeaveRequest`, `listLeaveTypes`,
`listLeaveBalances`; pin the 409 code list (`leave_overlaps`,
`leave_not_pending`, `leave_insufficient_balance`, `leave_start_too_old`,
`employee_not_active`) and that JSON `status` values are lowercase. No code.

Verify: docs diff only; `make check` unaffected (first full run at T2).

### T2 — Event contracts + payload builders (contract before producer)

- `contracts/events/leave/leave-{requested,approved,rejected,withdrawn}.v1.json`
  — payloads exactly per the Events table; every one carries `data.employee_id`
  (the Kafka key; `EventEnvelope` in `app/shared/outbox.py` requires it).
- `apps/api/app/domains/leave/{__init__.py,events.py}` — pure builders +
  `_envelope(...)` mirroring `attendance/events.py` (envelope key is **`version`**,
  not `event_version`); `decided_by` is the actor user id (ADR 0010).
- `apps/api/tests/contract/test_leave_events.py` — mirror
  `test_attendance_events.py` loader (`Draft202012Validator`, `glob("leave-*.v1.json")`)
  + the PII-marker key sweep (`reason`, `note`, … must not appear).

Verify: `make check-api` green (contract tests pass with no producer yet).

### T3 — Persistence: models · migration 0006 · repositories

- `apps/api/app/domains/leave/models.py`: `LeaveType` (`leave_types`: `id`,
  `tenant_id`, `code`, `name`, `counts_against_balance`,
  `default_entitlement_days`, `created_at`; unique `(tenant_id, code)`),
  `LeaveRequest` (`leave_requests`: id, tenant_id, employee_id, leave_type_id,
  start_date, end_date, days, status, reason, decision_note, decided_by,
  decided_at, created_at, updated_at; index `(tenant_id, employee_id, status,
  start_date)`), `LeaveBalance` (`leave_balances`: id, tenant_id, employee_id,
  leave_type_id, year, entitlement_days, used_days; unique `(tenant_id,
  employee_id, leave_type_id, year)`).
- Migration `apps/api/alembic/versions/0006_leave.py`
  (`revision="0006", down_revision="0005"`); add the models import to
  `alembic/env.py`. Tables only — the three type rows are provisioned per
  tenant by the service (below), so integration tests (no seeds, TRUNCATE per
  test) work unchanged.
- `apps/api/app/domains/leave/repository.py` (Protocol, every method takes
  `tenant_id` — same header rule as `attendance/repository.py`):
  `ensure_leave_types(tenant_id)`, `employee_facts(tenant_id, employee_id)`
  (minimal: id, status, manager_id — infra joins `employees`),
  `get_or_create_balance(...)`, `find_overlapping(...)`, `transition_*`
  conditional updates, paged `list_requests`/`list_balances`.
- `apps/api/app/infrastructure/postgres/repositories/leave.py` — Postgres
  impl (tenant condition first, unique-violation mapping like
  `attendance.py`'s `_map_unique_violation`).
- `apps/api/tests/integration/conftest.py` — TRUNCATE list += `leave_requests,
  leave_balances, leave_types`.

Verify: `make check-api` green (migration runs in the integration harness;
existing tests unaffected).

### T4 — Leave domain: exceptions · policies · service · unit tests

- `exceptions.py` — one class per pinned code, `status_code`/`code` style of
  `attendance/exceptions.py`: `LeaveOverlapsError` (409), `LeaveNotPendingError`
  (409), `LeaveInsufficientBalanceError` (409), `LeaveStartTooOldError` (409),
  and a local `EmployeeNotActiveError` (409 `employee_not_active` — same stable
  code attendance uses, own class per domain-owns-its-errors convention).
- `policies.py` — `ensure_can_decide(identity)`: role in
  {MANAGER, HR, ADMIN, SUPER_ADMIN} else `ForbiddenError`; self-approval is a
  `403` for everyone (checked in the service where the request is known).
  Scope narrowing stays in the service (attendance pattern).
- `schemas.py` — the pinned request/response shapes (T1), `reason`/`decision_note`
  1..500, dates `YYYY-MM-DD`.
- `service.py` — `LeaveService(repo, audit: AuditWriter, outbox: EventOutbox)`
  with `now` as the last parameter everywhere (router `_now()` is the only clock
  read): `create_request` (ensure types → status/date/balance rules →
  `find_overlapping` → insert + `leave.requested` + audit), `approve`/`reject`
  (self-approval 403, team scope via `employee_facts.manager_id` for MANAGER,
  HR/Admin all; conditional `WHERE status='pending'` update → else
  `leave_not_pending`; approve re-checks balance race-safely with
  `used_days + :days <= entitlement_days` in the UPDATE, rowcount 0 →
  `leave_insufficient_balance`; event + audit in the same transaction, ADR 0012),
  `withdraw` (own, `PENDING` only), `list_requests`/`get_request` (visibility:
  own for EMPLOYEE/FINANCE; MANAGER self+direct reports; HR/ADMIN/SUPER_ADMIN
  all; out-of-scope → 404), `list_types`, `list_balances` (own; `employee_id`
  param only for the same wider scopes). `reason`/`decision_note` never in
  `changes` (record `"changed"` markers), never logged. `annual`/`sick` balance
  rows are lazily created per `(employee, type, year(start_date))` with
  `entitlement_days` copied from the type's `default_entitlement_days`.
- `tests/unit/leave/` — `fakes.py` (in-memory repo, `RecordingAuditWriter`,
  `RecordingEventOutbox`), `conftest.py` (fixed `NOW`, identity factory),
  `test_service.py` (state machine + every rule incl. boundary dates, year
  boundary, unpaid bypass, concurrent-transition semantics),
  `test_policies.py` (parametrized allow/deny like
  `tests/unit/attendance/test_policies.py`).

Verify: `make check-api` green.

### T5 — Attendance integration: coverage seam + board (ADR 0014 first)

First deliverable: **`.ai/decisions/0014-leave-presence-and-coverage-seam.md`**
recording: `on_leave` wins over a same-day record; the coverage seam
(attendance declares port methods, infra joins `leave_requests` — no domain
imports leave internals, the `EmployeeContext` precedent); the windowed
`leave.approved` mapping (`start_date <= consume-date <= end_date` else no
fact); additive enum extensions (`PresenceStatus` += `on_leave`,
`PresenceReason` += `leave_approved`, `board.v1.json` upsert `reason` enum);
rejection/withdrawal are wall-silent.

- `apps/api/app/domains/leave/repository.py` is the provider side; the consumer
  side is `apps/api/app/domains/attendance/repository.py` += `leave_coverage(
  tenant_id, workday) -> set[UUID]` and `covers_leave(tenant_id, employee_id,
  workday) -> bool`, implemented in
  `app/infrastructure/postgres/repositories/attendance.py` (join to
  `leave_requests` where `status='approved'` and range covers the date — the
  same adapter already joins `employees`/`locations`).
- `attendance/exceptions.py` += `EmployeeOnLeaveError` (409
  `employee_on_leave`).
- `attendance/service.py`: `check_in` raises it immediately after the
  `employee_not_active` check (~line 290); **check-out stays allowed** (closing
  an open record is always safe). `board_detail` (~line 486): compute the
  covered set once; `counts.on_leave` becomes real (was hardcoded 0); `_absent_rows`
  excludes covered ids; a covered employee renders one row with
  `status="on_leave"`, `since=None`, `attendance_id=None`, **overriding** a
  same-day record row (the rule recorded in ADR 0014).
- `contracts/realtime/board.v1.json` — update the `counts.on_leave` description
  ("always 0 until Leave lands" is now stale); no shape change (row status
  enum already contains `on_leave`).
- Tests: attendance unit (board math with a fake coverage port) + integration
  (`check-in` → 409 `employee_on_leave`; board bucket/absent exclusion;
  coexistence rule; range boundary: ending yesterday / starting tomorrow does
  not count).

Verify: `make check-api` green.

### T6 — Routers · integration/audit/role tests · regenerate clients

- `apps/api/app/api/v1/leave.py` — 8 thin routes (identity → policy → service,
  `_now()` once, `operation_id`s per T1, response models per the contract);
  register in `app/api/v1/__init__.py` (`include_router(leave.router,
  tags=["leave"])`).
- `apps/api/tests/integration/` — `test_leave_requests.py` (happy paths with
  **audit-entry + outbox-envelope assertions** per transition),
  `test_leave_roles.py` (Employee approve → 403; self-approval → 403 for
  HR/Admin; manager outside team → 404), `test_leave_tenant_isolation.py`
  (mirror `test_attendance_tenant_isolation.py`), balances behavior tests.
- `make gen-api` (`scripts/export_openapi.py` + orval). **Known churn**: orval
  8.37.0 + prettier 3.9.9 re-wrap ~40 union aliases in
  `packages/api-client/src/index.ts` — resolve the standing TODO here by adding
  `formatter: "prettier"` to `apps/web/orval.config.ts`, commit the one-time
  churn, and tick that TODO item (semantic content unchanged).

Verify: `make check-api && make check-web` green.

### T7 — Workers: consumer mapping + wall deltas + broker tests

- `app/shared/presence.py` — `PresenceStatus` += `"on_leave"`,
  `PresenceReason` += `"leave_approved"`, `PresenceFact.attendance_id` becomes
  optional for the leave fact (`late=False`, `minutes_late=None`, `since=None`,
  `workday` = consume date).
- `contracts/realtime/board.v1.json` — `presence-upsert-frame.reason` enum
  additively += `"leave_approved"` (recorded in ADR 0014; mirrors the ADR 0013
  payload-extension convention).
- `workers/realtime/mapping.py` — `_REALTIME_EVENT_TYPES` += `leave.approved`;
  map v1 `leave.approved` → on-leave fact **only when the payload's range
  covers the consume date** (else `None`, like every filtered event);
  `leave.requested/rejected/withdrawn` stay filtered.
- `domains/realtime/models.py` — `PresenceState` recount handles `on_leave`
  rows (bucket count; rows keyed by employee override prior rows).
- Tests: `tests/contract/test_realtime_frames.py` (enum), `tests/integration/
  test_realtime_consumer.py` (real broker: approve-covered-today flips;
  future/past ranges filtered; other `leave.*` filtered; redelivery idempotent),
  and a relay-hop ≤2s test in the `test_outbox_relay.py` style (submit → approve
  through the outbox → consumer → Redis fact observed).

Verify: `make check-api` green (kafka-test + redis-test up).

### T8 — Web: leave page + approval inbox

- `apps/web/features/leave/` — `api.ts` (query keys from generated URLs +
  invalidate helpers), `types.ts`, `schema.ts` (zod: dates, reason 1..500),
  `hooks/use-leave.ts` (generated hooks + `leaveFieldErrors` mapping pinned
  codes → form/toast messages, `isForbiddenError` handling),
  `components/LeaveRequestDialog.tsx` (RHF + zod + shadcn Dialog/Select/DatePicker
  style per `EmployeeFormDialog.tsx`), `MyRequestsTable.tsx`, `LeaveBalanceCards.tsx`,
  `TeamApprovals.tsx` (pending list for MANAGER/HR/ADMIN/SUPER_ADMIN with
  approve/reject + optional `decision_note`; own requests never offered
  approve/reject — the self-approval error state renders when the API refuses).
- `apps/web/app/(dashboard)/leave/page.tsx` — composes `PageHeader` + the
  feature components (own view for everyone; approvals section role-gated).
- `apps/web/components/destinations.ts` — `NAV_ITEMS` += `/leave` (all roles).
- Vitest: `apps/web/tests/leave/` — submit happy path + `409 leave_overlaps`
  field error render, approve/reject actions, balance cards, self-approval
  error state.

Verify: `make check-web` green (and `make check-api` untouched).

### T9 — E2E · records

- `tests/e2e/leave.spec.ts` (mirror `command-center.spec.ts` two-context,
  `describe.serial`, `uiLogin` from `auth.ts`; seeded `worker@workpulse.test`
  (Wendy, reports to Morgan) and `manager@workpulse.test`): worker submits via
  UI → "My requests" shows `PENDING`; manager approves via UI; from the approve
  response, the command-center wall shows `presence-on_leave` (and
  `count-on-leave` 1 / reduced `count-absent`) within 2s — same elapsed assert
  as the check-in flip. Overlap refusal shown in the form as the second
  failure case.
- Records (same change): tick the AC boxes above, `Status: done`,
  `tasks/TODO.md` row Now → Done with gate counts,
  `.ai/features/README.md` backlog row → done.

Verify: full `make check` green (608+ API / 156+ web / 18+ e2e as the baseline).

### Risks, unknowns & decisions that deserve an ADR

1. **ADR 0014 (T5) — leave presence semantics + coverage seam.** `on_leave`
   wins over a same-day record; windowed consumer mapping; additive enum
   extensions. The alternative (record wins) would force the stateless consumer
   to know record state and guarantees snapshot/delta disagreement — rejected.
2. **Entitlement default numbers are fixture data, not product semantics** —
   `annual` default 12 days, `sick` 6, `unpaid` 0
   (`counts_against_balance=false`), stored in `leave_types.default_entitlement_days`
   per tenant and confirmed at T3; no code depends on the values.
3. **Wall self-heal at range/day boundaries** — a fact only fires at approval;
   when a range ends (or the day rolls), the connection-local row heals on the
   next 60s REST refetch or snapshot reconnect (existing semantics). Accepted
   for v1; ADR 0014 records it.
4. **Race safety** — double-approve and concurrent balance draws are closed by
   conditional UPDATEs (`status='pending'` guard; `used_days + :days <=
   entitlement_days` guard) inside one transaction; no SELECT-then-UPDATE.
5. **orval formatting churn** — resolved inside T6 (formatter pin + one-time
   re-commit), closing the standing `make gen-api` TODO item; if that churn is
   disputed it must be decided before T8 lands on the regenerated client.
6. **`employee_not_active` is a cross-domain shared stable code** with one
   class per domain (attendance already owns its copy) — deliberate, matches
   the errors convention; no shared error class introduced.

### Test strategy

| Layer | Added / changed | Gate | Failure case covered |
| --- | --- | --- | --- |
| Contract | `tests/contract/test_leave_events.py` (+ enum case in `test_realtime_frames.py`) | `make check-api` | payload PII sweep; schema drift on `reason` enum |
| Unit | `tests/unit/leave/` (service + policies) | `make check-api` | overlap, backdating, insufficient balance, non-`PENDING` transition, self-approval, denied roles, injected-clock boundaries |
| Integration | `tests/integration/test_leave_*.py`, attendance leave tests, `test_realtime_consumer.py` additions, relay-hop test | `make check-api` (kafka/redis test services) | tenant isolation (404), role denial (403), audit+outbox per transition, `409 employee_on_leave`, ≤2s broker flip, filtered event types, idempotent redelivery |
| Web unit | `apps/web/tests/leave/` | `make check-web` | `409` field-error rendering, self-approval error state, forbidden handling |
| E2E | `tests/e2e/leave.spec.ts` | `make check-e2e` (inside `make check`) | request → approve → live wall flip <2s without refresh; overlap refusal in the form |

Mandatory per `.ai/testing.md`: happy path + ≥1 failure case per surface,
tenant isolation, denied-role case for every mutating route, no wall-time in
unit tests. Full `make check` at the end of every task.
