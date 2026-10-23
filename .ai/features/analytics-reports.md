# Feature: Analytics & Reports

- **Status:** done
- **Phase:** 4
- **Size:** L
- **Surfaces:** api+web+events+workers
- **Links:** `.ai/domain.md` (Analytics & Reports context — pure consumer; event catalog) · `.ai/architecture.md` (Phase 4: ClickHouse = analytical projection; Phase-4 success metric) · `.ai/product.md` (goal 4 "analytics without a data project"; problem: "analytics is a monthly export") · `.ai/security.md` ("View company-wide reports" matrix row; analytics-row PII rule) · ADR 0006 (phase-gated `clickhouse-connect`) · ADR 0012 (outbox transport: consumer obligations, `event_id` dedupe, kept outbox as replay record) · ADR 0013 (additive v1 payload-extension pattern) · ADR 0017 (decimal/rounding discipline) · ADR 0018 (`included_in_payroll` flip emits no event) · `employees.md` · `attendance.md` · `leave.md` · `overtime.md` (event sources) · `payroll.md` (Decisions 6 deferral, answered here) · `notifications.md`/`realtime-consumer.md` (consumer conventions)

## Problem

Workforce questions today are answered by "a monthly export" or not at all
(`.ai/product.md`): there is no way to answer "overtime rate by team, location
and seniority over the last six months" without a separate data project, and
aggregates must not be run against the OLTP tables that serve check-ins and
approvals. Phase 4's whole reason for ClickHouse is that aggregation belongs in
an analytical projection fed by the event stream — "one fact, one event"
(`.ai/product.md` goal 2) already promises that analytics is a projection. This
feature delivers both halves of the Analytics & Reports context: an
event-driven projection into ClickHouse (`workers/reports/`) and a role-scoped
Reports surface (API + web) that answers the workforce questions — overtime
first — in well under a second.

## Business rules

1. **Analytics & Reports is a pure consumer** (`.ai/domain.md`): every
   ClickHouse row is a projection of the `workpulse.events` stream. A rebuild
   (drop projection state, replay the stream) reproduces the aggregates without
   ever reading OLTP tables. **No OLTP backfill** (PO Decision 7): reports
   answer over whatever the stream covers; range filters simply return what
   exists.
   - Display labels (department/position/location/leave-type names) are the one
     carve-out, and they follow the notifications enrichment pattern exactly
     (`.ai/domain.md`, ADR 0015): a **read-only reference lookup port** at
     query time, never a projection input, never another context's tables
     touched by the worker.
2. **Replay-safe projection** (ADR 0012 decision 5): the consumer deduplicates
   on `event_id`, so at-least-once redelivery never double-counts. Unknown
   fields are ignored; unknown or irrelevant `event_type`s (`notification.*`,
   `payslip.*`, …) are skipped with the offset advanced. A malformed message
   is logged (ids only, no payload) and skipped — a consumer never poisons its
   stream.
3. **Dimension state is folded from the employee stream.** Each fact is
   attributed to the employee's department/position/location **as of the fact
   date** — employment intervals folded from `employee.created`,
   `employee.transferred`, `employee.terminated` (plus the two additive
   payload extensions under Events). Pre-extension messages lacking the new
   fields degrade honestly: missing tenure → `unknown` seniority band; missing
   `to_position_id`/`to_location_id` → the previous value is kept.
4. **Seniority bands** derive from first employment start (`hired_at`), tenure
   measured at the row's bucket end (for `interval=total`, at `to`):
   `< 1 year` (`lt_1y`), `1–<3 years` (`1_3y`), `3–<5 years` (`3_5y`),
   `5+ years` (`5y_plus`). No tenure data → `unknown`.
5. **Overtime facts count verified hours only** (the payroll-relevant claim): a
   request contributes exactly while its folded state is `hr_verified`. The
   payroll flip to `included_in_payroll` emits no event (ADR 0018 decision 6)
   and therefore never removes the hours — a paid claim stays one counted
   claim. An HR un-verification (`overtime.rejected` with
   `rejected_from=hr_verified`) removes the hours again. Facts are attributed
   by `work_date`.
6. **Attendance facts** fold from `attendance.checked_in` + `attendance.checked_out`
   (same `attendance_id` = one record), attributed by `workday`.
   `worked_minutes` comes from `duration_minutes` of checked-out records only;
   `late`/`minutes_late` from the check-in fact. A checked-in-but-never-checked-out
   record counts as a check-in (and a lateness fact) but contributes no worked
   minutes.
7. **Leave facts** are approved days from `leave.approved`, attributed whole to
   the request's `start_date` bucket (a request is never split across buckets).
   The leave state machine has no un-approve, so approved days are stable once
   counted; `leave.rejected`/`leave.withdrawn` only ever follow `PENDING` and
   contribute nothing.
8. **Headcount**: active as of a date = hired on or before it and not
   terminated before it; `joiners` = `hired_at` in bucket; `leavers` =
   `employee.terminated` `effective_date` in bucket. A transfer moves the
   employee between groups at `effective_from` — a mid-month transfer splits
   their facts across groups at that date.
9. **Metrics per row** (identifiers, dates, counts and hours only — never
   names, emails, salaries or reasons; `.ai/security.md`):
   - Overtime: `verified_hours` (decimal 2dp), `verified_request_count`,
     `verified_day_count` (distinct workdays), `active_employee_count`,
     `hours_per_employee` = `verified_hours / active_employee_count` (2dp;
     `0.00` when the count is 0) — the "rate" of PO Decision 6.
   - Attendance: `checkin_count`, `worked_day_count`, `worked_minutes`,
     `late_count`, `late_rate` = `late_count / checkin_count` (fraction, 4dp;
     `0.0000` when `checkin_count` is 0), `avg_minutes_late` (1dp over late
     records; `0.0` when none).
   - Leave: `approved_day_count`, `approved_request_count`.
   - Headcount: `headcount` (at bucket end; for `interval=total`, at `to`),
     `joiners`, `leavers`.
   Rounding follows ADR 0017's discipline (decimal strings in the contract,
   half-even at 2dp for hours).
10. **Query-time aggregation**: v1 answers with `GROUP BY` over the projection's
    fact tables — no materialized views yet ("boring first"). The flagship
    question (`group_by=department,location,seniority`, `interval=month`, six
    months) must answer in **< 1s** (`.ai/product.md` Phase-4 metric), proven
    by an integration benchmark over seeded volume. Pre-aggregation is the
    documented upgrade if the budget is missed — not a v1 deliverable.
11. **Access is read-only and role-scoped** (`.ai/security.md` matrix):
    HR / Finance / Admin / Super Admin see company-wide reports; a Manager sees
    the same reports restricted to their manager scope — the identical
    self + direct-reports set that gates their leave/overtime visibility
    (`visible_employee_ids`) — so report scope and inbox scope can never
    disagree; an Employee-role call is `403`. Reports mutate nothing: **no
    audit entries and no events** (the `notification-inbox.md` rule-7
    precedent — a read is not a sensitive mutation).
12. **Tenant isolation** (invariant 1): every ClickHouse query filters
    `tenant_id` in the reports repository — never in the router, never in the
    service. Report rows contain **no employee identifiers at all**: rows are
    aggregates per group/bucket.
13. **Time is a parameter** (`.ai/conventions.md`): reports take explicit
    `from`/`to`; no wall-clock reads in the query path. Projection writes stamp
    the consumer's clock only as bookkeeping.
14. **One name**: the context is implemented as `reports` end to end —
    `domains/reports/`, `api/v1/reports.py`, `workers/reports/`,
    `features/reports/`, `app/(dashboard)/reports/`. The worker is the
    analytics projector; the store is "the analytics store" in prose.

## Scope

- ClickHouse as a Phase-4 store: `clickhouse` + `clickhouse-test` compose
  services, idempotent DDL under `database/clickhouse/`, `clickhouse-connect`
  added to `apps/api` (ADR 0006's pre-agreed Phase-4 gate — the dependency
  justification architecture rule 10 requires; same wiring pattern as
  `aiokafka`, ADR 0012 decision 10).
- `workers/reports/`: the projection worker — consume `workpulse.events`
  (stable group `workpulse-reports`, `auto_offset_reset="earliest"` within the
  configured retention window: first start builds the retained history,
  restarts resume committed offsets), fold
  employee dimension state + the four fact families, write to ClickHouse,
  commit offsets manually after the write (at-least-once in, idempotent on
  `event_id` out). Single instance in v1 (relay precedent).
- A documented, fenced rebuild path: gate report reads through
  `projection_state`, stop the worker, reset the group to `earliest`, truncate
  the projection tables and replay; the topic retains the 24-month report span,
  and ADR 0012 decision 8's kept `event_outbox` rows are the re-publish source
  if the topic has expired past retention (ops note, not code).
- Two additive v1 event payload extensions (ADR 0013 pattern — optional via
  `required`-list membership, no version bump) with producer updates:
  `employee-created.v1.json` gains `hired_at`;
  `employee-transferred.v1.json` gains `to_position_id`, `to_location_id`.
- `apps/api/app/domains/reports/` (read side + fold rules) and
  `apps/api/app/api/v1/reports.py` — the four report endpoints below.
- Web `features/reports/` + `app/(dashboard)/reports/`: a "Reports" nav entry
  and workspace with four report tabs (Overtime · Attendance · Leave ·
  Headcount), a shared filter bar, and charts + underlying tables per report
  (one charting dependency: **Recharts**, approved PO Decision 9; visuals use
  the existing `--chart-1..5` tokens and the `workpulse-ui` rulebook).
- Tests: unit (folds, policies, band/rate math), integration (ClickHouse
  repository + routers + real-broker worker proofs + rebuild proof + latency
  benchmark), contract (OpenAPI shapes + the two extended payload schemas,
  including a pre-extension payload still validating), web tests, and
  `tests/e2e/reports.spec.ts` (ClickHouse + worker started by `make
  check-e2e`'s hop, like the relay/consumer workers).

## Out of scope

- **Payroll cost analytics** (PO Decision 2): events carry no amounts, ever
  (`.ai/security.md`, `payroll.md`). `payroll.*` events are not consumed in v1
  — neither cost nor counts (PO Decision 1 chose the core four without payroll
  counts). Cost questions need their own amounts decision + ADR; this spec is
  the answer to `payroll.md` Decisions 6's deferral, and the answer is "still
  deferred".
- **CSV/async export** (PO Decision 4) — reports are on-screen; export is a
  follow-up spec if demanded.
- **OLTP backfill** of pre-bus history (PO Decision 7).
- Cross-dimensional **cross-tab beyond 3 dimensions**, and any arbitrary
  BI/SQL access (product non-goal: "we serve workforce questions").
- Department-tree roll-up: "by department" is the employee's exact department,
  never ancestor aggregation.
- Materialized views / pre-aggregation tables (the named upgrade if the <1s
  budget is missed).
- Scheduled/emailed reports, report notifications, saved views, dashboards
  beyond the four reports.
- Recruitment / Performance / payroll analytics — their event streams don't
  exist or don't carry the facts (Recruitment/Performance are Phase 4+).
- CDC (Phase 5 replaces hand-written projections — architecture.md).
- Multi-instance worker concurrency and offset claiming (relay precedent: v1
  runs one instance).

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/reports/` (schemas · service · repository · policies · exceptions; **no** `models.py`/`events.py` — the domain owns no OLTP rows and emits no events) |
| API router | `apps/api/app/api/v1/reports.py` |
| Events | consumed only — `contracts/events/{employees,attendance,leave,overtime}/`; additive extensions to `employee-created.v1.json` (`hired_at`) and `employee-transferred.v1.json` (`to_position_id`, `to_location_id`) |
| Workers | `workers/reports/` (consume → fold → ClickHouse; `main.py` + `consumer.py` + `mapping.py`, mirroring `workers/notifications/`) |
| Infra | `docker-compose.yml` (`clickhouse` + `clickhouse-test`) · `database/clickhouse/` (idempotent DDL) · `apps/api/app/infrastructure/clickhouse/` (client, projection writer, reports repository) · `apps/api/app/shared/` (reference lookup port for labels) |
| Web feature | `apps/web/features/reports/` (components · hooks · api.ts · types.ts · utils.ts) |
| Web routes | `apps/web/app/(dashboard)/reports/` |
| API tests | `apps/api/tests/unit` · `tests/integration` · `tests/contract` |
| E2E | `tests/e2e/reports.spec.ts` |

## Roles & access

| Capability | Employee | Manager | HR | Finance | Admin | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| View the four reports | — | team (self + direct reports) | Y | Y | Y | Y |

Policies live in `domains/reports/policies.py` (AGENTS.md rule 12). The web
hides the Reports nav from Employee users; the API is the authority. All four
endpoints are read-only: no audit entries, no events (rule 11).

## API contract

Pinned before code. Prefix `/api/v1`; the four operationIds are the orval hook
names and must match exactly. All four share the same query semantics and
response envelope.

| Method & path | operationId (orval hook) | Response |
| --- | --- | --- |
| `GET /api/v1/reports/overtime` | `getOvertimeReport` | `200 OvertimeReport` |
| `GET /api/v1/reports/attendance` | `getAttendanceReport` | `200 AttendanceReport` |
| `GET /api/v1/reports/leave` | `getLeaveReport` | `200 LeaveReport` |
| `GET /api/v1/reports/headcount` | `getHeadcountReport` | `200 HeadcountReport` |

Query parameters (all four):

| Param | Type | Semantics |
| --- | --- | --- |
| `from` | date (`YYYY-MM-DD`), **required** | inclusive range start |
| `to` | date, **required** | inclusive range end; `from ≤ to`, span ≤ 24 months |
| `group_by` | string, default `department` | comma-separated 1..3 distinct dimensions, in row order |
| `interval` | enum `day\|week\|month\|total`, default `month` | time bucketing; `week` starts Monday; `total` → `bucket: null` |
| `department_id` | uuid, optional | restrict facts to one department |
| `location_id` | uuid, optional | restrict facts to one location |
| `position_id` | uuid, optional | restrict facts to one position |

`group_by` dimensions: `department|location|position|seniority` on all four
reports, plus `leave_type` **on the leave report only** (PO Decision 8).
Buckets cover `[from, to]`; edge buckets are clipped (a `from` mid-month starts
that month's bucket mid-way). Rows exist only for non-empty buckets.

Validation: `from > to`, span > 24 months, a duplicate/unknown dimension, or
`leave_type` on a non-leave report → `422`. A filter id belonging to another
tenant or unknown → **empty rows** (a filter is not a fetch; existence is never
leaked). ClickHouse unreachable → `503` `{"error": {"code": "reports_unavailable"}}`.

Response envelope (shape identical across the four; metric block differs):

```jsonc
OvertimeReport {
  "from": date, "to": date,
  "group_by": ["department", "location", "seniority"],
  "interval": "day|week|month|total",
  "rows": [ OvertimeReportRow, … ]
}

ReportGroup {                       // one per group_by dimension, same order
  "dimension": "department|location|position|seniority|leave_type",
  "key": string,                    // uuid | band code | leave_type_code | "unknown"
  "label": string                   // reference name (enrichment port) or band label
}

OvertimeReportRow {
  "bucket": date|null,              // bucket start; null for interval=total
  "groups": [ReportGroup, …],
  "verified_hours": decimal,        // 2dp
  "verified_request_count": int,
  "verified_day_count": int,
  "active_employee_count": int,
  "hours_per_employee": decimal     // 2dp; verified_hours / active_employee_count
}

AttendanceReportRow {
  "bucket": date|null, "groups": [ReportGroup, …],
  "checkin_count": int, "worked_day_count": int, "worked_minutes": int,
  "late_count": int,
  "late_rate": decimal,             // fraction 0..1, 4dp
  "avg_minutes_late": decimal       // 1dp
}

LeaveReportRow {
  "bucket": date|null, "groups": [ReportGroup, …],
  "approved_day_count": int, "approved_request_count": int
}

HeadcountReportRow {
  "bucket": date|null, "groups": [ReportGroup, …],
  "headcount": int, "joiners": int, "leavers": int
}
```

No employee identifiers appear in any row (rule 12). Dates are `YYYY-MM-DD`,
timestamps UTC ISO 8601, decimals serialized as ADR 0017 decimal strings.

## Events

Analytics & Reports **emits nothing** — it is the domain's second pure consumer
(after Notifications). Contract before consumer (AGENTS.md rule 4): the two
additive extensions below land before the worker that needs them. Pattern is
ADR 0013's exactly — optional via `required`-list membership, nullable via
`"type": ["string","null"]`, `additionalProperties: false` kept, builders
always fill, **no version bump**:

| Schema (v1, additive) | New field | Filled by |
| --- | --- | --- |
| `employee-created.v1.json` | `hired_at` (`YYYY-MM-DD`, optional) | `EmployeeService.create` — the initial period's `effective_from` |
| `employee-transferred.v1.json` | `to_position_id`, `to_location_id` (uuid string or null, optional) | `EmployeeService.transfer` — the new period's `position_id`/`location_id` |

Consumed and folded: `employee.created` / `employee.transferred` /
`employee.terminated` (dimension state), `attendance.checked_in` /
`attendance.checked_out` (attendance facts), `leave.approved` (leave facts),
`overtime.requested` / `overtime.approved` / `overtime.verified` /
`overtime.rejected` / `overtime.withdrawn` (overtime state + facts).
Everything else on the topic is skipped. Pre-extension envelopes already in
`event_outbox`/Kafka remain valid; the consumer treats the missing fields as
rule 3 describes.

## Edge cases

- The same envelope delivered twice (at-least-once crash window, ADR 0012) →
  aggregates unchanged; the `event_id` dedupe absorbs it.
- Rebuild after ClickHouse data loss → truncate + replay from `earliest`
  reproduces byte-identical aggregates (fold is a pure function of the stream).
- An `hr_verified` row un-verified later (`overtime.rejected`,
  `rejected_from=hr_verified`) → its hours drop out of every affected bucket.
- A row that reaches payroll's `included_in_payroll` flip → hours stay counted
  exactly once (the flip emits no event — nothing to fold).
- Facts for an employee created by a pre-extension `employee.created` →
  `unknown` seniority band, still counted under department/location/position.
- A mid-month transfer → that employee's facts split at `effective_from`;
  headcount moves group at the same date.
- A leave request spanning two months → all its days land in the
  `start_date` bucket, never split.
- A record checked in but never checked out → counts in `checkin_count` (and
  `late_count` if late), contributes 0 `worked_minutes`, is absent from
  `worked_day_count`.
- Zero-denominator metrics (`hours_per_employee`, `late_rate`) → `0.00` /
  `0.0000`, never a division error.
- Empty result range or a manager with no direct reports → `200` with
  `rows: []` — never 404.
- `from > to`, span > 24 months, bad `group_by`/`interval` → `422`.
- Employee-role call → `403`; the web nav is hidden but direct API calls are
  denied by policy, not by UI.
- Cross-tenant: tenant B's query returns zero tenant-A facts even with a
  tenant-A uuid in a dimension filter (repository `tenant_id` scope).
- ClickHouse down → `503 reports_unavailable`; the web reports surface shows
  its error state; OLTP writes are unaffected (the write path never depends on
  the analytics store).
- A malformed or foreign message on the topic → ids-only log, skip, advance —
  the worker never stalls.
- Person-level data (names, emails, salaries, leave reasons) anywhere in
  projection rows, logs or report responses is a PII bug.

## Acceptance criteria

- [x] API (overtime): given projected fixtures spanning two departments, two locations and multiple tenures, `GET /api/v1/reports/overtime?from&to&group_by=department,location,seniority&interval=month` returns rows whose `verified_hours`, `verified_request_count`, `verified_day_count`, `active_employee_count` and `hours_per_employee` exactly match the expected per-group per-month aggregates.
- [x] API (latency): that same six-month flagship query over seeded volume completes in < 1s in the integration benchmark (`.ai/product.md` Phase-4 metric).
- [x] API (attendance/leave/headcount): each report returns its pinned metric block; leave with `group_by=leave_type` buckets by `leave_type_code`; headcount with `interval=total` reports `headcount` as of `to` plus range `joiners`/`leavers`.
- [x] API (attribution): an employee transferred mid-range contributes pre-transfer facts to the old department and post-transfer facts to the new one (per-fact date attribution).
- [x] API (failure): `from > to` or span > 24 months or `group_by=leave_type` on a non-leave report → `422`; an unreachable ClickHouse → `503 reports_unavailable` with the house error shape.
- [x] API (denied role): an Employee-role call to any report endpoint → `403`; a Manager's rows cover only self + direct reports (fixtures span two managers' teams).
- [x] API (tenant isolation): tenant B querying the same ranges sees no tenant-A facts (empty rows), including with tenant-A dimension filters — 404/empty, never 403-shaped leakage.
- [x] API (PII): a response/log/row sweep finds no names, emails, salaries, leave reasons or employee ids — only group keys, labels, dates, counts and hours.
- [x] Worker: redelivering the whole stream (duplicate `event_id`s) leaves aggregates byte-identical; an `hr_verified → rejected` un-verification removes its hours; unknown and malformed messages are skipped without stalling; offsets commit only after the ClickHouse write.
- [x] Worker (rebuild): truncating the projection and replaying from `earliest` reproduces the exact aggregates (pure-consumer proof, `.ai/domain.md`).
- [x] Contracts: `hired_at`/`to_position_id`/`to_location_id` validate in the extended v1 schemas, the producers fill them, and a pre-extension payload still validates (additive proof, ADR 0013).
- [x] Web: the Reports workspace renders each report with loading, empty and error states; filter changes re-query with new TanStack Query keys (no ad-hoc fetch); charts + tables show the same rows; the surface follows `workpulse-ui` (incl. `--chart-1..5`) and the control-language rules.
- [x] Web (denied): an Employee user never sees the Reports nav and a direct visit is denied (API 403 rendered as the error state).
- [x] E2E: with ClickHouse + the reports worker in the hop, an HR login opens /reports and sees non-empty overtime rows for seeded verified overtime; an Employee login cannot reach the surface.

## Decisions (PO, 2026-10-22)

1. **Report set = the core four**: Overtime (flagship), Attendance & lateness,
   Leave usage, Headcount/workforce overview. Payroll counts were explicitly
   *not* included.
2. **Payroll-cost analytics is out of scope.** Events carry no amounts
   (`.ai/security.md`); `payroll.*` events are not consumed in v1 at all. This
   answers `payroll.md` Decisions 6's deferral: cost analytics stays deferred
   until an amounts decision + ADR exists. (The considered alternative —
   pseudonymized cost bands on payroll events — was rejected as scope.)
3. **Access**: HR / Finance / Admin / Super Admin company-wide; Managers get
   the same reports scoped to their team; Employees get none. (The matrix row
   "View company-wide reports" stays true; the manager slice is a
   team-scoped read in its spirit.)
4. **CSV export is out of scope** for v1; on-screen reports only.
5. **Dimensions**: "team" means **department** (no Team entity exists);
   grouping dimensions are department, location, position and seniority bands
   from first employment start (`hired_at`) — `<1y`, `1–<3y`, `3–<5y`, `5y+`.
6. **The overtime metric** is verified hours + hours per active employee +
   verified request/day counts ("rate" = per-employee normalization for
   cross-group comparability). No "% of worked hours" (that would fold
   attendance into the overtime projection).
7. **History = events only**; no OLTP backfill of pre-bus data.
8. **Leave usage breaks down by leave type** as a fifth `group_by` dimension
   (leave report only).
9. **Charts + tables** on the web surface; a single charting dependency
   (**Recharts**) is approved for `apps/web`.

## Plan

The API contract is already pinned above (`## API contract` — frozen before
acceptance criteria, workflow rule 4); T5 materializes it in code (schemas +
router + `make gen-api`) before any web work (T7), so the surfaces cannot
drift. The event contract extensions land at **T1**, before their producers
(same task) and long before their consumer (T5/T6) — "contract before
producer or consumer" holds on both lanes. Everything else orders so `make
check` stays green after every task.

### Acceptance criteria → changes (restated)

| # | Criterion (restated) | Satisfied by |
| --- | --- | --- |
| 1 | Overtime report over `group_by=department,location,seniority&interval=month` returns exact per-group per-month aggregates | T4 stamped facts + fold; T5 query/service math; T5 integration test |
| 2 | Six-month flagship query over seeded volume < 1s | T5 integration benchmark (perf_counter bound over seeded projection rows) |
| 3 | Attendance/leave/headcount metric blocks; `leave_type` buckets; headcount `interval=total` semantics | T5 schemas/service + integration tests |
| 4 | Mid-range transfer splits facts per-fact date | T4 fold stamps dims at fact date; T5 attribution test |
| 5 | `422` range/group_by violations; `503 reports_unavailable` | T5 query validation + `ReportsUnavailableError`; T5 tests |
| 6 | Employee → `403`; manager rows = self + direct reports only | T5 `policies.py` + scope port; T5 role/scope tests |
| 7 | Tenant B sees no tenant-A facts (empty, not 403-shaped) | T5 repository `tenant_id` scope; T5 isolation test |
| 8 | PII sweep: no names/emails/salaries/reasons/employee ids in rows, logs, responses | T4 stamped rows carry ids only; T5 response shapes; T5/T8 sweep tests |
| 9 | Redelivery idempotent; un-verify removes hours; malformed/unknown skipped; commit after write | T4 convergent per-entity folds; T6 consumer semantics; T6 real-broker proofs |
| 10 | Rebuild (truncate + replay `earliest`) reproduces aggregates | T6 rebuild proof (fold is a pure function of the stream) |
| 11 | Extended v1 schemas validate; producers fill; pre-extension payload still validates | T1 schemas + builders + contract tests |
| 12 | Web workspace: loading/empty/error, query-keyed refetch, charts + tables, `workpulse-ui` | T7 feature + hooks + components + web tests |
| 13 | Employee never sees nav; direct visit denied (403 → error state) | T7 nav `roles` gate + error state; T7 web test; T8 e2e |
| 14 | E2E: hop runs ClickHouse + reports worker; HR sees non-empty overtime rows; Employee blocked | T8 hop wiring + `tests/e2e/reports.spec.ts` |

### Repo research findings the plan relies on

- **F1 — Worker shape.** `workers/notifications/` + `workers/realtime/` are the
  template: `AIOKafkaConsumer(topic, group_id=…, auto_offset_reset=…,
  enable_auto_commit=False)` (`workers/notifications/main.py:71-80`), a
  structural `EventSource` Protocol (`workers/realtime/consumer.py:57-64`),
  per-message manual `_commit` with swallowed commit failures
  (`consumer.py:166-173`), `_parse` swallowing malformed JSON into
  `consumer_message_skipped` ids-only warnings (`consumer.py:190-208`),
  exponential fetch backoff, `started` asyncio.Event readiness + `*_started`
  log lines. Ours differs only in `auto_offset_reset="earliest"` (spec Scope).
- **F2 — Settings.** `apps/api/app/config.py` (pydantic-settings, no prefix):
  naming precedent `realtime_consumer_group`, `kafka_bootstrap_servers`,
  `kafka_events_topic`. Add `reports_consumer_group = "workpulse-reports"` +
  `clickhouse_url`; `.env.example` gains both.
- **F3 — Event builders.** `employee_created` (`domains/employees/events.py:50-74`)
  is called at `service.py:220-231` where `created.hired_at` is in scope;
  `employee_transferred` (`events.py:98-120`) at `service.py:397-407` where the
  new `position_id`/`location_id` (computed :327-339) are in scope — exactly
  where the additive fields go. `EventEnvelope` accepts any `data` dict
  (message-key rule unchanged: `employee_id` present).
- **F4 — Additive pattern.** `attendance-checked-in.v1.json:32-37` shows the
  ADR 0013 shape: optional via `required`-list membership, nullable via
  `"type": ["integer","null"]`, `additionalProperties: false` kept. Contract
  tests build payloads via the real builders
  (`tests/contract/test_employee_events.py:65-111`) and the pre-extension
  precedent is the strip-and-revalidate test
  (`test_attendance_events.py:284`).
- **F5 — Errors.** `WorkPulseError` subclassing is all a `503
  reports_unavailable` needs (`shared/errors.py:15-37`; handler at :50-73,
  registered `app/main.py:38`). House 422 = Pydantic validation, generic
  `validation_error`. Query-param exemplar: `api/v1/overtime.py:62-69`.
- **F6 — Manager scope.** `visible_employee_ids(tenant_id, manager_id)` —
  self + direct reports, one level, `[]` for an unlinked manager
  (`infrastructure/postgres/repositories/leave.py:142-159`, overtime twin).
  Reports gets its own read-only adapter with the identical query (the
  existing per-domain-copy pattern), behind a domain port.
- **F7 — Ports + wiring.** Domain-owned Protocol + infrastructure adapter is
  the house seam: `domains/notifications/ports.py:22-43` `RecipientDirectory`
  ← `infrastructure/postgres/recipient_directory.py` (read-only, tenant-scoped,
  may read another context's ORM). Composition root `app/dependencies.py`
  (`get_x_service` factories + `XServiceDep` aliases; the ADR 0018 port swap at
  :207-219 is the registration precedent).
- **F8 — Infra + gates.** `docker-compose.yml` pairs: `postgres` (named
  volume) / `postgres-test` (tmpfs, :5433), `kafka` / `kafka-test` (:9093) —
  `clickhouse` / `clickhouse-test` mirrors them. `check-api` =
  db-test-up + redis-test-up + kafka-test-up + ruff (incl. `../../workers`) +
  pyright + pytest (Makefile:68-72); `check-e2e` = db-up redis-up kafka-test-up
  db-migrate db-seed + `tests/e2e/full-hop.sh` (Makefile:77-78). Worker
  recipe: `cd $(API_DIR) && PYTHONPATH="../..:." uv run python -m workers.<w>.main`.
- **F9 — Test wiring.** `tests/conftest.py` pins DB/Redis/Kafka test URLs
  before any `app.*` import (add `CLICKHOUSE_URL` there); integration conftest
  migrates + truncates per test, `client_factory` overrides `get_identity`;
  real-broker exemplar `test_notifications_consumer_kafka.py` (fresh group,
  bounded-poll awaits, `RecordingSleeper`); worker-loop unit exemplar
  `tests/unit/workers/test_realtime_consumer.py`. Latency asserts so far live
  only in e2e (`command-center.spec.ts:92-94`) — the spec's benchmark AC
  therefore lands as one bounded integration test (R1).
- **F10 — Web.** Nav: `components/destinations.ts` `NAV_ITEMS` `roles?`
  filtered in `main-nav.tsx:19-21` (the admin-only Settings precedent).
  Feature exemplar `features/overtime/` (`api.ts` keys + invalidate,
  `hooks/` thin wrappers mapping `ApiError.code` → message, components).
  orval: `orval.config.ts` → `packages/api-client/src/index.ts`, react-query
  client; `make gen-api` regenerates. `components/ui/` has select,
  dropdown-menu, table (`containerLabel`), dialog, … — **no Tabs, no
  Checkbox, no date picker**; `hooks/use-debounced-value.ts` exists; **no
  chart lib** (Recharts is PO-approved, rule-10 justification recorded in
  Decisions 9).

### Ordered tasks

**T1 — Event payload extensions (contract → producer).**
- `contracts/events/employees/employee-created.v1.json` — add optional
  `hired_at` (`{"type": ["string","null"]}` date pattern; ADR 0013 wording).
  `contracts/events/employees/employee-transferred.v1.json` — add optional
  `to_position_id`, `to_location_id` (`["string","null"]`). `required` lists
  and `additionalProperties: false` untouched.
- `domains/employees/events.py` — builders accept and fill the new fields;
  `domains/employees/service.py:220-231` passes `hired_at=created.hired_at`;
  `:397-407` passes `to_position_id=position_id`, `to_location_id=location_id`.
*Tests:* `tests/contract/test_employee_events.py` — new builder cases validate
against the extended schemas; a strip-the-new-fields pre-extension payload
still validates (F4 precedent); tamper tests reject wrong types.
*Verify:* `make check-api` green; no non-contract diff outside the two
call sites.

**T2 — ADR 0019 (docs, before any ClickHouse code).**
- `.ai/decisions/0019-clickhouse-projection-and-reports.md` pinning:
  ClickHouse as the Phase-4 analytical projection (why now: the spec's <1s
  metric); table design — per-entity current-state `ReplacingMergeTree` rows
  (`fact_overtime`, `fact_attendance`, `fact_leave`, `dim_employee`,
  `dim_assignment` intervals) with dimension stamps resolved at fold time;
  queries aggregate with `FINAL`/dedupe semantics; consumer semantics — group
  `workpulse-reports`, `auto_offset_reset="earliest"`, manual commit **after**
  the ClickHouse write, skip malformed/unknown, single instance; idempotency =
  convergent ordered per-entity folds (redelivery = identical upserts — the
  realtime "harmless identical upserts" reading of ADR 0012 decision 5's
  event_id dedupe requirement); query-time `GROUP BY` (no materialized views —
  the named upgrade); rebuild = truncate + replay from `earliest`, with
  `event_outbox` (ADR 0012 decision 8) as the expired-topic replay source;
  label + manager-scope read-only ports (ADR 0015 carve-out pattern);
  `clickhouse-connect` dependency (ADR 0006's pre-agreed gate).
*Tests:* none (docs-only). *Verify:* diff touches only `.ai/decisions/`.

**T3 — ClickHouse infra (compose, DDL, dependency, wiring).**
- `docker-compose.yml` — `clickhouse` (named volume, loopback, `:8123` HTTP,
  `/ping` healthcheck) + `clickhouse-test` (tmpfs, `:8124→8123`).
- `database/clickhouse/` — idempotent DDL (`CREATE TABLE IF NOT EXISTS`):
  `fact_overtime(tenant_id, overtime_id, employee_id, work_date, hours,
  status, department_id, position_id, location_id, hired_at)` etc. plus the
  `projection_state` rebuild marker per ADR 0019;
  `app/infrastructure/clickhouse/schema.py` applies it at startup (the worker
  must succeed; the API is best-effort so ClickHouse down never blocks boot),
  `client.py` wraps `clickhouse-connect`.
- `apps/api/pyproject.toml` — `uv add clickhouse-connect` (rule-10
  justification: ADR 0006 gate + this spec). `app/config.py` —
  `clickhouse_url`; `.env.example` updated.
- `Makefile` — `clickhouse-up/clickhouse-down/clickhouse-test-up`;
  `check-api` gains `clickhouse-test-up`; `tests/conftest.py` pins
  `CLICKHOUSE_URL` to the test port before `app.*` imports (F9).
*Tests:* `tests/integration/test_clickhouse_schema.py` — client connects to
  clickhouse-test, DDL applies idempotently (twice), tables exist.
*Verify:* `make check-api` green with the new service up.

**T4 — Projection domain: folds + write port (pure + adapter).**
- `apps/api/app/domains/reports/folds.py` — pure, I/O-free fold rules:
  seniority band from `hired_at` at a reference date (`lt_1y|1_3y|3_5y|
  5y_plus|unknown`); dimension-interval transitions (create / transfer-close+open
  / terminate-close) with the pre-extension degradations (rule 3); a transfer
  fold returns the bounded re-stamp range (facts dated on/after
  `effective_from`) that the projection writer executes (backdated-transfer
  correction, ADR 0019 decision 5); overtime state machine
  (`requested|manager_approved|hr_verified|
  rejected|withdrawn` per `overtime_id`, un-verify removal semantics,
  `included_in_payroll` invisibility); attendance record merge (checked_in +
  checked_out → one row); leave approved-days row. All pure functions of
  (prior row, envelope).
- `domains/reports/repository.py` — `ProjectionWriter` Protocol (upsert
  per-entity rows + the bounded per-employee fact re-stamp on transfer,
  tenant-scoped) + `ReportsRepository` Protocol (aggregate queries);
  `domains/reports/schemas.py` — fold-row DTOs;
  `domains/reports/exceptions.py` — `ReportsUnavailableError`
  (`503 reports_unavailable`).
- `app/infrastructure/clickhouse/projection.py` — `ClickhouseProjectionWriter`
  implementing upserts against the ADR 0019 tables (the worker's output port).
*Tests:* `tests/unit/reports/test_folds.py` — band boundaries (anniversary
  dates), backdated-transfer re-stamp (facts dated on/after `effective_from`),
  un-verify drops hours, payroll-flip invisibility,
  pre-extension `unknown` band + stale dims, check-in-only record, duplicate
  application is a no-op (idempotence); `tests/integration/
  test_clickhouse_projection.py` — writer round-trips rows, re-writes converge
  (redelivery shape), and the transfer re-stamp corrects facts dated on/after
  `effective_from`.
*Verify:* `make check-api` green.

**T5 — Reports read API (contract pinned in code) + `make gen-api`.**
- `domains/reports/schemas.py` — the pinned request/response models
  (`ReportGroup`, the four row shapes, the four report envelopes; decimals as
  ADR 0017 strings); query model with validators (`from ≤ to`, span ≤ 24
  months, `group_by` 1..3 distinct dims, `leave_type` leave-only) → house
  `422 validation_error`.
- `domains/reports/policies.py` — `ensure_can_read_reports` (Employee →
  `ForbiddenError` 403; manager → scoped). `domains/reports/ports.py` —
  `ReferenceLabels` + `EmployeeScope` read-only Protocols (F7);
  `app/infrastructure/postgres/reference_labels.py` +
  `manager_scope.py` adapters (F6's identical scope query, read-only).
- `domains/reports/service.py` — pure metric math (hours_per_employee,
  late_rate, avg_minutes_late, half-even rounding, band assignment +
  cross-row merge) over repository aggregates; no wall-clock.
- `app/infrastructure/clickhouse/reports.py` — `ClickhouseReportsRepository`:
  gates every query on `projection_state.ready` (`503 reports_unavailable`
  while a rebuild is in progress), then `GROUP BY` over stamped fact columns
  with bucket expressions
  (`toStartOfMonth` / `toStartOfWeek(…, 1)` Monday / `toStartOfDay`), fact
  filters `tenant_id` + `[from,to]` + optional dim filters + manager scope
  employee set; headcount as-of `min(bucket_end, to)` from
  `dim_employee`/`dim_assignment`, using the first assignment interval as the
  hire anchor when `hired_at` is null (pre-extension); ClickHouse client
  errors → `ReportsUnavailableError`.
- `app/api/v1/reports.py` — four GET routes, operationIds
  `getOvertimeReport|getAttendanceReport|getLeaveReport|getHeadcountReport`;
  `app/dependencies.py` — `get_reports_service` + `ReportsServiceDep` +
  port wiring (F7 precedent). Then `make gen-api` (openapi.json +
  `@workpulse/api-client` hooks).
*Tests:* `tests/integration/test_reports_api.py` — seed projected rows through
  the T4 writer (AC 1/3/4 exact aggregates incl. mid-range transfer split and
  `leave_type`/headcount semantics), `422` cases, Employee `403`, manager
  scoping (two managers' fixtures), tenant isolation (empty, not 403),
  unknown/foreign filter ids → empty rows, ClickHouse-down → 503 (client
  stub raising), `projection_state.ready = 0` → 503, pre-extension headcount
  fallback, PII sweep over responses; `tests/integration/
  test_reports_benchmark.py` — flagship query over the seeded volume < 1s
  (`time.perf_counter`, R1). OpenAPI drift test covers the contract pin.
*Verify:* `make check-api` green and `make gen-api` diff-free.

**T6 — `workers/reports/` projection worker.**
- `workers/reports/{main.py,consumer.py,mapping.py}` — F1 shape: group
  `workpulse-reports`, `auto_offset_reset="earliest"`, manual commit after the
  ClickHouse write, `_parse`/skip for malformed and unknown types
  (`consumer_message_skipped`, ids-only), envelope → fold command in
  `mapping.py`, fold application through `ClickhouseProjectionWriter`,
  `reports_started` readiness line, SIGINT/SIGTERM, `app/config.py`
  `reports_consumer_group`.
- `Makefile` — `dev-reports` (F8 recipe); `scripts/dev.sh` gains the worker +
  `clickhouse-up` so `make dev` shows real reports.
- `workers/relay/publisher.py` — topic creation sets `retention.ms` covering
  the 24-month maximum report span plus a margin (ADR 0019); the reports
  consumer applies the transfer re-stamp after folding the new interval.
*Tests:* `tests/unit/workers/test_reports_consumer.py` (loop with in-memory
  stubs: malformed/unknown skip, commit-after-write ordering);
  `tests/integration/test_reports_consumer_kafka.py` (real kafka-test +
  clickhouse-test): a produced event slice projects exact rows; whole-stream
  redelivery leaves aggregates identical (AC 9); `hr_verified → rejected`
  removes hours; truncate + replay reproduces aggregates (AC 10) with the
  `projection_state.ready` fence toggled by the test; retention covers the
  maximum report span at topic creation (`retention.ms`);
  pre-extension envelopes degrade per rule 3.
*Verify:* `make check-api` green.

**T7 — Web Reports workspace (`apps/web/`).**
- `pnpm add recharts` in `apps/web/` (PO Decision 9) +
  `components/ui/tabs.tsx` via the `shadcn` skill (the one new primitive;
  rule 15 loads `workpulse-ui` + `shadcn` for this task).
- `features/reports/` — `api.ts` (query keys `[reports, kind, params]` +
  invalidation), `hooks/use-reports.ts` (thin wrappers over
  `useGetOvertimeReport`… mapping `ApiError.code === "reports_unavailable"`
  / 403 to messages), `types.ts`, `utils.ts` (band labels, decimal metric
  formatting, bucket → chart-series pivot), `components/`
  (`ReportsWorkspace` + tab views + shared `ReportFilters`
  (`Input type="date"` from/to, `Select` interval, `DropdownMenu` +
  `DropdownMenuCheckboxItem` group-by in selection order, dim `Select`s fed
  from the generated organization hooks) + `ReportCharts` (Recharts,
  `--chart-1..5`) + `ReportTable` (`containerLabel` + swipe hint per the
  presentation rules)).
- `app/(dashboard)/reports/page.tsx` (RSC shell) +
  `components/destinations.ts` nav entry (F10) with
  `roles: ["manager","hr","finance","admin","super_admin"]`.
*Tests:* `apps/web/tests` — utils math (pivots, formatting, band labels),
  hook param mapping (query-key change on filter change), workspace renders
  loading/empty/error (incl. `reports_unavailable` and denied states) and
  charts+tables agree on rows; no ad-hoc fetch.
*Verify:* `make check-web` green.

**T8 — E2E hop + fixtures + record.**
- `Makefile` `check-e2e` gains `clickhouse-test-up`;
  `tests/e2e/full-hop.sh` exports `CLICKHOUSE_URL`, adds a
  `reset_reports_fixtures` (deterministic overtime/attendance/leave facts,
  F9 reset pattern) and starts `workers/reports` with the `reports_started`
  readiness wait alongside relay/realtime/payroll.
- `tests/e2e/reports.spec.ts` — HR (or admin) login → `/reports` → overtime
  tab shows non-empty rows for the seeded verified overtime after the worker
  projects (bounded poll), switching tabs/filters refetches; `worker@` login
  → no Reports nav and a direct `/reports` visit renders the denied state
  (AC 13/14).
- Record: tick the AC boxes, set `Status: done`, move the
  `.ai/features/README.md` backlog row to `done`, add the `tasks/TODO.md`
  Done entry.
*Verify:* `make check` green end-to-end (all three gates).

### Test strategy

| Gate | Covers | Failure cases |
| --- | --- | --- |
| `make check-api` | T1 contract tests; T3 schema smoke; T4 fold units + writer round-trip; T5 API integration + benchmark + OpenAPI drift; T6 worker units + real-broker proofs | pre-extension payload rejection (types), wrong-state fold (un-verify), 422 range/group_by, Employee 403, cross-tenant empty, 503 on ClickHouse down, redelivery/rebuild divergence, malformed-message stall |
| `make check-web` | T7 utils/hooks/component tests | empty/error/denied states, `reports_unavailable` mapping, query-key drift on filter change |
| `tests/e2e/` (`make check-e2e`) | T8 full hop (ClickHouse + reports worker + the existing workers) | employee blocked from the surface; report empty when the worker has not projected (bounded wait proves the opposite); hop regression for relay/realtime/payroll |

Mandatory rules honoured: failure case per surface (503/422/403/denied/
malformed), tenant isolation test (T5), denied-role test (T5/T7/T8), no PII
sweeps on rows/logs/responses (T5/T6), time injected (no wall-clock in fold or
metric math; the single bounded latency benchmark is R1), deterministic
fixtures (T8 reset script).

### Risks & unknowns

- **R1 — Latency benchmark in pytest.** House precedent keeps wall-clock out
  of pytest (F9). The spec's AC names an integration benchmark, so T5 adds
  exactly one: `perf_counter` < 1s over the seeded volume — ClickHouse answers
  this in milliseconds, so the bound is ~100× headroom. If it ever flakes on a
  loaded machine, widen the seed, not the bound, and say why.
- **R2 — `ReplacingMergeTree` + `FINAL`.** Correctness relies on every report
  query reading deduped rows (`FINAL` or `argMax`); at v1 volumes this is
  cheap. Documented in ADR 0019; a query that forgets `FINAL` shows up as
  doubled aggregates in T5/T6 tests.
- **R3 — Hop weight.** `check-e2e` now starts Kafka + ClickHouse + four
  workers; startup time and port collisions are the gate's flake risk.
  Mitigations: tmpfs test services, readiness log waits (existing pattern), no sleeps.
- **R4 — Band/bucket semantics.** Tenure at bucket end (`min(bucket_end, to)`)
  and `active_employee_count` = employees whose employment **overlaps** the
  bucket are the plan's precise readings of spec rules 4/8/9; both are unit-
  and integration-tested so any later reinterpretation is a one-place change.
- **R5 — Pre-existing events.** Dev/topic history built before T1 lacks
  `hired_at`/`to_position_id` → `unknown` band and stale dims (rule 3). Seeds
  and e2e fixtures are fresh, so this only affects replayed legacy envelopes —
  the degradation is tested, not hidden.
- **R6 — Dependency debt.** A fifth `apps/api`-env worker keeps ADR 0012
  decision 10's uv-workspace deferral alive; revisit when worker count next
  grows (noted in ADR 0019). `clickhouse-connect` and `recharts` are the only
  new dependencies (both spec-justified).
- **Unknowns:** none blocking — PO Decisions 1–9 resolved 2026-10-22; the
  remaining precision calls (R4) are pinned here and tested.
