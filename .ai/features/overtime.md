# Feature: Overtime

- **Status:** approved
- **Phase:** 4 (payroll seam P0; its events were catalogued in `.ai/domain.md`'s Phase-3 wave)
- **Size:** M
- **Surfaces:** api+web+events
- **Links:** `.ai/domain.md` (Overtime state machine, invariants 1–6) · ADR 0018 (payroll ↔ overtime seam — read/write ports, finalize flip) · ADR 0014 (seam pattern) · ADR 0010 (actor ids) · ADR 0012 (outbox) · ADR 0017 (decimal/rounding discipline) · `payroll.md` (the consuming side; `NoOvertimeHoursPort` placeholder) · `leave.md` (the analogous approval workflow) · `.ai/security.md` (roles matrix, PII)

## Problem

Overtime approvals live in email threads (`.ai/product.md`): an employee has no
structured way to claim hours worked beyond the shift, a manager decides without
an audit trail, and HR cannot confirm what payroll should pay. Payroll is
already built for it — its formula term `verified_overtime_hours × hourly_rate ×
overtime_multiplier` is wired to the ADR 0018 seam — but the seam's real
adapter does not exist, so the term is hard-wired to `0.00` behind the
`NoOvertimeHoursPort` placeholder. This feature gives overtime a real home:
requests move through the domain's two-step approval machine (manager approves,
HR verifies), and the verified rows become payroll's counted overtime through
ports only.

## Business rules

- **State machine** (extends `.ai/domain.md` with `WITHDRAWN`, as `leave.md`
  did; every transition except the payroll flip is an event + an audit entry):

  ```text
  (submit) ─► REQUESTED ─► MANAGER_APPROVED ─► HR_VERIFIED ─► INCLUDED_IN_PAYROLL
                 │                │                  │
                 └────────────────┴──────────────────┴─► REJECTED

  withdraw (own): REQUESTED | MANAGER_APPROVED ─► WITHDRAWN
  ```

  `REJECTED`, `WITHDRAWN` and `INCLUDED_IN_PAYROLL` are terminal. The domain
  machine draws the `REJECTED` arrow from every pre-inclusion state: HR may
  withdraw a verification (`HR_VERIFIED ─► REJECTED`) before payroll counts the
  row — ADR 0018's finalize rollback assumes exactly this window.
  `INCLUDED_IN_PAYROLL` is written **only** by payroll's finalize through the
  write port (below) — no overtime endpoint reaches it.
- **Requesting is own-only** (security matrix): a user creates and views their
  own requests via `identity.employee_id`. A terminated employee cannot request
  overtime (invariant 4 → `409 employee_not_active`).
- **One workday + hours per request**: `work_date` (single `YYYY-MM-DD`) plus
  `hours` (decimal, `0 < hours ≤ 12`, at most 2 decimal places) and an optional
  `reason` (1..500). Overtime is a **claim for hours already worked**:
  `work_date ≤ today` (server date at submit) → otherwise
  `409 overtime_work_date_in_future`.
- **Open-request overlap is blocked**: a new request whose `work_date` equals
  the `work_date` of an existing request of the same employee in `REQUESTED`,
  `MANAGER_APPROVED` or `HR_VERIFIED` → `409 overtime_overlaps`.
  `REJECTED`/`WITHDRAWN` never block. Once a row is `INCLUDED_IN_PAYROLL` a new
  claim for the same date is allowed: rows are counted individually, so a later
  "additional hours" claim pays exactly its own verified hours.
- **Deciding is never self-decision**: the requester can never approve, reject,
  verify or withdraw anyone's request but their own, and never their own
  decisions (`403`), whatever their role. Otherwise the same non-requester
  actor may perform both steps when they hold standing for both (e.g. HR
  approving and verifying).
- **Two-step decision** (security matrix: *Approve leave / overtime =
  team/HR*):
  - `approve` (→ `MANAGER_APPROVED`): the employee's team manager, or HR /
    Admin / Super Admin, with an optional `manager_note`.
  - `verify` (→ `HR_VERIFIED`): HR / Admin / Super Admin **only** — a manager
    without an HR role cannot verify. Accept/reject only: HR confirms the hours
    exactly as requested and may never adjust them (PO Decision 3). Optional
    `hr_note`.
  - `reject` (→ `REJECTED`) is allowed from `REQUESTED` (manager-step roles),
    `MANAGER_APPROVED` and `HR_VERIFIED` (HR-step roles), carrying the step's
    note and recording `rejected_from`.
- **Withdrawal is own-only and pre-verification**: the requester withdraws from
  `REQUESTED` or `MANAGER_APPROVED` → `WITHDRAWN`. `HR_VERIFIED` is payroll-bound
  and cannot be withdrawn (PO Decision 4).
- **Payroll seam** (ADR 0018 — same pattern as the attendance↔leave seam, ADR
  0014): payroll reads verified rows through `OvertimeHoursPort.verified_hours`
  and flips counted rows through `OvertimeHoursPort.mark_included`; overtime
  never imports payroll internals and payroll never touches overtime tables.
  The real adapter (Postgres, `app/infrastructure/postgres/`) replaces the
  `NoOvertimeHoursPort` wiring in `app/dependencies.py` — **no payroll code
  changes**. The flip is state-guarded (`WHERE status = 'HR_VERIFIED'`),
  idempotent for the same run, and each row is counted by **at most one
  `FINALIZED` run** — the overtime side (this feature) proves that with a
  guard/uniqueness test (ADR 0018 decision 4).
- **PII**: `reason`, `manager_note` and `hr_note` are sensitive
  (`.ai/security.md`) — never in logs, event payloads, error messages or audit
  `changes` values. Events carry identifiers, the work date, hours (a count,
  not an amount) and state only.
- Tenant isolation (invariant 1), policy placement in
  `domains/overtime/policies.py` (AGENTS.md rule 12) and time-as-a-parameter
  (`.ai/conventions.md`) hold as everywhere else.

## Scope

- Overtime requests: create (submit to `REQUESTED`), list/detail with
  role-scoped visibility, approve, verify, reject (any open state), own
  withdraw.
- Event contracts `overtime.requested` / `overtime.approved` /
  `overtime.verified` / `overtime.rejected` / `overtime.withdrawn` written
  first, produced through the `EventOutbox` port in the same transaction as the
  transition (ADR 0012).
- The real `OvertimeHoursPort` Postgres adapter replacing the `NoOvertimeHoursPort`
  placeholder, with the at-most-one-finalized-run guard test and a payroll
  seam integration test (verified rows → timesheet `overtime_hours` → finalize
  flip).
- Web: an employee "My overtime" view (submit, withdraw, history), a
  role-gated manager inbox (approve/reject) and an HR verification queue
  (verify/reject), with an Overtime nav entry.

## Out of scope

- Hour adjustment at verification — HR accepts the requested hours or rejects;
  a change means reject and re-request.
- Editing a submitted request; re-opening a `REJECTED`/`WITHDRAWN` one
  (re-request instead).
- Any API path to `INCLUDED_IN_PAYROLL` — inclusion is payroll's accounting
  flip inside the finalize transaction (ADR 0018), not an overtime operation.
- An `overtime.included_in_payroll` event — ADR 0018 decision 6: the flip is a
  payroll accounting fact and payroll never emits `overtime.*`.
- Notification delivery of overtime events — ADR 0015's trigger map v1 covers
  five kinds only; adding overtime triggers is a follow-up extension of that
  map, not this feature.
- Realtime/live-wall deltas — overtime does not change presence state.
- Attendance correlation (e.g. requiring a closed attendance record on
  `work_date`), shift-awareness, weekly/daily hour-cap policy config, planned
  (future) overtime, recurring overtime, bulk import.
- Overtime rate configuration — `overtime_multiplier` already lives in payroll
  config (`payroll.md`).

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/overtime/` (models · schemas · service · repository · events · policies · exceptions) |
| API router | `apps/api/app/api/v1/overtime.py` |
| Seam adapter | `apps/api/app/infrastructure/postgres/` (`OvertimeHoursPort` impl; wired in `app/dependencies.py`, replacing `NoOvertimeHoursPort`) |
| Events | `contracts/events/overtime/` (`requested` · `approved` · `verified` · `rejected` · `withdrawn`, v1) |
| Workers | — (the payroll worker consumes via the port; no overtime worker) |
| Web feature | `apps/web/features/overtime/` |
| Web routes | `apps/web/app/(dashboard)/overtime/` |
| API tests | `apps/api/tests/unit` · `tests/integration` · `tests/contract` |
| E2E | `tests/e2e/overtime.spec.ts` (+ the payroll `full-hop.sh` reset extended for overtime rows) |

## Roles & access

| Capability | Employee | Manager | HR | Finance | Admin | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| Create request / view own requests | own | own | own | own | own | own |
| View team requests | — | team | Y | — | Y | Y |
| Approve / reject (manager step, from `REQUESTED`) | — | team | Y | — | Y | Y |
| Verify / reject (HR step, from `MANAGER_APPROVED`/`HR_VERIFIED`) | — | — | Y | — | Y | Y |
| Withdraw own open request | own | own | own | own | own | own |

Out-of-scope viewers get `404` (existence is not leaked); a viewer with standing
who attempts a forbidden transition or acts on their own request gets `403`.
"Team" scope is the same predicate `leave.md` uses.

## API contract (final — pinned by T1, 2026-10-22)

| Method & path | operationId (orval hook) | Input | Response |
| --- | --- | --- | --- |
| `POST /api/v1/overtime/requests` | `createOvertimeRequest` | `OvertimeRequestCreate` | `201 OvertimeRequest` (`requested`) |
| `GET /api/v1/overtime/requests` | `listOvertimeRequests` | `?employee_id=&status=&from=&to=&page=&page_size=` | `200 {"items": OvertimeRequest[], "page": Page}` |
| `GET /api/v1/overtime/requests/{id}` | `getOvertimeRequest` | — | `200 OvertimeRequest` |
| `POST /api/v1/overtime/requests/{id}/approve` | `approveOvertimeRequest` | `OvertimeDecision` | `200 OvertimeRequest` |
| `POST /api/v1/overtime/requests/{id}/verify` | `verifyOvertimeRequest` | `OvertimeDecision` | `200 OvertimeRequest` |
| `POST /api/v1/overtime/requests/{id}/reject` | `rejectOvertimeRequest` | `OvertimeDecision` | `200 OvertimeRequest` |
| `POST /api/v1/overtime/requests/{id}/withdraw` | `withdrawOvertimeRequest` | — | `200 OvertimeRequest` |

The seven operationIds above are the orval hook names and are pinned: the
router sets them as `operation_id` (T5) and the generated
`@workpulse/api-client` hooks must match exactly.

Query semantics (pinned):

- `from`/`to` filter `work_date` **inclusively** (`work_date >= from`,
  `work_date <= to`); either may be omitted.
- `employee_id` filters by employee; a foreign (other-tenant / unknown)
  `employee_id` → `404 employee_not_found`.
- `status` accepts one lowercase status value; JSON `status` values are
  lowercase everywhere
  (`requested|manager_approved|hr_verified|included_in_payroll|rejected|withdrawn`).
- Pagination is the house `PageParams` clamp shape (clamp, never an error).

Decision semantics (pinned):

- `OvertimeDecision.note` (1..500) routes to `manager_note` when the reject is
  from `requested` (manager step) and to `hr_note` from
  `manager_approved`/`hr_verified` (HR step); `approve` stores it in
  `manager_note`, `verify` in `hr_note`.
- `reject` stores `rejected_from` as the observed prior state
  (`requested|manager_approved|hr_verified`).

Pinned error codes (`409`/`404` responses carry these stable `code` values):

| Code | HTTP | Raised when |
| --- | --- | --- |
| `overtime_overlaps` | 409 | new request's `work_date` equals an open (`requested`/`manager_approved`/`hr_verified`) request of the same employee |
| `overtime_work_date_in_future` | 409 | `work_date` after the server date at submit |
| `overtime_not_requested` | 409 | `approve` from a non-`requested` state |
| `overtime_not_manager_approved` | 409 | `verify` from a non-`manager_approved` state |
| `overtime_not_open` | 409 | `withdraw` on `hr_verified` (payroll-bound); `reject`/`withdraw` on `included_in_payroll`, `rejected` or `withdrawn` (terminal) |
| `employee_not_active` | 409 | requester's employee record is not active |
| `overtime_request_not_found` | 404 | missing, cross-tenant or out-of-scope request id |
| `employee_not_found` | 404 | foreign/unknown `employee_id` list filter |

Field bounds (`hours` out of range or >2 dp, note/reason lengths) are `422`.

```jsonc
OvertimeRequest {
  "id", "tenant_id", "employee_id",
  "work_date": date,
  "hours": decimal,                 // 0 < hours <= 12, max 2 dp
  "status": "requested|manager_approved|hr_verified|included_in_payroll|rejected|withdrawn",
  "reason": string|null,            // PII — own requester + deciders only
  "manager_note": string|null,      // PII — same visibility rule
  "hr_note": string|null,           // PII — same visibility rule
  "approved_by": uuid|null,         // actor user id (ADR 0010)
  "approved_at": date-time|null,
  "verified_by": uuid|null,
  "verified_at": date-time|null,
  "rejected_by": uuid|null,
  "rejected_at": date-time|null,
  "rejected_from": "requested|manager_approved|hr_verified"|null,
  "withdrawn_at": date-time|null,
  "included_in_run_id": uuid|null,  // set by payroll's finalize flip (ADR 0018)
  "created_at", "updated_at"
}

OvertimeRequestCreate { "work_date": date, "hours": decimal, "reason"?: string }  // reason 1..500
OvertimeDecision      { "note"?: string }  // 1..500 → manager_note or hr_note by step
```

Dates are `YYYY-MM-DD`, timestamps UTC ISO 8601. `reason`/`manager_note`/
`hr_note` are never logged, never in event payloads, never in audit `changes`
values.

### Seam port (already pinned by ADR 0018)

`apps/api/app/domains/payroll/repository.py` owns the protocol; this feature
lands the real adapter only:

- `verified_hours(tenant_id, period)` → the `(overtime_id, employee_id, hours)`
  rows in `HR_VERIFIED` for the month, excluding already-included rows.
- `mark_included(tenant_id, rows, run_id)` → flips exactly `rows` to
  `INCLUDED_IN_PAYROLL` with `included_in_run_id`, `WHERE status =
  'HR_VERIFIED'`, idempotent for the same run, returning the flip count.

## Events

Contract before producer (AGENTS.md rule 4); produced through the `EventOutbox`
port in the same transaction as the transition (ADR 0012), relayed verbatim to
`workpulse.events`:

| `event_type` | Emitted when | `data` payload |
| --- | --- | --- |
| `overtime.requested` | submit to `REQUESTED` | `overtime_id`, `employee_id`, `work_date`, `hours` |
| `overtime.approved` | manager approves | `overtime_id`, `employee_id`, `work_date`, `hours`, `approved_by` |
| `overtime.verified` | HR verifies | `overtime_id`, `employee_id`, `work_date`, `hours`, `verified_by` |
| `overtime.rejected` | reject from any open state | `overtime_id`, `employee_id`, `work_date`, `hours`, `rejected_by`, `rejected_from` |
| `overtime.withdrawn` | own withdraw | `overtime_id`, `employee_id`, `work_date` |

No notes text — identifiers, the work date, hours and state only. The payroll
flip emits **no** event (ADR 0018 decision 6). `.ai/domain.md`'s event catalog
row (`overtime.requested` / `overtime.approved`) is updated to these five types
in the implementing change.

## Edge cases

- Approve from a non-`REQUESTED` state → `409 overtime_not_requested`; verify
  from a non-`MANAGER_APPROVED` state → `409 overtime_not_manager_approved`.
- Withdraw an `HR_VERIFIED` row, or reject/withdraw an `INCLUDED_IN_PAYROLL`,
  `REJECTED` or `WITHDRAWN` row → `409 overtime_not_open` (withdraw is
  pre-verification only; inclusion is terminal). Rejecting an `HR_VERIFIED`
  row is the un-verify path below — allowed for HR-step roles.
- A requester approving/rejecting/verifying their own request → `403`, even for
  HR/Admin; an `Employee`-role decide attempt → `403`; a manager without an HR
  role verifying → `403`.
- `work_date` after today → `409 overtime_work_date_in_future`; `hours ≤ 0`,
  `> 12`, or with more than 2 decimal places → `422`.
- Overlap with an open request on the same `work_date` → `409 overtime_overlaps`;
  against `REJECTED`/`WITHDRAWN` → allowed. Two `INCLUDED_IN_PAYROLL` rows on
  one date are allowed and paid as separate claims (each counted once).
- Two concurrent approves (or two verifies) of the same request → exactly one
  transition; the other gets the wrong-state `409`.
- A row un-verified (`HR_VERIFIED ─► REJECTED`) after a run's calculation but
  before its finalize → that finalize rolls back (ADR 0018); the run must be
  recalculated. No silent partial pay.
- A correction run pays only rows not already `INCLUDED_IN_PAYROLL`; a row
  included by an earlier finalized run is never paid again.
- Cross-tenant request id → `404`; a manager of another team reading a request
  detail → `404`.
- A terminated employee submitting → `409 employee_not_active`.
- Notes/`reason` appearing anywhere outside the API response bodies (logs,
  events, audit values) is a PII bug.

## Acceptance criteria

- [ ] API: submitting a request returns `201` in `requested` with the stored `hours`, writes an audit entry and produces a valid `overtime.requested` envelope.
- [ ] API: manager approve → `manager_approved` + `overtime.approved`; HR verify → `hr_verified` + `overtime.verified`; notes are stored but absent from both payloads.
- [ ] API: reject from `REQUESTED`, `MANAGER_APPROVED` and `HR_VERIFIED` each land in `rejected` with the right `rejected_from` and produce `overtime.rejected`; own withdraw produces `overtime.withdrawn`.
- [ ] API (seam): the real adapter's `verified_hours` returns exactly the `hr_verified` rows for the period excluding included ones, and `mark_included` flips exactly those rows to `included_in_payroll` with `included_in_run_id`; a flip of the same rows for a different `run_id` flips 0 rows (at-most-one-finalized-run guard, ADR 0018 decision 4).
- [ ] API (failure): future `work_date` → `409 overtime_work_date_in_future`; same-date open overlap → `409 overtime_overlaps`; approve from the wrong state → `409 overtime_not_requested`; verify from the wrong state → `409 overtime_not_manager_approved`; withdraw on `hr_verified`, and reject/withdraw on `included_in_payroll` or other terminal rows, → `409 overtime_not_open`; out-of-bounds `hours` → `422`.
- [ ] API (denied role): an `Employee`-role approve or verify attempt → `403`; a manager without an HR role verifying → `403`; self-approval/self-verification → `403` even for HR/Admin.
- [ ] API (tenant isolation): tenant B sees none of tenant A's requests (assert `404`/empty).
- [ ] Contracts: the five `overtime.*` v1 schemas exist, validate produced payloads, and contain no `reason`/`manager_note`/`hr_note` text.
- [ ] Unit tests use an injected clock — no wall-time assertions (date rules take `now` as the parameter).
- [ ] Payroll regression: with zero verified rows the real adapter keeps payroll's overtime term at `0.00`, and no payroll file changes (the `NoOvertimeHoursPort` swap is wiring-only).
- [ ] Web: an employee submits a request from the overtime page and sees it in "My requests" as `requested`; withdraw works while it is open.
- [ ] Web: a manager sees their team's `requested` overtime and can approve/reject; a self-action attempt shows the `403` error state.
- [ ] Web: HR sees the verification queue (`manager_approved` rows) and can verify/reject; the wrong-state and overlap refusals surface the pinned error messages.
- [ ] E2E: request → manager approve → HR verify shows the row `hr_verified` on both the employee's and HR's views without a manual refresh.
- [ ] E2E (seam): an `hr_verified` row appears in the payroll timesheet's `overtime_hours` for its month and flips to `included_in_payroll` after finalize (extend the payroll `full-hop.sh` reset for overtime rows).

## Decisions (PO, 2026-10-22)

Scope answers (feature kickoff):

1. **One workday + hours per request** — a single `work_date`, decimal hours,
   optional reason. One row = one payroll flip handle; simplest to verify and
   audit. (Range-based and per-day breakdowns rejected as heavier models with
   coarser verification.)
2. **Claims for hours already worked, bounded** — `work_date ≤ today`,
   `0 < hours ≤ 12`; same-employee/same-date open requests blocked
   (`409 overtime_overlaps`). Planned/future overtime and uncapped hours wait.
3. **HR verification is accept/reject only** — no hour adjustment; any change
   means reject and re-request. Keeps the verified figure identical to the
   requested one and the audit trail two-sided.
4. **Own withdrawal before verification** — from `REQUESTED`/`MANAGER_APPROVED`
   only; `HR_VERIFIED` is payroll-bound. Mirrors leave's withdraw-from-pending.
5. **Manager approves, HR verifies** — `MANAGER_APPROVED` by the team manager
   or HR/Admin/Super Admin; `HR_VERIFIED` by HR/Admin/Super Admin only; the
   requester is never a decider at either step (`403`), matching the security
   matrix and the domain machine's step names.
6. **Reject covers the un-verify window** (clarified 2026-10-22 at plan time):
   `reject` is allowed from `HR_VERIFIED` by HR-step roles, recording
   `rejected_from: hr_verified` — the transition ADR 0018's finalize rollback
   assumes. Withdraw stays pre-verification only; `INCLUDED_IN_PAYROLL` is
   terminal for both. This resolved an internal contradiction: AC5 and the
   edge-case bullet read "reject/withdraw on `hr_verified`/`included_in_payroll`
   → `409 overtime_not_open`", conflicting with the state machine, AC3 and ADR
   0018; both were reworded to "withdraw on `hr_verified`; reject/withdraw on
   `included_in_payroll`/terminal rows".

## Plan

Planned 2026-10-22. **Contract-first:** T1 pins the API contract in this spec,
T2 writes the five `overtime.*` event schemas and their pure builders before any
producer exists, the backend builds against them (T3 persistence → T4 domain →
T5 router + clients), T6 lands the ADR 0018 seam adapter and swaps the wiring,
the web (T7) consumes only the regenerated `@workpulse/api-client`, T8 lands the
e2e hops, T9 records. Every task ends with `make check` green.

**Plan-time clarifications (PO, 2026-10-22):**

1. **Reject covers the un-verify window** (`## Decisions` 6). `reject` is
   allowed from `HR_VERIFIED` (HR-step roles, `rejected_from: hr_verified`) —
   the transition ADR 0018's finalize rollback assumes. Withdraw is
   pre-verification only; `INCLUDED_IN_PAYROLL`/`REJECTED`/`WITHDRAWN` are
   terminal for reject and withdraw (`409 overtime_not_open`). AC5 and the
   edge-case bullet were reworded accordingly.
2. **Notes are not redacted per viewer.** `reason`/`manager_note`/`hr_note` are
   returned in full to anyone with row visibility (own / team / all — every
   viewer is either the requester or a decider, which is exactly the PII rule's
   audience). Redaction applies to logs, event payloads and audit `changes`
   values only.

### Acceptance criteria → where they are satisfied

| AC | Landed by | Proof |
| --- | --- | --- |
| Submit → `201 requested` + audit + valid `overtime.requested` | T3–T5 | `tests/integration/test_overtime_requests.py` |
| Approve → `manager_approved` + `overtime.approved`; verify → `hr_verified` + `overtime.verified`; notes absent from payloads | T2, T4, T5 | `test_overtime_requests.py` + `tests/contract/test_overtime_events.py` |
| Reject from all three open states → `rejected` + right `rejected_from` + `overtime.rejected`; own withdraw → `overtime.withdrawn` | T4, T5 | `test_overtime_requests.py` |
| Seam adapter: `verified_hours` exact, `mark_included` flips exactly those rows, different `run_id` flips 0 | T6 | `tests/integration/test_overtime_seam.py` |
| Failure codes (future date · overlap · wrong-state ×2 · not-open · `hours` 422) | T4, T5 | `tests/unit/overtime/test_service.py` + `test_overtime_requests.py` |
| Denied roles: `Employee` decide → 403; manager w/o HR verifying → 403; self-decide → 403 | T4, T5 | `tests/unit/overtime/test_policies.py` + `tests/integration/test_overtime_roles.py` |
| Tenant isolation → 404/empty | T5 | `tests/integration/test_overtime_tenant_isolation.py` |
| Five v1 schemas validate payloads, no notes text | T2 | `tests/contract/test_overtime_events.py` |
| Injected clock everywhere | T4 (convention) | `tests/unit/overtime/conftest.py` fixed `NOW`; no wall-time asserts |
| Payroll regression: zero rows → `0.00`, no payroll file changes | T6 | `test_overtime_seam.py` (wiring diff in `app/dependencies.py` only) |
| Web: submit → "My requests" `requested`; withdraw works open | T7, T8 | `apps/web/tests/overtime/` + `tests/e2e/overtime.spec.ts` |
| Web: manager inbox approve/reject; self-action 403 state | T7 | `apps/web/tests/overtime/manager-inbox` |
| Web: HR queue verify/reject; wrong-state + overlap refusals surface pinned messages | T7 | `apps/web/tests/overtime/hr-verification` + `errors` |
| E2E: request → approve → verify shows `hr_verified` on both views without refresh | T8 | `tests/e2e/overtime.spec.ts` |
| E2E seam: `hr_verified` row in timesheet `overtime_hours`, flips after finalize | T8 | payroll seam hop (`tests/e2e/payroll.spec.ts`) + `full-hop.sh` reset |

### T1 — Pin the API contract (docs only)

Finalize `## API contract` in this spec: header → "final — pinned by T1,
2026-10-22"; pin the seven operationIds (orval hook names): `createOvertimeRequest`,
`listOvertimeRequests`, `getOvertimeRequest`, `approveOvertimeRequest`,
`verifyOvertimeRequest`, `rejectOvertimeRequest`, `withdrawOvertimeRequest`;
pin the error codes: 409s `overtime_overlaps`, `overtime_work_date_in_future`,
`overtime_not_requested`, `overtime_not_manager_approved`, `overtime_not_open`,
`employee_not_active`; 404s `overtime_request_not_found` (missing, cross-tenant
or out-of-scope id) and `employee_not_found` (foreign `employee_id` list
filter); pin that JSON `status` values are lowercase, `OvertimeDecision.note`
routes to `manager_note` when the reject is from `requested` and to `hr_note`
from `manager_approved`/`hr_verified`, reject stores `rejected_from`, and the
list `from`/`to` filter `work_date` inclusively. No code.

Verify: docs diff only; `make check` unaffected (first full run at T2).

### T2 — Event contracts + pure builders (contract before producer)

- `contracts/events/overtime/overtime-{requested,approved,verified,rejected,withdrawn}.v1.json`
  — JSON Schema draft 2020-12 mirroring `contracts/events/leave/leave-*.v1.json`
  (`$id`, `additionalProperties: false`, pinned `required`); payloads exactly the
  Events table; every payload carries `data.employee_id` (the Kafka key —
  `EventEnvelope` in `app/shared/outbox.py` requires it) plus `overtime_id`,
  `work_date`, `hours` (and `approved_by`/`verified_by`/`rejected_by`+
  `rejected_from` where the table lists them); no notes keys anywhere.
- `apps/api/app/domains/overtime/{__init__.py,events.py}` — five pure builders +
  `_envelope(...)` mirroring `domains/leave/events.py` (envelope key is
  `version: 1`; `actor_id` is the actor **user** id, ADR 0010; `occurred_at`
  RFC 3339 UTC). Builders take plain values — no ORM imports.
- `apps/api/tests/contract/test_overtime_events.py` — mirror
  `tests/contract/test_leave_events.py`: `Draft202012Validator` over
  `glob("overtime-*.v1.json")`, envelope + payload validation, PII key sweep
  (`reason|manager_note|hr_note|note` must never appear), schema↔builder parity,
  filename↔event_type mirror, Kafka-key presence, `EventEnvelope` write gate,
  exact pinned key-sets, negative schema cases.
- `.ai/domain.md` — event catalog row `overtime.requested` / `overtime.approved`
  → the five types; `**Overtime**` state machine += `WITHDRAWN` (withdraw arrow
  from `REQUESTED`/`MANAGER_APPROVED`) to match this spec.

Verify: `make check-api` green (contract tests pass with builders only; no
producer yet).

### T3 — Persistence: models · migration 0014 · repositories

- `apps/api/app/domains/overtime/models.py`: `OvertimeRequest`
  (`overtime_requests`): `id` (uuid7 PK), `tenant_id`, `employee_id` (FK
  `employees.id`), `work_date` (Date), `hours` (`Numeric(4, 2)` — ADR 0017, no
  floats), `status` (`String(20)`, lowercase values), `reason` /
  `manager_note` / `hr_note` (`String(500)`, nullable — PII), `approved_by` /
  `approved_at`, `verified_by` / `verified_at`, `rejected_by` / `rejected_at`,
  `rejected_from` (`String(20)`, nullable — `requested|manager_approved|hr_verified`),
  `withdrawn_at`, `included_in_run_id` (nullable bare UUID — **no FK** across
  the seam boundary, ADR 0018), `created_at` / `updated_at` (leave's timestamp
  pattern). CheckConstraints: `hours > 0 AND hours <= 12`, `status` enum,
  `rejected_from` enum-or-NULL. Indexes: `(tenant_id, employee_id, status,
  work_date)` for overlap/visibility, and a partial covering index on
  `(tenant_id, work_date)` `postgresql_where=text("status = 'hr_verified'")`,
  `postgresql_include=["employee_id", "hours"]` for the seam read — declared in
  **both** the migration and `__table_args__` (parity rule).
- Migration `apps/api/alembic/versions/0014_overtime.py`
  (`revision="0014", down_revision="0013"` — confirm the head at build time);
  add `app.domains.overtime.models` to `alembic/env.py`.
- `apps/api/app/domains/overtime/repository.py` — Protocol-only (every method
  takes `tenant_id` first, attendance/leave header rule):
  `employee_facts(tenant_id, employee_id)` (minimal id/status/manager_id —
  infra joins `employees`, leave's `EmployeeFacts` shape),
  `visible_employee_ids(tenant_id, manager_id)` (self + direct reports),
  `find_open_overlap(tenant_id, employee_id, work_date)` (states
  `requested|manager_approved|hr_verified` only), `create_request`, `get_request`,
  `list_requests(filters, page)` (filters: employee_id, status, from, to), and
  conditional `transition_approve/verify/reject/withdraw(...)` updates that
  return the row or `None` when the state guard lost the race.
- `apps/api/app/infrastructure/postgres/repositories/overtime.py` — Postgres
  impl (tenant condition first, `_map_unique_violation` style); the reject
  update's WHERE covers the three from-states and writes `rejected_from` from
  the observed prior state in the same statement where possible (else read-then-
  conditional-update with the rowcount guard).
- `apps/api/tests/integration/conftest.py` — TRUNCATE list += `overtime_requests`.

Verify: `make check-api` green (the harness migrates; existing tests
unaffected).

### T4 — Overtime domain: exceptions · policies · schemas · service · unit tests

- `exceptions.py` — one `WorkPulseError` subclass per pinned code
  (`attendance/exceptions.py` style): `OvertimeOverlapsError`,
  `OvertimeWorkDateInFutureError`, `OvertimeNotRequestedError`,
  `OvertimeNotManagerApprovedError`, `OvertimeNotOpenError`,
  a local `EmployeeNotActiveError` (409 `employee_not_active` — per-domain copy
  convention), `OvertimeRequestNotFoundError` (404), `EmployeeNotFoundError`
  (404).
- `policies.py` — the two step gates: `ensure_can_approve(identity)` (roles
  {MANAGER, HR, ADMIN, SUPER_ADMIN}) and `ensure_can_verify(identity)` ({HR,
  ADMIN, SUPER_ADMIN}); reject uses the manager-step gate from `requested` and
  the HR-step gate from `manager_approved`/`hr_verified`. Scope narrowing and
  the self-decision `403` stay in the service (leave/attendance pattern).
- `schemas.py` — `OvertimeRequestStatus` StrEnum (six lowercase values),
  `OvertimeRejectedFrom` StrEnum (three), `OvertimeRequest` (response shape per
  T1), `OvertimeRequestCreate` (`extra="forbid"`; `reason` 1..500 optional),
  `OvertimeDecision` (`note` 1..500 optional), `OvertimeRequestListPage`.
- `service.py` — `OvertimeService(repo, audit: AuditWriter, outbox: EventOutbox)`
  with `now` as the **last parameter** everywhere (router `_now()` is the only
  clock read):
  - `create_request`: fail closed when `identity.employee_id is None`
    (leave's behavior); `employee_facts` active check → `employee_not_active`;
    `work_date <= now.date()` → `overtime_work_date_in_future`;
    `find_open_overlap` → `overtime_overlaps`; insert + `overtime.requested` +
    audit.
  - `approve` / `verify` / `reject`: `_decidable` (self-decision 403 whatever
    the role; out-of-scope → 404) → step gate → conditional transition (pre-check
    raises the wrong-state code; a lost race post-UPDATE raises the same code —
    exactly one of two concurrent decides wins) → event + audit in the same
    transaction (ADR 0012). Reject routes `note` by step and records
    `rejected_from`.
  - `withdraw`: own-only, from `requested|manager_approved`; `hr_verified` /
    terminal → `overtime_not_open`.
  - `list_requests` / `get_request`: visibility — own for EMPLOYEE/FINANCE,
    `visible_employee_ids` for MANAGER, all for HR/ADMIN/SUPER_ADMIN;
    out-of-scope/foreign id → 404.
  - PII: `reason`/`manager_note`/`hr_note` never logged, never in event data,
    never in audit `changes` values (leave's `"changed"` marker).
- `apps/api/tests/unit/overtime/` — `fakes.py` (in-memory repo with conditional-
  update semantics, `RecordingAuditWriter`, `RecordingEventOutbox`), `conftest.py`
  (fixed `NOW = datetime(...)`, identity factory), `test_service.py` (whole
  machine + every pinned code + boundary `hours`/dates + concurrent-winner
  semantics), `test_policies.py` (parametrized allow/deny like
  `tests/unit/attendance/test_policies.py`).

Verify: `make check-api` green.

### T5 — Router · integration tests · regenerate clients

- `apps/api/app/api/v1/overtime.py` — seven thin routes (identity → policy →
  service; `_now()` once; `operation_id`s per T1; `from_` aliased to `from`;
  `PageParamsDep`; response models per the contract: create `201`, decisions and
  withdraw `200`); register in `app/api/v1/__init__.py`
  (`api_router.include_router(overtime.router, tags=["overtime"])`).
- `apps/api/tests/integration/`:
  - `test_overtime_requests.py` — happy paths per transition with **audit-action
    + outbox-envelope assertions**, `rejected_from` per source state, note
    storage vs payload absence, overlap rules (open states block;
    `rejected`/`withdrawn`/`included_in_payroll` do not), list filters + clamp
    pagination, concurrent double-decide → one wins, one `409`.
  - `test_overtime_roles.py` — `Employee` approve/verify → 403; manager without
    an HR role verify → 403; self-approve/self-verify/self-reject → 403 even for
    HR/Admin; manager outside the team → 404; reject role split by source state.
  - `test_overtime_tenant_isolation.py` — mirror
    `test_leave_tenant_isolation.py` (B sees 404/empty; cross-tenant id → 404).
- `make gen-api` (export OpenAPI → orval). The prettier formatter churn is
  already resolved (leave T6); expect only new overtime hooks in
  `packages/api-client/`.

Verify: `make check-api && make check-web` green.

### T6 — Seam adapter · wiring · guard + seam tests (ADR 0018)

- `apps/api/app/infrastructure/postgres/payroll_facts.py` +=
  `PostgresOvertimeHoursPort` (mirrors `PostgresAttendanceHoursPort` /
  `PostgresLeaveDaysPort`: session adapter, imports `app.domains.overtime.models`
  under an alias, returns payroll's wire shape; the payroll domain stays
  untouched):
  - `verified_hours(tenant_id, period)` — rows with `status = 'hr_verified'`
    (stored lowercase; ADR 0018's `'HR_VERIFIED'` is enum notation) whose
    `work_date` falls inside the `YYYY-MM` month, deterministic order →
    `OvertimeHoursRow(overtime_id, employee_id, hours)`.
  - `mark_included(tenant_id, rows, run_id)` — UPDATE exactly `rows` `WHERE
    status = 'hr_verified'` → `included_in_payroll` + `included_in_run_id`;
    rows already marked under the **same** `run_id` count as idempotent
    successes (replay returns the full count, no-op on state); rows under a
    different `run_id` or another state count 0. Returns the count so payroll's
    `flipped == len(rows)` check holds exactly when every row is counted by
    `run_id` — the at-most-one-finalized-run guard (ADR 0018 decision 4).
- `apps/api/app/dependencies.py` — `get_overtime_hours_port` returns
  `PostgresOvertimeHoursPort(session)`. **Wiring-only**; `domains/payroll/**`
  and every other payroll file unchanged.
- `apps/api/tests/integration/test_overtime_seam.py`:
  1. `verified_hours` returns exactly the period's `hr_verified` rows,
     excluding included ones (and out-of-month rows);
  2. `mark_included` flips exactly those rows with `included_in_run_id`;
  3. a second flip of the same rows for a **different** `run_id` flips 0 rows
     (guard test); same-`run_id` replay is idempotent;
  4. zero verified rows keep payroll's overtime term at `0.00` (regression);
  5. verified rows → timesheet `overtime_hours` → finalize flips them (the
     scope's seam integration test, through the real `PayrollService` with the
     real adapter);
  6. a row un-verified (`hr_verified → rejected`) after calculation shortens the
     flip count → finalize rolls back (`PayrollRunStaleError` path).

Verify: `make check-api` green — payroll's own suite must pass unmodified.

### T7 — Web: overtime feature · route · nav

- `apps/web/features/overtime/` mirroring `features/leave/`: `types.ts`
  (re-exports generated types), `api.ts` (query keys + invalidation helpers),
  `hooks/use-overtime.ts` (query wrappers, mutation hooks with toast +
  invalidation, and `OVERTIME_ERROR_MESSAGES: Record<string, string>` mapping
  every pinned `code` — the `features/payroll/labels.ts` dictionary pattern, no
  raw code reaches a toast), `schema.ts` (zod: calendar `YYYY-MM-DD`, hours
  `> 0 && <= 12` with at most 2 decimals, reason/note 1..500),
  `labels.ts` (status labels + Badge variants (`requested`→warning,
  `manager_approved`→default/secondary, `hr_verified`→success,
  `included_in_payroll`→muted, `rejected`→destructive, `withdrawn`→muted) +
  `OVERTIME_MANAGER_STEP_ROLES` / `OVERTIME_HR_STEP_ROLES` mirrored from
  `policies.py`), `dates.ts` (`todayISO()` etc.).
- Components: `MyOvertimeTable` (filters, empty/spinner/error states, paged
  table, withdraw on `requested|manager_approved` rows), `OvertimeRequestDialog`
  (rhzf+zod; field-level server errors + root `409` states for
  `overtime_overlaps` / `overtime_work_date_in_future`),
  `ManagerInbox` (team `requested` rows → approve/reject via a shared
  `DecisionDialog`), `HrVerificationQueue` (`manager_approved` rows →
  verify/reject), each hidden unless `useAuth().user.role` matches its role set
  (leave's `TeamApprovals` render-nothing gate).
- Live updates without reload: mutation invalidation (leave pattern) **plus**
  conditional `refetchInterval` polling on the list queries — `use-payroll-runs`
  precedent (`PAYROLL_POLL_INTERVAL = 2500`, `false` when nothing is open).
  No websocket: realtime deltas are out of scope; the e2e "no manual refresh" AC
  rides this polling.
- `apps/web/app/(dashboard)/overtime/page.tsx` (Server Component composing
  `PageHeader` + the three sections) and `components/destinations.ts`
  `NAV_ITEMS` += `/overtime` "Overtime" entry.
- `apps/web/tests/overtime/` — `my-overtime`, `manager-inbox`,
  `hr-verification`, `overtime-request-dialog`, `errors`, `schema` (vi.mock the
  generated hooks + `useAuth`, RTL through a QueryClient wrapper).

Verify: `make check-web` green (typecheck + lint + vitest).

### T8 — E2E: workflow · payroll seam hop · fixture reset

- `tests/e2e/overtime.spec.ts` (leave.spec.ts mechanics: per-role contexts via
  `loggedIn`, `waitForResponse` on the exact POST, seeded users from
  `tests/e2e/auth.ts`): employee submits → row `requested` without reload;
  manager approves; HR verifies → row `hr_verified` visible on **both** the
  employee's and HR's views without a manual refresh (polling; assert with a
  bounded wait, not `reload()`); a self-action attempt shows the 403 error
  state; the overlap and wrong-state refusals surface the pinned messages.
- Payroll seam hop inside `tests/e2e/payroll.spec.ts`'s serial flow (it owns the
  one-open-run-per-period lifecycle, so the hop cannot fight overtime.spec.ts
  over a finalized run): seed one `hr_verified` row for the current period
  (API contexts: submit → approve → verify) with fixed hours, assert the payroll
  timesheet's `overtime_hours` includes it, finalize, assert the row flips to
  `included_in_payroll` with `included_in_run_id` set. Exact-number assertions
  in that spec account for the seeded hours; the hop starts from its own cleanup
  so rows from other specs cannot leak into payroll totals.
- `tests/e2e/full-hop.sh` += `reset_overtime_fixtures` (scoped delete of the
  seeded worker's `overtime_requests`, `reset_leave_fixtures` style) called
  before the workers start.

Verify: `make check-e2e` (free :3000/:5433/:6379 and `make kafka-up` first),
then the full `make check`.

### T9 — Records

After the final green `make check`: tick every acceptance criterion in this
spec, set `Status: done`, move the backlog row in `.ai/features/README.md` to
`done`, and tick the overtime line in `tasks/TODO.md`. No new ADR is required —
the seam (0018), outbox (0012), actor ids (0010) and decimal discipline (0017)
are already pinned; `## Decisions` 6 carries the un-verify clarification. A
future notification-trigger extension for `overtime.*` is an ADR 0015 follow-up,
out of scope here.

### Test strategy

| Suite | Files (new) | Gate | Failure cases covered |
| --- | --- | --- | --- |
| Contract | `apps/api/tests/contract/test_overtime_events.py` | `make check-api` | payload/schema drift, PII keys in payloads, missing `employee_id` Kafka key, filename↔event_type mismatch |
| Unit | `apps/api/tests/unit/overtime/{fakes,conftest,test_service,test_policies}.py` | `make check-api` | future `work_date`, overlap (open states block, terminal states don't), `hours` bounds/precision, every wrong-state 409, `overtime_not_open`, `employee_not_active`, self-decision 403, step-role denial, concurrent double-decide single-winner; injected clock only |
| Integration | `apps/api/tests/integration/{test_overtime_requests,test_overtime_roles,test_overtime_tenant_isolation,test_overtime_seam}.py` | `make check-api` | audit + outbox per transition, `rejected_from` per source state, denied roles (403), out-of-scope 404, tenant B isolation, seam read/flip exactness, at-most-one-run guard (0 flips for a second `run_id`), zero-row payroll `0.00` regression, un-verify → finalize rollback |
| Web | `apps/web/tests/overtime/{my-overtime,manager-inbox,hr-verification,overtime-request-dialog,errors,schema}.test.*` | `make check-web` | submit/withdraw states, role-gated sections hidden, 403 self-action state, pinned error messages (overlap/wrong-state), schema bounds |
| E2E | `tests/e2e/overtime.spec.ts`, payroll seam hop in `tests/e2e/payroll.spec.ts`, `full-hop.sh` reset | `make check-e2e` / `make check` | full approval chain live on two views without reload, self-action 403, timesheet `overtime_hours` + finalize flip |

### Risks, unknowns and ADR notes

- **E2E cross-spec coupling** — payroll totals must not absorb overtime rows
  from other specs. The seam hop cleans its own fixture first and runs inside
  payroll's serial flow; `reset_overtime_fixtures` clears the seeded worker's
  rows at session start. If payroll.spec.ts has exact-amount assertions, extend
  them by the seeded hours when landing T8.
- **"Without a manual refresh" is polling, not realtime** — cross-user visibility
  lags up to the poll interval (~2.5 s). Sub-second cross-user updates would
  need a realtime/notifications extension (ADR 0015 trigger map + ADR 0013
  family) and stay out of scope.
- **Status case** — rows store lowercase values (JSON contract); ADR 0018's
  `WHERE status = 'HR_VERIFIED'` is enum notation — the adapter queries
  `hr_verified`. Keep enum values lowercase end to end.
- **Decimal hours on the wire** — FastAPI serializes `Decimal` per the existing
  schema conventions (payroll precedent); confirm the generated client types at
  T5's `make gen-api` and coerce in zod (`features/payroll/schema.ts` pattern).
  Never switch to floats (ADR 0017).
- **Reject role split by source state** is deliberate (manager-step from
  `requested`, HR-step from `manager_approved`/`hr_verified`) — both branches
  need tests (T5) or they will drift.
- **Migration head** may move if another feature lands first — confirm
  `down_revision` at T3.
- **No new ADR**: everything expensive to reverse is already pinned (ADR 0018
  seam + flip semantics, 0012 outbox, 0010 actor ids, 0017 decimals); the
  un-verify clarification is `## Decisions` 6.
