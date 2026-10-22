# Feature: Payroll

- **Status:** done
- **Phase:** 4
- **Size:** L
- **Surfaces:** api+web+events+workers
- **Links:** ADR 0003 (payroll is a deterministic simulation) · `.ai/domain.md` (Payroll context, Timesheet/PayrollRun/Payslip language, Overtime state machine, invariants 1–6) · `.ai/security.md` (roles matrix, PII, audit) · `tasks/doc.md` §8 · upcoming `overtime.md` (overtime workflow — **build order: overtime first, payroll second**) · `leave.md` (Decision 7 deferred working-day questions here) · ADR 0012 (outbox transport, amended for run-level keys) · ADR 0017 (money & rounding) · ADR 0018 (payroll↔overtime seam)

## Problem

Payroll inputs live everywhere and the calculation lives nowhere: attendance
hours, approved overtime, bonuses and deduction rules are assembled by hand,
and nobody can reproduce last month's numbers or explain a payslip line. The
product promises "payroll runs" as first-class facts (`.ai/product.md`) and
Finance's job to be done is "configure payroll, review disbursements, financial
audit" — none of which exists. This feature gives payroll a real home as a
**deterministic simulation** (ADR 0003): a closed monthly period is assembled
into timesheets, calculated into inspectable payslips line by line, finalized
immutably, and emitted as events. It is explicitly not a compliance engine —
UI and docs must say "simulated payroll" (ADR 0003 consequences).

## Business rules

- **Formula** (ADR 0003, deterministic):

  ```text
  Net = (Base Salary + Overtime Pay + Bonuses) − Deductions
  ```

  Component rules (all money is `decimal(12,2)`, each output line rounded
  half-up to 2dp; intermediates unrounded):

  - `Base Salary` = the employee's salary record with the greatest
    `effective_from` ≤ period end (see Salary records).
  - `Overtime Pay` = `verified_overtime_hours × hourly_rate × overtime_multiplier`,
    where `hourly_rate = base / standard_monthly_hours`. `standard_monthly_hours`
    (default `160`) and `overtime_multiplier` (default `1.50`) are tenant payroll
    config.
  - `Bonuses` = sum of per-run, per-employee bonus entries (see Bonuses).
  - `Deductions` = one line per active tenant deduction rule: `percent_of_base`
    → `round(base × value/100, 2)`; `fixed_amount` → `value` (see Deductions).
  - `net = base + overtime_pay + bonuses − Σ deduction_lines`.
- **Period** is a calendar month (`YYYY-MM`, e.g. `2026-09`); a run covers the
  whole month inclusive. A run may only be created once the period has ended
  (ADR 0003: "a job over a closed period") → `409 payroll_period_not_closed`.
- **Salary records** are per employee: `{amount, currency, effective_from}`,
  append-only history (multiple records per employee, one per
  `effective_from` date — a second record on the same date →
  `409 salary_effective_date_taken`). Maintained by Finance / Admin / Super
  Admin; HR has read-only view. `currency` comes from tenant payroll config
  (one currency per tenant).
- **Deduction lines** are tenant-level and inspectable (ADR 0003): `{code,
  name, kind: percent_of_base|fixed_amount, value, active}`. Every active line
  lands on every payslip of the run as its own line — no hidden tax tables.
  Managed by Finance / Admin / Super Admin. `value` for `percent_of_base` is
  `0.01..100`; for `fixed_amount` `>= 0.01` (else `422`).
- **Bonuses** are per-run, per-employee entries (`amount >= 0.01`, optional
  `note` 1..200 — PII). Entered by Finance / Admin / Super Admin while the run
  is not `FINALIZED`. A bonus for an employee with no payslip in the run →
  `409 payroll_employee_not_in_run`.
- **Run lifecycle** (every transition is an audit entry):

  ```text
  (create) ─► QUEUED ─► CALCULATING ─┬─► CALCULATED ─► FINALIZED (terminal)
                                     └─► FAILED ─(recalculate)─► QUEUED
  ```

  - Calculation is an **asynchronous background job** (`workers/payroll/`),
    never inside the request (ADR 0003). The create/recalculate endpoints
    return the run in `QUEUED`.
  - `CALCULATED` = provisional payslips (status `provisional`). `recalculate`
    is allowed from `CALCULATED`/`FAILED` and **replaces the provisional
    payslips atomically** on the same run id. Re-running unchanged inputs
    produces byte-identical results (determinism — the simulation's core
    promise).
  - `FINALIZED` is terminal and immutable: payslips flip to `issued`
    (`payslip.issued` per payslip), counted overtime flips to
    `INCLUDED_IN_PAYROLL` (domain state machine) via the overtime write port,
    and nothing on the run can be mutated (`409 payroll_run_finalized`).
    Finalize is allowed only from `CALCULATED` (`409 payroll_run_not_calculated`
    otherwise); concurrent finalizes: exactly one wins.
- **One open run per `(tenant, period)`**: creating a run while a non-finalized
  run exists for the period → `409 payroll_period_open`. After finalize, a new
  run for the same period is allowed and acts as a **correction run**: when it
  finalizes, the earlier run's payslips become `superseded` (still readable to
  their owner and to HR/Finance/Admin/Super Admin, excluded from "current").
- **Timesheets** are assembled at calculation time per `(employee, period)`:
  `worked_hours` = Σ `(check_out − check_in)` over closed attendance records
  whose check-in date falls in the period; `overtime_hours` = HR-verified
  overtime hours in the period (overtime seam below); `leave_days` = calendar
  days covered by `APPROVED` leave inside the period; `attendance_days` = count
  of closed records. Timesheets are **informational + the overtime input** —
  they never alter base pay (no proration, see Decisions 5).
- **Overtime seam** (upcoming `overtime.md`; same pattern as the attendance↔
  leave seam, ADR 0014): payroll reads verified overtime hours through an
  overtime **read port** and flips counted rows through an **write port** — it
  never imports overtime internals or touches overtime tables. Only rows in
  `HR_VERIFIED` count; each row is counted by **at most one `FINALIZED` run**
  (the flip at finalize enforces it — no double pay).
- **Run scope**: every employee whose employment overlaps the period and who
  has a salary record effective by period end. Mid-period hire or termination
  gets the **full** base (no proration). Employment overlap without a salary
  record → **skipped** with reason `no_salary`, listed in the run summary;
  skips never block calculation or finalize.
- **Roles** (security matrix: *Configure & run payroll — HR: view*): run
  create/recalculate/finalize, config, deduction lines, salary records and
  bonuses = Finance / Admin / Super Admin. HR reads runs, timesheets and all
  payslips. Every user reads **own** payslips. Managers have no team payroll
  visibility. Callers without standing get `404` (existence is not leaked);
  callers with standing attempting a forbidden transition get `403`.
- **Audit**: run create/recalculate/finalize, salary create, deduction-line
  changes, bonus create, supersession — all write audit entries. Amounts are
  PII: audit `changes` record "field changed", never values (`.ai/security.md`).
- **PII**: salary amounts, bonus amounts/notes, payslip lines and run totals
  never appear in logs, error messages or event payloads. Events carry
  identifiers, period and counts only.
- Tenant scoping in repositories and policies in
  `domains/payroll/policies.py` hold as everywhere else (invariants 1, 5).

## Scope

- Tenant payroll config (currency, `standard_monthly_hours`,
  `overtime_multiplier`) and tenant deduction lines (CRUD-lite).
- Per-employee salary records (append-only effective-dated history).
- Monthly payroll runs over closed periods: async calculation → timesheets +
  provisional payslips → finalize → issued payslips, with recalculation while
  open and a correction-run supersession path.
- Per-run bonuses.
- Events `payroll.calculated` / `payslip.issued` written first, produced through
  the `EventOutbox` port in the same transaction as the transition (ADR 0012).
- Web: a Finance/Admin payroll workspace (config, salaries, runs, timesheets,
  payslips) and an employee "My payslips" view.

## Out of scope

- The overtime request/approval/HR-verification workflow itself — its own spec
  (`overtime.md`, built **before** this one). This spec consumes its read port
  and flips counted rows via its write port only.
- Regional tax, statutory deductions, social insurance, regulatory reporting,
  compliance claims (ADR 0003) — deduction lines are transparent config, not
  jurisdiction logic. UI copy says "simulated payroll".
- Proration: mid-period hiring/termination, unpaid-leave deduction,
  working-day/holiday calendars (closes the loop on `leave.md` Decision 7 —
  working-day questions stay out; base is a fixed monthly amount).
- Retroactive adjustments beyond the correction-run path, multi-currency
  payouts, payment/disbursement execution ("review disbursements" stays a
  reporting concern), bank files, payslip PDF/documents (Phase 4+ documents
  feature).
- ClickHouse "payroll cost by department" analytics — this spec emits
  `payroll.calculated`; the Analytics feature projects it (Decisions 6).
- Notifications about payslips — the events exist for Notifications to consume;
  no delivery here.
- Editing or deleting a `FINALIZED` run or an issued payslip — immutability is
  the point.

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/payroll/` (models · schemas · service · repository · events · policies · exceptions) |
| API router | `apps/api/app/api/v1/payroll.py` |
| Events | `contracts/events/payroll/` (`payroll-calculated.v1.json` · `payslip-issued.v1.json`) |
| Workers | `workers/payroll/` (run job: `QUEUED` → `CALCULATING` → `CALCULATED`/`FAILED`) |
| Web feature | `apps/web/features/payroll/` |
| Web routes | `apps/web/app/(dashboard)/payroll/` |
| API tests | `apps/api/tests/unit` · `tests/integration` · `tests/contract` |
| E2E | `tests/e2e/payroll.spec.ts` |

## Roles & access

| Capability | Employee | Manager | HR | Finance | Admin | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| View own payslips | own | own | own | own | own | own |
| View runs, timesheets, all payslips | — | — | Y | Y | Y | Y |
| View salary records | — | — | Y | Y | Y | Y |
| Create / recalculate / finalize runs, enter bonuses | — | — | — | Y | Y | Y |
| Configure payroll config & deduction lines | — | — | — | Y | Y | Y |
| Create salary records | — | — | — | Y | Y | Y |

Out-of-scope viewers get `404`; a caller with standing attempting a forbidden
action gets `403` (e.g. HR finalizing a run, any user opening another
employee's payslip).

## API contract

| Method & path | operationId (orval hook) | Input | Response |
| --- | --- | --- | --- |
| `GET /api/v1/payroll/config` | `getPayrollConfig` | — | `200 PayrollConfig` |
| `PUT /api/v1/payroll/config` | `updatePayrollConfig` | `PayrollConfigUpdate` | `200 PayrollConfig` |
| `GET /api/v1/payroll/deductions` | `listPayrollDeductions` | — | `200 PayrollDeduction[]` (bare, unpaged) |
| `POST /api/v1/payroll/deductions` | `createPayrollDeduction` | `PayrollDeductionCreate` | `201 PayrollDeduction` |
| `PATCH /api/v1/payroll/deductions/{id}` | `updatePayrollDeduction` | `PayrollDeductionUpdate` | `200 PayrollDeduction` |
| `GET /api/v1/payroll/salaries` | `listSalaries` | `?employee_id=&page=&page_size=` | `200 {"items": Salary[], "page": Page}` |
| `POST /api/v1/payroll/salaries` | `createSalary` | `SalaryCreate` | `201 Salary` |
| `POST /api/v1/payroll/runs` | `createPayrollRun` | `PayrollRunCreate` | `201 PayrollRun` (`queued`) |
| `GET /api/v1/payroll/runs` | `listPayrollRuns` | `?period=&status=&page=&page_size=` | `200 {"items": PayrollRun[], "page": Page}` |
| `GET /api/v1/payroll/runs/{id}` | `getPayrollRun` | — | `200 PayrollRun` |
| `POST /api/v1/payroll/runs/{id}/recalculate` | `recalculatePayrollRun` | — | `200 PayrollRun` (`queued`) |
| `POST /api/v1/payroll/runs/{id}/finalize` | `finalizePayrollRun` | — | `200 PayrollRun` (`finalized`) |
| `GET /api/v1/payroll/runs/{id}/timesheets` | `listRunTimesheets` | `?page=&page_size=` | `200 {"items": Timesheet[], "page": Page}` |
| `GET /api/v1/payroll/runs/{id}/payslips` | `listRunPayslips` | `?page=&page_size=` | `200 {"items": Payslip[], "page": Page}` |
| `POST /api/v1/payroll/runs/{id}/bonuses` | `createRunBonus` | `BonusCreate` | `201 PayrollBonus` |
| `GET /api/v1/payroll/payslips` | `listMyPayslips` | `?page=&page_size=` | `200 {"items": Payslip[], "page": Page}` (own only) |
| `GET /api/v1/payroll/payslips/{id}` | `getPayslip` | — | `200 Payslip` (own, or HR/Finance/Admin/Super Admin) |

Pagination is the house `PageParams` clamp shape (out-of-range values clamp,
non-integers `422`). Pinned error codes for the mutating flows (`409` conflict
responses carry these stable `code` values): `payroll_period_not_closed`,
`payroll_period_open`, `payroll_run_not_calculated`, `payroll_run_finalized`,
`payroll_employee_not_in_run`, `salary_effective_date_taken`. JSON status
values are lowercase. Amount fields are JSON numbers with 2dp.

```jsonc
PayrollConfig {
  "tenant_id", "currency": "USD",              // one currency per tenant
  "standard_monthly_hours": 160.00,            // > 0
  "overtime_multiplier": 1.50,                 // > 0
  "updated_at"
}
PayrollConfigUpdate { "currency"?, "standard_monthly_hours"?, "overtime_multiplier"? }

PayrollDeduction {
  "id", "code": string, "name": string,
  "kind": "percent_of_base|fixed_amount", "value": decimal,
  "active": bool, "created_at", "updated_at"
}
PayrollDeductionCreate { "code", "name", "kind", "value", "active"?: bool }
PayrollDeductionUpdate { "name"?, "kind"?, "value"?, "active"? }

Salary { "id", "employee_id", "amount": decimal, "currency",
         "effective_from": date, "created_at", "created_by" }   // append-only
SalaryCreate { "employee_id", "amount": decimal, "effective_from": date }

PayrollRun {
  "id", "period": "YYYY-MM",
  "status": "queued|calculating|calculated|finalized|failed",
  "employee_count": int, "skipped_count": int,   // skips carry reason no_salary
  "skipped": [{ "employee_id", "reason": "no_salary" }],
  "totals": null | { "base", "overtime_pay", "bonuses", "deductions", "net" },
  "failed_code": string|null,                    // stable code, never PII
  "calculated_at": date-time|null, "finalized_at": date-time|null,
  "supersedes_run_id": uuid|null, "superseded_by_run_id": uuid|null,
  "created_by", "created_at", "updated_at"
}
PayrollRunCreate { "period": "YYYY-MM" }

Timesheet { "employee_id", "period", "worked_hours": decimal,
            "overtime_hours": decimal, "leave_days": int, "attendance_days": int }

Payslip {
  "id", "run_id", "employee_id", "period",
  "status": "provisional|issued|superseded",
  "base": decimal, "overtime_pay": decimal, "bonuses": decimal,
  "deductions": [{ "code", "name", "kind", "value", "amount" }],
  "net": decimal,
  "issued_at": date-time|null
}
PayrollBonus { "id", "run_id", "employee_id", "amount": decimal,
               "note": string|null, "created_by", "created_at" }
BonusCreate  { "employee_id", "amount": decimal, "note"?: string }
```

Amounts, run totals and bonus notes are PII: never logged, never in error
messages, never in event payloads or audit `changes` values.

## Events

Contract before producer (AGENTS.md rule 4); produced through the `EventOutbox`
port in the same transaction as the transition and relayed verbatim to
`workpulse.events` (ADR 0012):

| `event_type` | Emitted when | `data` payload |
| --- | --- | --- |
| `payroll.calculated` | a run reaches `CALCULATED` (job success) | `run_id`, `period`, `employee_count` |
| `payslip.issued` | finalize, one per payslip flipped to `issued` | `payslip_id`, `run_id`, `employee_id`, `period` |

Identifiers, period and counts only — **no amounts** (`.ai/domain.md` catalog
row + PII rule). Recalculation emits nothing (payslips stay provisional);
supersession emits nothing in v1 (audit covers it). `payroll.calculated` is the
first run-level event: its Kafka key is `data.run_id` (ADR 0012 amendment);
`payslip.issued` remains keyed by `data.employee_id`.

## Edge cases

- Run for a month that has not ended → `409 payroll_period_not_closed`.
- Second run for a period with an open (non-finalized) run →
  `409 payroll_period_open`; after finalize the correction run is allowed.
- Finalize from `queued`/`calculating`/`failed` → `409 payroll_run_not_calculated`.
- Recalculate / bonus create / anything mutating on a `finalized` run →
  `409 payroll_run_finalized`.
- Concurrent finalize of the same run → exactly one transition; the other
  `409 payroll_run_not_calculated`.
- Employee in scope without a salary record → skipped (`no_salary`, listed in
  `skipped`), no payslip; run still calculates and finalizes.
- Employee hired or terminated mid-period → full base (no proration).
- Two salary records for the same employee with the same `effective_from` →
  `409 salary_effective_date_taken`; salary amount `< 0.01` or future-only
  history → `422`.
- Bonus for an employee outside the run → `409 payroll_employee_not_in_run`.
- `percent_of_base` value outside `0.01..100` → `422`; inactive deduction line
  appears on no payslip.
- Recalculate after a new bonus/salary landed → payslips replaced in full;
  unchanged inputs → identical payslip set (determinism).
- The job crashes mid-run → `failed` with a stable `failed_code`; recalculate
  re-queues; a re-delivered/duplicate job pass never duplicates payslips
  (idempotent on run state).
- A counted overtime row already `INCLUDED_IN_PAYROLL` is never counted again —
  a second finalize for the same period (correction run) pays only hours not
  yet included; `included_in_run_id` on the overtime row records where it paid.
- Cross-tenant run/payslip/config id → `404`; an employee opening another
  employee's payslip → `404`.
- Superseded payslips remain readable (marked `superseded`) to their owner and
  HR/Finance/Admin/Super Admin; `listMyPayslips` includes them.
- Rounding: each line half-up 2dp; a line-amount sum must equal the run
  `totals` to the cent (test).

## Acceptance criteria

- [x] API: creating a run for a closed period returns `201` `queued`; the worker calculates it to `calculated` with deterministic payslips — base from the effective salary record, `overtime_pay = hours × (base/standard_monthly_hours) × multiplier`, bonus entries summed, one line per active deduction, `net = base + overtime_pay + bonuses − Σ deductions` — and `totals` equal the Σ of payslip lines to the cent.
- [x] API: finalize moves the run to `finalized`, flips payslips to `issued`, writes audit entries, flips counted overtime rows to `INCLUDED_IN_PAYROLL` exactly once, and produces one valid `payroll.calculated` envelope (at calculate) plus one `payslip.issued` envelope per payslip — payloads contain no amounts.
- [x] API: recalculate an open run replaces the provisional payslips atomically; recalculating with unchanged inputs yields byte-identical payslips (determinism).
- [x] API: a correction run for a finalized period supersedes it on finalize — the earlier payslips become `superseded` and are excluded from "current" while remaining readable.
- [x] API (failure): a run for a month that has not ended → `409 payroll_period_not_closed`; a second open run for the same period → `409 payroll_period_open`; finalize of a non-`calculated` run → `409 payroll_run_not_calculated`; any mutation of a `finalized` run → `409 payroll_run_finalized`.
- [x] API (failure): an in-scope employee without a salary record is skipped with reason `no_salary` in the run summary and gets no payslip; the run still calculates and finalizes.
- [x] API (denied role): an `Employee`- or `Manager`-role attempt to create a run, write config, or create a salary/bonus → `403`; an `HR`-role finalize attempt → `403`; an employee fetching another employee's payslip → `404`.
- [x] API (tenant isolation): tenant B sees none of tenant A's runs, payslips, salaries, deductions or config (assert `404`/empty).
- [x] Contracts: the `payroll.calculated` / `payslip.issued` v1 schemas exist, validate produced payloads, and contain no amounts or salary values.
- [x] Unit tests use an injected clock and a fixed `standard_monthly_hours`/multiplier — no wall-time assertions; rounding is asserted at the cent.
- [x] Workers: a queued run is picked up by `workers/payroll/` and ends `calculated` (or `failed` with a stable code on job failure, recoverable via recalculate); a duplicate job pass produces no second payslip set.
- [x] Web: Finance configures deduction lines and a salary record, triggers a run, sees it move to `calculated`, reviews timesheets and payslip lines, and finalizes.
- [x] Web: an employee sees only their own payslips in "My payslips" (issued + superseded marked); another employee's payslip id renders the not-found state.
- [x] Web (failure): finalize is unavailable while the run is `queued`/`calculating`/`failed`, and a `409` on any mutation surfaces the mapped error message (toast), not a raw code.
- [x] E2E: config → salary → run → calculated → finalize → the employee's payslip view shows the issued payslip with the expected net.

## Decisions (PO, 2026-10-21)

Feature kickoff answers:

1. **Overtime is its own spec, built first.** The ADR 0003 formula's overtime
   term consumes only HR-verified overtime through a read/write port
   (attendance↔leave seam pattern, ADR 0014); the request → manager approve →
   HR verify workflow lives in `overtime.md`. Payroll stays a pure calculator.
   Build order: `overtime.md` → `payroll.md`.
2. **Period = calendar month** (`YYYY-MM`), one run per tenant per period.
   Deterministic and matches fixed monthly salary semantics.
3. **Calculate → finalize lifecycle with recalculate while open.** Provisional
   payslips until finalize; finalize is terminal; a finalized period is fixed
   by a new correction run that supersedes on finalize. Matches ADR 0003's
   "re-running a period produces the same result unless its inputs changed".
4. **Money inputs: salary records + per-run bonuses + tenant deduction lines.**
   Effective-dated append-only salary history; bonuses typed per run;
   transparent configurable deduction lines (percent-of-base or fixed); one
   currency per tenant.
5. **No proration in v1.** Base is a fixed monthly amount; timesheets report
   worked/overtime hours and approved leave days for transparency; unpaid
   leave does not reduce pay. Working-day/holiday calendars stay out of scope
   (`leave.md` Decision 7 is closed as "deferred, not owned here").
6. **ClickHouse deferred to the Analytics spec.** This spec emits
   `payroll.calculated`; the Phase 4 Analytics feature owns the payroll-cost
   projection. Payroll stays OLTP-only.
7. **Overtime pay = `hours × (base / standard_monthly_hours) ×
   overtime_multiplier`** with tenant config defaults `160` / `1.50` —
   deterministic, salary-relative, plain arithmetic (ADR 0003 keeps the engine
   inspectable).

## Plan

_(/plan, 2026-10-21. Ordered tasks; every task ends with `make check` green.)_

**Prerequisite P0 — overtime (PO Decision 1: overtime first, payroll second).**
`overtime.md` does not exist yet (no spec, no domain, no contracts). It is its
own feature with its own `/spec` + `/plan` + build; payroll needs exactly three
things from it: (a) overtime rows with states through `HR_VERIFIED` and
`INCLUDED_IN_PAYROLL`, (b) a **read port** returning HR-verified hours per
`(employee, period)`, (c) a **write port** flipping counted rows to
`INCLUDED_IN_PAYROLL` with `included_in_run_id`. Tasks T3 defines those ports as
Protocols + fakes so payroll is fully testable before overtime lands; T7/T8/T6
use the real adapters once P0 exists. Do not ship payroll counting real
overtime rows before P0 is built — until then overtime pay is exercised
through fakes and `0.00` hours in e2e.

### Acceptance criteria → changes

| # | Acceptance criterion | Delivered by |
| --- | --- | --- |
| AC1 | create → `queued`; worker → `calculated`; deterministic payslips; formula; totals = Σ lines to the cent | T4 (calculator), T7 (job), T2 (tables), T5–T6 (API) |
| AC2 | finalize → `finalized`, payslips `issued`, audit, overtime flipped once, `payroll.calculated` + `payslip.issued` envelopes, no amounts | T1 (contracts), T6 (finalize + events), T7 (calculate event), P0 (flip port) |
| AC3 | recalculate replaces provisional payslips atomically; unchanged inputs → byte-identical | T4 (determinism), T7 (atomic replace) |
| AC4 | correction run supersedes on finalize; old payslips `superseded`, readable, excluded from "current" | T6 (supersession), T9 (list semantics) |
| AC5 | `payroll_period_not_closed` / `payroll_period_open` / `payroll_run_not_calculated` / `payroll_run_finalized` | T6 (+ T3 exceptions) |
| AC6 | `no_salary` skip in run summary, no payslip, run still calculates + finalizes | T7 |
| AC7 | denied roles `403`; foreign payslip `404` | T3 (policies), T5/T6/T9 (integration tests) |
| AC8 | tenant isolation `404`/empty | T5/T9 integration tests |
| AC9 | event schemas exist, validate payloads, contain no amounts | T1 (contract tests) |
| AC10 | unit tests: injected clock, fixed config, rounding at the cent | T4 |
| AC11 | worker: queued → calculated (or `failed` + stable code, recoverable); duplicate pass no double payslips | T8 |
| AC12 | Web: config → salary → run → calculated → review → finalize | T10 |
| AC13 | Web: own payslips only, issued+superseded marked; foreign payslip id → not-found | T11 |
| AC14 | Web: finalize gated on status; `409` mapped to toast, not raw code | T10 |
| AC15 | E2E full hop with expected net | T12 |

### Ordered tasks

**T1 — Event contracts first (before any producer).**
- Add `contracts/events/payroll/payroll-calculated.v1.json` (data: `run_id`,
  `period`, `employee_count`) and `payslip-issued.v1.json` (data: `payslip_id`,
  `run_id`, `employee_id`, `period`) — draft 2020-12, `additionalProperties:
  false`, identifiers/period/counts only; envelope via
  `contracts/events/common/event-envelope.v1.json` (see Risk R2 for the
  run-level key question).
- Add ADRs: **0017 money & rounding** (`Numeric(12,2)` / `Decimal`,
  ROUND_HALF_UP per output line at 2dp, intermediates unrounded, JSON numbers
  2dp — the repo's first money convention), **0018 payroll↔overtime seam**
  (read + write ports; extends ADR 0014's read-only seam to a write flip), and
  an **ADR 0012 amendment** for R2's run-level envelope key (0016 is taken by
  the notification read-state ADR).
- Tests: `apps/api/tests/contract/test_payroll_events.py` — schemas parse,
  sample payloads validate, PII-marker scan (reuse `_PII_KEY_MARKERS` incl.
  `salary`), no amount-like keys.
- Verify: `make check-api`.

**T2 — Persistence: migration + models.**
- `apps/api/alembic/versions/0010_payroll.py` (append-only, named constraints,
  partial-index parity): `payroll_configs` (one per tenant: `currency`,
  `standard_monthly_hours`, `overtime_multiplier`), `payroll_deduction_lines`,
  `salary_records` (UNIQUE `(tenant_id, employee_id, effective_from)`),
  `payroll_runs` (partial UNIQUE on `(tenant_id, period) WHERE status <>
  'finalized'` — one open run per period; `supersedes_run_id` /
  `superseded_by_run_id`; `failed_code`), `payroll_bonuses`, `payroll_timesheets`,
  `payslips` (UNIQUE `(run_id, employee_id)`), `payslip_deduction_lines`
  (ordered by `code` for determinism). `decimal(12,2)` money columns,
  `tenant_id` everywhere (invariant 1), UUIDv7 ids, timestamptz.
- `apps/api/app/domains/payroll/models.py` mirroring it (ADR 0017 types).
- Verify: `make db-test-up && make check-api` (migration applies clean, models
  import, ruff/pyright green).

**T3 — Domain scaffolding + seam ports.**
- `domains/payroll/{schemas,exceptions,repository,events,policies}.py`:
  wire schemas per the API-contract section; one exception per pinned code
  (`PayrollPeriodNotClosedError` 409 `payroll_period_not_closed`, …,
  `SalaryEffectiveDateTakenError` 409 `salary_effective_date_taken`);
  `PayrollRepository` Protocol (tenant-explicit, house style) plus seam
  Protocols: `OvertimeHoursPort` (`verified_hours`, `mark_included(rows,
  run_id)`), `AttendanceHoursPort` (`worked_hours`, `attendance_days`),
  `LeaveDaysPort` (`approved_leave_days`) — payroll owns the Protocols; Postgres
  adapters in `app/infrastructure/postgres/` do the cross-table joins (ADR 0014
  pattern: domain imports no foreign internals/tables, R3); policies
  (`ensure_can_configure`, `ensure_can_run`, `ensure_can_view_runs`,
  `ensure_can_view_payslip`) — Finance/Admin/Super Admin mutate, HR view,
  everyone own payslips; `events.py` envelope builders.
- Wire DI in `app/dependencies.py`; fakes for every port in
  `tests/unit/payroll/conftest.py`.
- Tests: `tests/unit/payroll/test_policies.py` (denied-role matrix incl. HR
  finalize denied), `test_exceptions.py` (codes/status).
- Verify: `make check-api`.

**T4 — Deterministic calculation core (pure).**
- `domains/payroll/calculation.py`: salary-pick (greatest `effective_from` ≤
  period end), `hourly_rate = base / standard_monthly_hours`, overtime pay,
  bonus sum, one deduction line per active rule, net; every output line
  ROUND_HALF_UP 2dp; stable ordering (deduction lines sorted by `code`);
  `calculate_run_inputs → {payslips, totals, skipped}` pure and clock-free
  (period/`now` passed in).
- Tests: `tests/unit/payroll/test_calculation.py` — injected clock + fixed
  `160`/`1.50` (AC10), rounding asserted at the cent, line-sum == totals,
  determinism (same inputs → byte-identical serialized payslips, AC3),
  `no_salary` skip, full-base for mid-period hire/termination, inactive
  deduction absent.
- Verify: `make check-api`.

**T5 — Config, deduction lines, salary records API.**
- `domains/payroll/service.py` (config/deduction/salary operations + audit
  entries: field names only for PII values) and `app/api/v1/payroll.py`
  (`GET/PUT /payroll/config`, deductions CRUD-lite, `GET/POST /payroll/salaries`)
  with pinned `operationId`s from the contract table; register in
  `app/api/v1/__init__.py`; run `make gen-api` (keeps `test_openapi_drift`
  green, refreshes orval client).
- Tests: `tests/integration/test_payroll_config.py`,
  `test_payroll_salaries.py` — happy paths, `422` (`percent_of_base` outside
  `0.01..100`, `fixed_amount < 0.01`, salary `< 0.01`), `409
  salary_effective_date_taken`, denied roles `403` (Employee/Manager/HR
  mutating), cross-tenant `404`/empty (AC8), audit assertions.
- Verify: `make check-api`.

**T6 — Run lifecycle + bonuses API (state machine).**
- `domains/payroll/service.py`: `create_run` (closed-period + one-open-run
  guards), `recalculate`, `finalize` (conditional `UPDATE … WHERE status =
  'calculated'` → exactly one winner; flips payslips `issued` + overtime rows
  via `OvertimeHoursPort.mark_included` once; supersedes the prior finalized
  run's payslips on a correction run; `payslip.issued` envelopes through
  `EventOutbox.append` in the same transaction; audit per transition),
  `create_bonus` (`payroll_employee_not_in_run`, `payroll_run_finalized`);
  routers `POST /payroll/runs`, `POST /payroll/runs/{id}/recalculate`,
  `POST /payroll/runs/{id}/finalize`, `POST /payroll/runs/{id}/bonuses`;
  `make gen-api`.
- Tests: `tests/integration/test_payroll_runs.py` — AC5 error matrix, concurrent
  finalize (two tasks, one wins), bonus guards, audit entries per transition,
  denied-role `403` (HR finalize), tenant isolation; outbox envelopes validated
  against T1 schemas with no amounts.
- Verify: `make check-api`.

**T7 — Calculation job (application service).**
- `domains/payroll/service.py` `calculate_run(run_id, now)`: `QUEUED →
  CALCULATING → CALCULATED|FAILED` (stable `failed_code`, e.g.
  `payroll_calculation_failed`), assembles timesheets via the three ports
  (worked/attendance from closed attendance records by check-in date,
  `overtime_hours` = HR-verified rows, `leave_days` = APPROVED leave calendar
  days), computes via T4, replaces provisional payslips atomically, writes
  `payroll.calculated` envelope in the success transaction, idempotent on run
  state (re-delivered job pass no-ops / no second payslip set).
- Tests: `tests/integration/test_payroll_calculation.py` — AC1/AC3/AC6, `no_salary`
  skip listed in run summary, FAILED + recalculate recovery, duplicate-pass
  idempotency, totals-to-the-cent.
- Verify: `make check-api`.

**T8 — Worker `workers/payroll/`.**
- `workers/payroll/{main.py,runner.py}` in the `apps/api` uv env (relay
  pattern): poll `payroll_runs` for `QUEUED` (claim via `FOR UPDATE SKIP
  LOCKED` / state-guarded update), call `calculate_run`, backoff + graceful
  stop, readiness/started log line; Makefile `dev-payroll` target +
  `scripts/dev.sh` wiring.
- Tests: runner-level test in `tests/integration/` invoking the loop once with
  fakes for sleep; failure path ends `failed` with stable code.
- Verify: `make check-api`; smoke `make dev-payroll` picks up a queued run.

**T9 — Query endpoints.**
- Routers: `GET /payroll/runs`, `/runs/{id}`, `/runs/{id}/timesheets`,
  `/runs/{id}/payslips`, `POST`-less reads, `GET /payroll/payslips` (own only,
  includes `superseded`), `GET /payroll/payslips/{id}` (own or
  HR/Finance/Admin/Super Admin; otherwise 404); `PageParams` clamp pagination;
  `make gen-api`.
- Tests: `tests/integration/test_payroll_queries.py` — own-payslip scoping
  (AC13 server side), foreign payslip `404`, tenant isolation, superseded
  excluded from "current" but listed in my-payslips, role gating (Manager sees
  no team payroll).
- Verify: `make check-api`.

**T10 — Web: Finance/Admin payroll workspace** (load `workpulse-ui` skill;
shadcn skill only if `components/ui/` primitives change).
- `apps/web/features/payroll/` (api.ts query keys, `hooks/`, `schema.ts` zod,
  `labels.ts` code→message map incl. the six `409` codes, components:
  `PayrollConfigForm`, `DeductionLinesTable`, `SalaryForm`, `RunsTable`,
  `RunDetail` (timesheets + payslip line tables + totals), `BonusDialog`) and
  `apps/web/app/(dashboard)/payroll/` routes (`page.tsx` workspace,
  `runs/[id]/page.tsx`); finalize action disabled/hidden while
  `queued|calculating|failed` (AC14); all mutations surface mapped toasts via
  the `errorMessage`/`ApiError` pattern — never raw codes; "simulated payroll"
  wording in page copy (ADR 0003).
- Tests: Vitest in `check-web` — error-code mapping, status-gating logic,
  zod schemas.
- Verify: `make check-web`.

**T11 — Web: "My payslips".**
- `apps/web/app/(dashboard)/payroll/my-payslips/page.tsx` +
  `features/payroll/components/MyPayslipsTable.tsx` (own payslips, `issued` +
  `superseded` marked); unknown/foreign payslip id → existing not-found state;
  nav entry visible to every role, workspace nav filtered to
  Finance/Admin/HR (server still enforces).
- Tests: Vitest for the table's status marking + not-found rendering.
- Verify: `make check-web`.

**T12 — E2E + close-out.**
- `tests/e2e/payroll.spec.ts` (house login helper + seeded roles): Finance
  config → salary → create run → wait `calculated` → review payslip lines →
  finalize → employee logs in and sees the issued payslip with the expected net
  (AC15); plus finalize-unavailable-while-queued and foreign-payslip
  not-found checks (AC14/AC13).
- Tick acceptance criteria, sync `.ai/features/README.md` (→ `done`) and
  `tasks/TODO.md` in the closing change; run `make check` end-to-end.
- Verify: `make check-e2e`, then `make check` (free :3000/:5433/:6379
  first — known local conflicts).

### Risks & unknowns

- **R1 — Overtime prerequisite missing (biggest).** P0 is unbuilt; payroll's
  formula term and the finalize flip depend on its ports. Mitigation: T3
  Protocols + fakes keep payroll testable; the real adapters and any
  real-hours e2e block on P0. Do not start T7/T8 integration with real
  overtime rows before `overtime.md` is built.
- **R2 — Event envelope key conflict (resolved in T1).** ADR 0012 pinned the
  Kafka message key as `data.employee_id`, but `payroll.calculated` is run-level
  (`run_id`, `period`, `employee_count`). Resolution: an ADR 0012 amendment
  extends the rule to `employee_id` when present, else `run_id`; `EventEnvelope`
  and the relay publisher implement it, and the contract test pins both keys.
- **R3 — Timesheet cross-context reads.** The spec pins only the overtime
  seam; `worked_hours`/`attendance_days`/`leave_days` are attendance/leave
  data. Proposed default: the same port pattern (payroll-owned read Protocols,
  adapter joins — the `covers_leave` precedent). Recorded in ADR 0018.
- **R4 — First money convention in the codebase** (no `Numeric`/`Decimal`
  anywhere yet): column scale, ROUND_HALF_UP, unrounded intermediates and
  JSON 2dp are pinned by ADR 0017 in T1 so T2/T4 cannot drift.
- **R5 — Write-direction seam.** ADR 0014's seam is read-only;
  `mark_included` is the first cross-context write port. Ownership (payroll
  Protocol, overtime-side implementation) is settled in ADR 0018; the
  "at most one FINALIZED run counts a row" guarantee needs a uniqueness/guard
  test on the overtime side (P0) and a payroll-side integration test.
- **R6 — Worker claim + finalize concurrency.** The polling run-job and the
  exactly-one-wins finalize are new patterns (relay is outbox-shaped, not
  job-shaped). Keep claims state-guarded (`WHERE status='queued'`) and prove
  both races with tests (T6/T8).
- **R7 — Determinism details** (line ordering by `code`, rounding at the line
  not the total) must be pinned in T4 or AC3's byte-identical test will flake.
- **R8 — Local gate conflicts** on this machine (:3000 Nuxt dev, stray
  postgres :5433 / redis :6379) — `make check` needs them free.

### Test strategy

- **Unit** (`apps/api/tests/unit/payroll/`, no infra): calculation core —
  formula, rounding at the cent, determinism, `no_salary`, no-proration
  (AC1/AC3/AC6/AC10); policies denied-role matrix (AC7); exception codes;
  event builders pass `EventEnvelope` + PII scan (AC9).
- **Integration** (`apps/api/tests/integration/`, test DB :5433): API happy +
  failure paths per task (AC5 `409` matrix), tenant isolation per domain
  (AC8), denied-role `403` + audit assertions for every mutation (testing.md
  rules 3), worker idempotency + failure/recovery (AC11), finalize race.
- **Contract** (`apps/api/tests/contract/`): `payroll.calculated` /
  `payslip.issued` schemas validate produced payloads, contain no amounts
  (AC2/AC9); `test_openapi_drift` stays green via `make gen-api` after each
  router task.
- **Web** (`make check-web` Vitest): error-code→toast mapping, finalize status
  gating, payslip status marking, zod schemas (AC14).
- **E2E** (`tests/e2e/payroll.spec.ts`, `make check-e2e`): the AC15 full hop
  plus the two web failure cases (AC13/AC14) against seeded roles.
- Gates per task as listed; full `make check` before the closing status sync.

## Close-out (2026-10-22)

T1–T12 are built. T10 landed the Finance/Admin workspace, T11 the self-service
"My payslips" list/detail with the foreign-id not-found state (and one
longest-prefix nav rule so the nested payslip route marks only its own entry),
and T12 the full e2e hop: `full-hop.sh` now runs the payroll worker alongside
the relay/realtime consumer and resets the seeded tenant's payroll ledger
through the shared `apps/api/scripts/reset_payroll_fixtures.py`, which the spec
also invokes per attempt so CI retries start clean, so
`tests/e2e/payroll.spec.ts` proves config → salaries → queued gate →
`calculated` → provisional line review → finalize → the employee's issued
payslip and a foreign-id `404` without test doubles. Gates: 1044 API tests, 226
web tests, 22 Playwright tests.

**Overtime seam (landed 2026-10-22).** `overtime.md` is built, so
`app/dependencies.py` now wires the real `PostgresOvertimeHoursPort` in place of
`NoOvertimeHoursPort`. Payroll code is unchanged: the overtime term, the
read/write seam and the "counted once" flip are proven by
`tests/integration/test_overtime_seam.py`, and `tests/e2e/payroll.spec.ts` seeds
an `hr_verified` row for the run's month, sees its hours in the timesheet, and
proves the finalize flip to `included_in_payroll` with the run handle.
