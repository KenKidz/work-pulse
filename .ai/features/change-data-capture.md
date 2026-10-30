# Feature: Change Data Capture (Debezium projection pipeline)

- **Status:** done
- **Phase:** 5
- **Size:** L
- **Surfaces:** api+web+events+workers
- **Links:** `.ai/architecture.md` (Phase 5 — "`PostgreSQL ──► Debezium ──► Kafka ──► ClickHouse` — replaces hand-written analytics projections"; roadmap row 5) · `tasks/doc.md` §10 "Advanced Infrastructure Enhancements" + §11 V5 · ADR 0019 (ClickHouse projection model this amends) · ADR 0012 (outbox/`workpulse.events` — untouched backbone) · ADR 0007 (soft-delete semantics — no hard deletes on captured tables) · `.ai/features/analytics-reports.md` (the projection and reports being replaced) · `.ai/security.md` (PII handling) · `.ai/domain.md` (Analytics & Reports = pure consumer)

## Problem

The Analytics & Reports projection is a hand-written fold of `workpulse.events`
(`workers/reports/`: `mapping.py` envelope→command, `folds.py` command→rows):
every projected value is re-derived from event payloads by application code, and
every new analytical entity needs more fold code. Phase 5 moves the projection
to the database log: PostgreSQL row changes are captured by Debezium, streamed
through Kafka, and landed in the ADR 0019 ClickHouse projection tables — the
projection becomes a data pipeline instead of application code, and a rebuild
comes from snapshot + WAL replay instead of topic replay.

## Business rules

1. **CDC owns the analytics projection (PO 2026-10-29).** The pipeline
   `PostgreSQL → Debezium (Kafka Connect) → Kafka → ClickHouse` fills the five
   ADR 0019 projection tables (`dim_employee`, `dim_assignment`, `fact_overtime`,
   `fact_attendance`, `fact_leave`) and their `projection_state` gate. The
   hand-written fold in `workers/reports/` is retired at cutover (rule 2).
2. **Parity before retirement (PO 2026-10-29).** Both paths run in parallel
   (CDC lands in a shadow set); a parity harness proves identical report
   aggregates from identical API writes before the fold is removed. The fold
   stays the serving projection until parity is green.
3. **PII never enters the CDC stream (PO 2026-10-29).** Debezium column include
   lists restrict every captured row to identifiers, state and timestamps —
   the same rule as event payloads (`.ai/security.md`): no names, emails,
   phones, salaries, reasons, decision notes or document metadata cross into
   Kafka or ClickHouse via CDC. There is no security carve-out.
4. **Captured tables are exactly the sources of the five projection tables:**
   `employees`, `employments`, `attendance_records`, `overtime_requests`,
   `leave_requests`, plus `leave_types` (type-code reference for
   `fact_leave.leave_type_code`). Nothing else is captured.
5. **Every projected column is derivable from captured row state.** The pipeline
   never re-implements domain rules. Where a projected value exists only in
   event payloads today (`minutes_late`), the OLTP row gains it via an additive
   migration — it is already computed at check-in (`.ai/domain.md`/ADR 0009)
   and only the event carries it. `attendance_records` already stores `late`,
   `duration_minutes`, `checked_in_at`/`checked_out_at`; `leave_requests.days`
   is `approved_day_count`; `employees.hired_at`/`terminated_at` and the
   `employments` interval columns cover the dimension rows.
6. **Dimension attribution is as-of the fact's business date** — the fold's
   `stamp_for` semantics (resolved per fact date, backdated-transfer restamp,
   ADR 0019 decision 5): each fact attributes to the `employments` interval in
   effect on its work date; a backdated transfer corrects attribution through
   the captured history rows with no restamp pass. Facts with no matching
   interval attribute as `unknown` dimensions — never dropped (analytics-reports
   rule 3). The representation (stamped columns vs query-time joins) is pinned
   by ADR 0024 with parity (rule 2) as the arbiter.
7. **Replacement version = source commit timestamp.** A projection row's
   `updated_at` is the source transaction's commit timestamp from the Debezium
   envelope — never ingest wall clock. `ReplacingMergeTree(updated_at)` makes
   at-least-once delivery idempotent: a redelivered change replaces, never
   duplicates (ADR 0019 decision 2 semantics preserved).
8. **The projection never deletes rows.** Reference data is soft-deleted
   (ADR 0007); a delete operation is dropped, counted in a pipeline metric,
   and never removes a projection row.
9. **The domain-event backbone is untouched.** The outbox + relay and
   `workpulse.events` (ADR 0012) keep powering realtime, notifications,
   workflow and audit; CDC serves analytics only. The write path gains no dual
   write — CDC tails the WAL.
10. **Report reads stay gated on projection readiness.** The existing
    `ReportsUnavailableError` (`503`) semantics are preserved; readiness flips
    only after the connector's initial snapshot completes.
11. **Tenant isolation end-to-end.** `tenant_id` is captured on every row and
    enforced in the report repositories exactly as today (`.ai/security.md`).
12. **Pipeline health is metrics-first** (Prometheus + Grafana, ADR 0023):
    connector state, replication-slot/ingest lag, dropped-delete count and sink
    errors. No web surface for pipeline health.

## Scope

- `ops/cdc/`: Debezium connector configuration + ClickHouse sink composition
  (proposed location — `ops/` holds provisioned infra assets, the
  `ops/observability/` precedent).
- `docker-compose.yml`: `kafka-connect` + `kafka-connect-test` (Debezium image)
  pair wired to the existing `kafka`/`kafka-test` and `clickhouse`/
  `clickhouse-test` services; `make cdc-up` / `make cdc-down`
  (naming per `observability-up`).
- `contracts/cdc/` (proposed): JSON schema for the captured-row change
  envelope (op, before/after restricted column maps, commit timestamp, source
  LSN) — contract before pipeline.
- Landing layer: captured rows → the five ADR 0019 projection tables + a parity
  shadow set during cutover (rule 2; mechanics are a plan/ADR choice).
- Additive migration: `attendance_records.minutes_late` (rule 5).
- Reports repository queries re-pointed at CDC-fed tables under rule 6
  semantics; the report API shapes are frozen (below).
- Parity harness: identical API writes (including transfers and a backdated
  transfer) drive fold path + CDC path to quiescence; aggregates must match.
- Cutover: after parity, remove the `workers/reports/` fold, update
  `analytics-reports.md` acceptance criteria, and amend ADR 0019 via **ADR 0024**
  (attribution representation, rebuild = snapshot + WAL, `updated_at` = commit
  ts, `minutes_late` source).
- Tests on every touched surface (below) plus config-gate checks — captured-
  table list and PII include-list — following the observability config-gate
  precedent.

## Out of scope

- CDC as a general-purpose replica/audit/search change stream for
  non-analytics sinks — future features.
- Capturing any other domain tables (payroll, recruitment, performance,
  notifications, documents, identity).
- DDL/schema-evolution replication beyond additive columns the sink ignores.
- Exactly-once delivery guarantees; the contract is at-least-once + idempotent
  replacement (rule 7).
- Migrating realtime/notifications/workflow consumers off `workpulse.events` —
  they are untouched (rule 9).
- Any web UI: pipeline health lives in Grafana (rule 12).
- Production-grade Connect cluster operations (sharding, multi-instance
  tuning) — Phase 8.
- Replacing `event_outbox` or the relay — unchanged (rule 9).

## Surfaces & files

| Surface | Location |
| --- | --- |
| CDC pipeline / ops | `ops/cdc/` (connector + sink config); `docker-compose.yml` (`kafka-connect`, `kafka-connect-test`); `Makefile` (`cdc-up`/`cdc-down`) |
| Contracts | `contracts/cdc/` (change-envelope schemas; proposed location) |
| Migration | `apps/api/alembic/` (append-only; additive `attendance_records.minutes_late`) |
| API domain | `apps/api/app/domains/reports/` (repository queries re-pointed; readiness gate unchanged) |
| API infrastructure | `apps/api/app/infrastructure/clickhouse/` (projection writer replaced by the pipeline) |
| Workers | `workers/reports/` (fold removed at cutover; parity harness runs against it until then) |
| Web feature | `apps/web/features/reports/` — unchanged (parity proven through the existing UI) |
| Web routes | `apps/web/app/(dashboard)/reports/` — unchanged |
| API tests | `apps/api/tests/unit` (fold retirement), `tests/integration` (parity, attribution, tenant isolation, ready gate), `tests/contract` (CDC envelope vs `contracts/cdc/`) |
| E2E | `tests/e2e/reports.spec.ts` (expectations unchanged; CDC pipeline in the hop) |
| Config gate | captured-table + PII include-list checks in `make check-api` (observability precedent) |

## Roles & access

No new endpoints, no new mutations, no new roles. The four report routes keep
their existing RBAC exactly as `analytics-reports.md` pins (manager self +
direct reports; HR/Finance/Admin company-wide; employee denied) and the
existing denied-role and tenant-isolation tests keep passing against CDC-fed
data. Nothing here is a sensitive mutation, so no new audit entries.

## API contract

No new routes and no shape changes — parity requires the contract to freeze:

- `GET /api/v1/reports/overtime` · `/api/v1/reports/attendance` ·
  `/api/v1/reports/leave` · `/api/v1/reports/headcount` — request and response
  shapes exactly as pinned in `analytics-reports.md` and
  `@workpulse/api-client`.
- Failure: while `projection_state.ready = 0`, report reads fail with the
  existing `ReportsUnavailableError` (`503`, stable code) — never silently
  empty numbers.
- Internal change only: repository SQL resolves dimensions per rule 6.

## Events

This feature adds **no domain events** and touches none of the
`contracts/events/` catalog. It adds **CDC change topics** (a different stream):

- One topic per captured table: `workpulse.cdc.<table>` (proposed Debezium
  `topic.prefix`), e.g. `workpulse.cdc.attendance_records`.
- Payload: the Debezium change envelope — `op` (`c`/`u`, `r` for snapshot
  rows), restricted `before`/`after` column maps, source commit timestamp and
  LSN — with columns limited by rule 3's include list. The envelope schema is
  documented in `contracts/cdc/` before the connector ships (contract before
  pipeline), and a contract test validates real captured messages against it.

## Edge cases

- **Backdated transfer after facts exist:** as-of attribution (rule 6)
  corrects automatically from the captured `employments` rows — the fold's
  restamp pass (ADR 0019 decision 5) disappears. Parity covers this case.
- **Mid-period transfer:** work on either side of `effective_from` attributes
  to the department/position/location in effect on each work date, on both
  paths.
- **Hard delete op (should not occur — ADR 0007):** dropped and counted in the
  dropped-delete metric; no projection row is ever removed (rule 8).
- **Redelivery / connector restart:** the same commit timestamp upserted twice
  replaces; aggregates unchanged (rule 7).
- **Initial snapshot on a populated database:** snapshot rows arrive as
  `op: r`; readiness (rule 10) flips only after the snapshot completes, so
  reports never read a half-populated projection.
- **Cross-topic out-of-order arrival** (fact change before its dimension
  change): facts are self-contained rows and attribution resolves at read time,
  so a late dimension row corrects attribution on the next query without
  restamping. A transient mismatch while streaming is accepted; parity is
  proven at quiescence.
- **Sink/connector down:** Postgres WAL accumulates at the replication slot —
  lag metric (rule 12) plus a documented ops recovery note; no data loss within
  WAL retention.
- **New column added to a captured table by a future migration:** ignored
  unless added to the include list; adding a PII column to the include list
  fails the config gate (rule 3).
- **Pre-migration attendance rows:** `minutes_late` is `null` for rows written
  before the rule-5 migration and a CDC rebuild reproduces `null` — the fold's
  event-derived value is unrecoverable from row state. Accepted: parity is
  proven on post-migration writes, and the analytics-reports rebuild guarantee
  is amended accordingly (ADR 0024).
- **Facts with no dimension history** (stream started after the employee was
  created, missing `employments`): attributed `unknown`, never dropped
  (rule 6 / analytics-reports rule 3).
- **Fresh tenant / empty database:** empty snapshot, reports show existing
  empty states.

## Acceptance criteria

- [x] Pipeline (happy): given the stack is up via `make cdc-up`, when an API
      write touches a captured table, then the change lands in the projection
      tables and the report aggregates include it.
- [x] API (parity): given identical seeded API writes (transfers + a backdated
      transfer, the overtime state machine, approved leave) drive both paths to
      quiescence, when the four reports are queried, then the CDC projection
      returns aggregates identical to the fold projection.
- [x] API (attribution, edge): given an employee with a mid-period transfer and
      a backdated transfer, when reports aggregate by department, then each
      fact attributes to the assignment in effect on its business date — on
      both paths.
- [x] API (tenant isolation): given captured rows for two tenants, when reports
      run as tenant A, then no tenant B fact or dimension appears (existing
      isolation tests re-run against CDC-fed data).
- [x] API (failure): given `projection_state.ready = 0`, when a report is
      requested, then the API fails with the existing `ReportsUnavailableError`
      code — never empty numbers.
- [x] Pipeline (failure): given a delete op on a captured table, when the sink
      processes it, then no projection row is removed and the dropped-delete
      counter increments.
- [x] Pipeline (idempotency): given a redelivered change (same commit
      timestamp), when it is upserted twice, then the aggregates are unchanged.
- [x] Config gate (PII): given a change adds a PII column (e.g.
      `employees.email`, `leave_requests.reason`) to the connector include
      list, when the gate runs, then it fails naming the column.
- [x] Config gate (scope): the connector's captured-table list equals the
      rule-4 set exactly — no more, no less.
- [x] Contract: the change-envelope schema exists under `contracts/cdc/` and a
      contract test validates captured messages against it — identifiers and
      state only, no PII fields.
- [x] E2E: with ClickHouse + the CDC pipeline in the hop, an HR login opens
      Reports and sees non-empty correct aggregates — the existing
      `tests/e2e/reports.spec.ts` expectations pass unchanged.
- [x] Cutover: after parity is green, the `workers/reports/` fold is removed,
      `make check` is green (API/web/e2e), `analytics-reports.md` acceptance
      criteria are updated to CDC semantics, and ADR 0024 amends ADR 0019
      (attribution representation, rebuild = snapshot + WAL, `updated_at` =
      commit timestamp, `minutes_late` source).
- [x] Observability: connector state, lag, dropped deletes and sink errors are
      exposed on the existing Prometheus target conventions and visible in
      Grafana (ADR 0023 conventions).

## Decisions (PO, 2026-10-29)

1. **CDC replaces the hand-written reports projection.** Debezium captures the
   projection source tables → Kafka → ClickHouse fills the ADR 0019 tables; the
   `workers/reports/` fold retires. *Rationale:* the architecture roadmap
   states CDC "replaces hand-written analytics projections" literally; the
   event-contract pressure the product depends on stays because the
   `workpulse.events` backbone and its consumers are untouched (rule 9).
   Projection semantics shift from event-time stamps to row state with as-of
   attribution (rule 6), so ADR 0019 gets amended at cutover.
2. **PII is excluded at the connector.** Column include/exclude lists keep PII
   columns out of Kafka entirely; captured rows carry identifiers and state
   only. *Rationale:* keeps `.ai/security.md`'s "minimal event payloads / no
   PII in analytics rows" rules intact with no carve-out to police.
3. **Parity, then retire.** CDC runs alongside the fold (shadow set), a parity
   harness proves identical aggregates from identical writes, and only then is
   the fold removed. *Rationale:* protects ADR 0019's rebuild guarantee while
   the semantics change; the old code stays until equivalence is proven.

### Implementation defaults (the plan and ADR 0024 may refine)

- Topic naming `workpulse.cdc.<table>` via Debezium `topic.prefix`.
- Infra under `ops/cdc/`; compose pair `kafka-connect` / `kafka-connect-test`;
  `make cdc-up` / `make cdc-down`.
- Envelope contracts under `contracts/cdc/` (new directory alongside
  `contracts/events/`).
- Sink mechanism — ClickHouse Kafka engine + materialized views vs. the
  official ClickHouse sink connector vs. a thin `workers/cdc-sink/` consumer —
  is the plan's choice; the behavioral rules above are the contract either way.
- Parity mechanics — shadow tables vs. a shadow ClickHouse database — is the
  plan's choice; the shadow set is dropped at cutover.

## Plan

The API contract is frozen (rule: no routes, no shape changes) — parity forbids
drift, so there is nothing to pin in code before the pipeline; the **CDC
change-envelope contract lands at T1**, before any connector (T3) or sink (T4)
— "contract before producer or consumer" holds on the new stream. The
additive `minutes_late` migration (T2) precedes the sink and the parity
harness because rule 5 makes the column a projection source. Read-side
re-pointing (T5) lands before the parity harness (T6) so both paths are
compared through **one** query formulation. Real Debezium enters the e2e hop
while the fold still serves (T7), so cutover (T9) is a flip, not a leap.
Everything orders so `make check` stays green after every task.

### Acceptance criteria → changes (restated)

| # | Criterion (restated) | Satisfied by |
| --- | --- | --- |
| 1 | `make cdc-up` + API write → change lands in the projection tables, reports include it | T3 `cdc-up`/connector; T4 sink landing; T7 real-Debezium hop proof |
| 2 | Identical writes (transfers + backdated transfer, overtime state machine, approved leave) → CDC aggregates identical to fold aggregates | T6 parity harness (both consumers, one repository); T7 shadow-vs-serving comparison on real Debezium |
| 3 | Mid-period + backdated transfer attribute per business date, on both paths | T5 as-of join (D3); T6 attribution assertions; T7 |
| 4 | Two tenants → tenant A sees no tenant B facts/dimensions | T6 isolation assertions on CDC-fed reads (existing tests re-run pattern) |
| 5 | `projection_state.ready = 0` → existing `ReportsUnavailableError` (503), never empty numbers | T4 sink-owned readiness (D5); T6 ready-gate twin test; existing `ensure_ready` semantics untouched (T5) |
| 6 | Delete op → no projection row removed, dropped-delete counter increments | T4 drop-and-count + unit/integration tests |
| 7 | Redelivered change (same commit ts) upserted twice → aggregates unchanged | T4 `updated_at = source.ts_ms` (D4); T4 redelivery test |
| 8 | PII column added to the include list → gate fails naming the column | T3 gate + mutation tests against `ops/cdc/connector.json` |
| 9 | Captured-table list = rule-4 set exactly | T3 gate (exact-set assertion, mutation-tested) |
| 10 | Envelope schema in `contracts/cdc/` + contract test validates captured messages (ids/state only) | T1 schemas + tests; T7 recorded real Debezium fixtures re-validated |
| 11 | E2E with ClickHouse + CDC in the hop: HR sees non-empty correct aggregates; `reports.spec.ts` passes unchanged | T7 hop wiring + fixture row-writes; spec untouched |
| 12 | Cutover: fold removed, `make check` green, `analytics-reports.md` ACs updated, ADR 0024 amends ADR 0019 | T9 |
| 13 | Connector state, lag, dropped deletes, sink errors on Prometheus conventions, visible in Grafana | T4 sink counters; T8 poller gauges + Prometheus/Grafana + gate updates |

### Repo research findings the plan relies on

- **F1 — Fold retirement unit.** `workers/reports/{main,consumer,mapping}.py`
  (fetch → map → fold/write → commit; commit-after-write, head-of-line retry,
  `reports_started`) calls the pure folds in
  `apps/api/app/domains/reports/folds.py` through the `ProjectionWriter` port
  (`domains/reports/repository.py:37-78`) implemented by
  `ClickhouseProjectionWriter` (`app/infrastructure/clickhouse/projection.py`,
  instantiated at `workers/reports/main.py:63`). All four retire at cutover.
- **F2 — Landing targets.** `database/clickhouse/schema.sql` holds the five
  `ReplacingMergeTree(updated_at)` tables + `projection_state` (row
  `key='reports'`); `projection_state.ready` is read at
  `infrastructure/clickhouse/reports.py:76-89` (`ensure_ready`; missing row =
  served, `ready = 0` → `ReportsUnavailableError` 503) and written by no
  application code today — only ops/tests.
- **F3 — Read side kept.** `ClickhouseReportsRepository` (`reports.py`) reads
  stamped fact columns (`_fact_query`, `reports.py:430-470`) with the
  seniority SQL twin over `f.hired_at`; headcount already folds
  `dim_employee`/`dim_assignment` at read time in Python. The fold's
  `resolve_assignment`/`stamp_for` (`folds.py:203-246`) is the exact
  attribution semantics to preserve: latest interval with `effective_from <=
  fact_date`, earliest-interval fallback, `unknown` when no intervals exist.
- **F4 — Row sources (rule 5).** `attendance_records` has `late`,
  `duration_minutes`, `checked_in_at`/`checked_out_at` and **no**
  `minutes_late` (event-only today; `_minutes_late` computes it at check-in,
  `attendance/service.py:161-175,331-338`); `employments` is the interval
  source (`effective_from`/`effective_to`, close-only mutation);
  `leave_requests.days` = `approved_day_count`, `status` ∈
  `pending|approved|rejected|withdrawn` (approved terminal);
  `overtime_requests.status` ∈ `requested|manager_approved|hr_verified|
  included_in_payroll|rejected|withdrawn`; `leave_types.code` is the fact's
  type code.
- **F5 — Infra + gates.** Compose pairs `<svc>`/`<svc>-test` (tmpfs, loopback,
  healthchecks; `kafka`/`kafka-test` advertise `localhost:909x` — container
  clients need a second listener); `observability-up` is the `cdc-up` recipe
  template; the `.PHONY` line is gate-checked; `tests/unit/
  test_observability_config.py` is the stack-as-code gate precedent (YAML/JSON
  parsed with `safe_load`, mutation-style assertions, `Settings` port parity,
  dashboard `jsonschema` validation); worker metrics via
  `start_worker_metrics_server` on 9101–9105 with ADR 0023 label policy
  (`FORBIDDEN_METRIC_LABELS`, bounded instruments).
- **F6 — Contracts.** JSON Schema Draft 2020-12 under `contracts/` with
  `$id: https://workpulse.dev/contracts/…`; `tests/contract/` validates real
  builder output with `Draft202012Validator` + a recursive PII-marker sweep +
  `additionalProperties: false` tamper tests (`test_employee_events.py`).
- **F7 — Tests.** `tests/conftest.py` routes to :5433/:9093/:8124 (clickhouse
  test DB `workpulse_test`); integration conftest migrates + truncates per
  test; `test_reports_consumer_kafka.py` is the real-broker consumer exemplar;
  `test_clickhouse_projection.py` drops/recreates projection tables around
  tests; `tests/e2e/full-hop.sh` resets fixtures before workers start —
  `reset_reports_fixtures` truncates the projection and publishes synthetic
  envelopes for a fixed-id synthetic People Ops employee ("No OLTP row is
  touched") with the e2e report window set to its fixed dates.
- **F8 — Migrations + dev wiring.** Alembic linear string ids, head
  `0019_documents` → next is `0020`, append-only, model/`__table_args__`
  parity (`0009_notification_read_state.py` additive-column template);
  `scripts/dev.sh` launches workers as `python -m workers.<name>.main` with
  pinned `*_started` readiness lines.

### Implementation decisions the plan pins (ADR 0024 material)

- **D1 — Sink = thin `workers/cdc-sink/` consumer** (house worker pattern:
  commit-after-write, head-of-line retry, bounded labels), not ClickHouse
  Kafka-engine MVs or the Connect sink connector: it owns delete-drop
  counting, the leave-type reference dict, the readiness fence and the
  parity shadow target in testable code.
- **D2 — Parity mechanics = shadow ClickHouse database** (`workpulse_cdc`
  dev / `workpulse_test_cdc` test) with the same `database/clickhouse/schema.sql`
  applied; `Settings.cdc_projection_database` selects the sink target
  (shadow during parallel run; cutover flips to canonical and drops the
  shadow DBs). Same repository code reads both databases in the parity
  harness — the aggregate comparison is the rule-6 arbiter.
- **D3 — Attribution = query-time as-of resolution** (rule 6's join option):
  fact rows carry no live dimension stamps; the report SQL resolves
  `dim_assignment` exactly as `resolve_assignment` does (latest interval with
  `effective_from <= fact_date`, earliest-interval fallback, `unknown` when
  the employee has no intervals — never dropped). Stamped fact columns stay in
  the DDL (additive-only) but the CDC sink leaves them null and the read path
  retires them; the fold keeps writing them until cutover. No restamp pass
  exists on the CDC path — a late `employments` row corrects attribution on
  the next query (rule 6 edge case).
- **D4 — `updated_at` = `source.ts_ms`** (the envelope's source transaction
  timestamp) rendered `DateTime64(6)` UTC — never ingest wall clock (rule 7).
  Sub-millisecond same-row ties are accepted (ms-resolution `ts_ms`; two
  same-row updates within 1 ms do not occur on these write paths) and
  recorded in ADR 0024 with the `(commit_ts, lsn)` composite as the named
  fallback.
- **D5 — Readiness is sink-owned in its target database.** Fresh consumer
  group → upsert `projection_state` `ready = 0` before polling; flip
  `ready = 1` on snapshot-done evidence (`source.snapshot == "last"`, a
  Debezium heartbeat (empty-snapshot case), or the first non-snapshot change);
  restart with committed offsets never touches `ready`.
  `ops/cdc/register.sh` also writes `ready = 0` before creating a connector,
  closing the registration window. Missing-row semantics unchanged (F2).
- **D6 — Overtime status map (fold parity):** `requested|manager_approved|
  rejected|withdrawn` map 1:1; OLTP `included_in_payroll` projects as
  `hr_verified` (no event ever moved the fold past `hr_verified`, and the
  reports filter `status = 'hr_verified'`). `fact_leave` rows are written
  only for `status = 'approved'` rows (approved is terminal); other statuses
  produce no row and are not deletes.
- **D7 — `leave_type_code` reference:** resolved from the `workpulse.cdc.
  leave_types` stream by the sink's in-memory dict (warmed by the connector
  snapshot, refreshed on change events; FK ordering bounds the miss window).
  Unknown type id → head-of-line retry with backoff, then a bounded
  `"unknown"` fallback counted as a reference miss.
- **D8 — Captured column include lists** (rule 3/4 — the gate and the row
  contracts pin these exact sets): `employees`: id, tenant_id, hired_at,
  terminated_at · `employments`: tenant_id, employee_id, department_id,
  position_id, location_id, effective_from, effective_to ·
  `attendance_records`: id, tenant_id, employee_id, workday, duration_minutes,
  late, minutes_late · `overtime_requests`: id, tenant_id, employee_id,
  work_date, hours, status · `leave_requests`: id, tenant_id, employee_id,
  leave_type_id, start_date, days, status · `leave_types`: id, tenant_id, code.
  Names, emails, phones, salaries, reasons, notes, decision columns and
  document metadata never cross (no carve-out).
- **D9 — Envelope wire format:** Debezium JSON values with
  `value.converter.schemas.enable=false`; temporal fields keep Debezium's
  wire encoding (epoch-day dates / epoch-micro timestamps), `decimal.
  handling.mode=string` so `hours` is an ADR 0017 decimal string. Row
  contracts are strict (`additionalProperties: false`); the envelope wrapper
  allows Debezium's extra `source` fields.
- **D10 — Kafka container connectivity:** additive internal listener
  (`INTERNAL://kafka:29092`, `INTERNAL://kafka-test:29093` advertised as
  `kafka(-test):2909x`, `inter.broker.listener.name=INTERNAL`); host clients
  keep `localhost:909x` unchanged.

### Ordered tasks

**T1 — CDC change-envelope contract (`contracts/cdc/`) + contract tests.**
- `contracts/cdc/change-envelope.v1.json` — Draft 2020-12, `$id:
  https://workpulse.dev/contracts/cdc/change-envelope.v1.json`: `op`
  (`c|u|d|r|t`), `before`/`after` (object|null per op), `source`
  (`ts_ms`, `lsn`, `snapshot` `true|last|null`, `db`, `schema`, `table` enum
  of the six), per D9.
- `contracts/cdc/rows/{employees,employments,attendance_records,
  overtime_requests,leave_requests,leave_types}.v1.json` — strict row shapes,
  property sets exactly D8, `additionalProperties: false`.
*Tests:* `tests/contract/test_cdc_envelope.py` (F6 pattern): schema files
  validate; synthetic fixtures per op class × table validate envelope + row
  schema; row schemas reject an extra column (contract-level PII gate);
  recursive PII-marker sweep; tamper tests. Fixture dir
  `tests/contract/fixtures/cdc/` (synthetic now; recorded real messages in T7).
*Verify:* `make check-api` green; diff touches only `contracts/cdc/` +
`tests/contract/`.

**T2 — `attendance_records.minutes_late` (rule 5 additive migration).**
- `apps/api/alembic/versions/0020_attendance_minutes_late.py` —
  `op.add_column` nullable `minutes_late` (Integer), downgrade drops (F8
  template).
- `app/domains/attendance/models.py` — `minutes_late: Mapped[int | None]`.
- `app/domains/attendance/service.py` — check-in persists the same value the
  event carries (`_minutes_late(...) if late else None`, service.py:331-366);
  check-out leaves the column untouched (fold parity: a check-out only closes
  the record).
*Tests:* attendance integration tests — late check-in sets the column (==
  event value), not-late leaves null, check-out does not overwrite;
  pre-migration rows stay null (accepted, edge case).
*Verify:* `make check-api` green.

**T3 — Pipeline infra + config gates (`ops/cdc/`, compose, make).**
- `docker-compose.yml` — D10 listeners on `kafka`/`kafka-test`; new
  `kafka-connect` (Debezium Connect image pinned like `apache/kafka:3.9.1`;
  REST :8083; state on the kafka cluster) + `kafka-connect-test` (:8084,
  wired to `kafka-test`) per the `<svc>`/`<svc>-test` template (F5).
- `ops/cdc/connector.json` — the Postgres connector: `topic.prefix:
  workpulse.cdc`, `table.include.list` = the rule-4 six, per-table
  `column.include.list` FQNs = D8 sets, `snapshot.mode=initial`,
  `decimal.handling.mode=string`, heartbeat, JSON converters with
  `schemas.enable=false`.
- `ops/cdc/register.sh` — idempotent create/update of the connector
  (Connect REST; `CONNECT_URL` env); before creating: write
  `projection_state` `ready = 0` into the target ClickHouse database
  (`docker compose exec` clickhouse-client, `db-seed` pattern). `ops/cdc/
  README.md` — runbook: `cdc-up`/down ordering (sink first, then register),
  rebuild = snapshot + WAL (offset-reset procedure), sink-down → WAL
  accumulates at the slot (lag metric + recovery note).
- `Makefile` — `cdc-up`/`cdc-down` (+ `cdc-test-up`/`cdc-test-down`),
  `observability-up` recipe style (F5), added to `.PHONY`.
*Tests:* `tests/unit/test_cdc_config.py` (F5 gate pattern): captured-table
  set equals the rule-4 six exactly (mutation fails naming the delta); per-
  table include sets equal D8 and equal the `contracts/cdc/rows/*` property
  sets; adding a PII column (`employees.email`, `leave_requests.reason`,
  `overtime_requests.hr_note`) fails **naming the column** (mutation tests);
  compose wiring + host ports intact (extend the compose gate); cdc targets
  on `.PHONY`.
*Verify:* `make check-api` green (gate parses files only — no infra needed).

**T4 — CDC sink worker + landing layer + readiness (D1/D4–D7).**
- `workers/cdc_sink/{__init__,mapping,consumer,main}.py` — F1 worker shape:
  group `workpulse-cdc-sink` (`cdc_sink_consumer_group`), six
  `workpulse.cdc.<table>` topics + heartbeat, `auto_offset_reset="earliest"`,
  commit-after-write with head-of-line retry/backoff; `mapping.py` parses the
  envelope (contract-shaped models) → per-table upsert rows (`updated_at =
  source.ts_ms`); D6 status map + leave rules; D7 leave-type dict;
  `d`/`t` ops dropped and counted, never deleting rows; D5 readiness
  (fresh group → `ready = 0`; flip at snapshot-done evidence);
  `workpulse_cdc_dropped_deletes` / `workpulse_cdc_sink_errors` counters
  (ADR 0023 label policy) + `worker_unit` telemetry; `cdc_sink_started`
  readiness line; SIGINT/SIGTERM.
- `app/infrastructure/clickhouse/cdc_projection.py` — `ClickhouseCdcWriter`:
  target-database client (`cdc_projection_database`, shadow by default, D2),
  `CREATE DATABASE IF NOT EXISTS` + `schema.sql` apply on startup, per-table
  buffered upserts flushed once per fetched batch; the worker constructs it
  (F1 precedent).
- `database/clickhouse/schema.sql` — widen `fact_leave.approved_day_count` to
  `UInt32` (unpaid leave is not balance-capped, so a valid OLTP `days` must fit
  the projection) with an idempotent `ALTER` for existing projections; the
  row contract gains the matching maximum and ADR 0019's table line records
  the width.
- `app/config.py` — `cdc_sink_consumer_group`, `cdc_projection_database`,
  `cdc_connect_url`, `cdc_sink_metrics_port: int = 9106`; `.env.example`.
- `Makefile` `dev-cdc-sink`; `scripts/dev.sh` — eighth process +
  `cdc_sink_started` wait.
*Tests:* `tests/unit/workers/test_cdc_sink_consumer.py` +
`tests/unit/test_cdc_sink_mapping.py` — per-table mapping, D6 status map,
leave dict + unknown-id retry/fallback, delete-drop counter, commit-ts
versioning, readiness flip (snapshot records hold `ready = 0`; `last`/
heartbeat/first-change flips; restart with offsets leaves `ready` alone).
`tests/integration/test_cdc_sink.py` (real kafka-test + clickhouse-test
shadow DB) — envelopes land exact rows (AC-1 shape at the sink level);
redelivery of the same commit ts leaves aggregates unchanged (AC 7);
delete op removes nothing and increments the counter (AC 6); `ready` gate
(AC 5 mechanics); approved-only leave rows; `included_in_payroll` →
`hr_verified` parity (D6).
*Verify:* `make check-api` green.

**T5 — Reports read side: query-time as-of attribution (D3).**
- `app/infrastructure/clickhouse/reports.py` — `_fact_query` + the seniority
  SQL twin resolve dimensions via the D3 as-of join to `dim_assignment` /
  `dim_employee` (semantics byte-for-byte equivalent to `resolve_assignment`,
  F3); stamped fact columns no longer read; metrics, buckets, filters,
  tenant predicate, manager scope, `ensure_ready` and the `_query` →
  `ReportsUnavailableError` wrap unchanged; headcount and
  `_active_employee_counts` logic unchanged (already read-time).
*Tests:* existing `tests/integration/test_reports_api.py` +
  `test_reports_benchmark.py` must pass **unchanged** (they seed through the
  fold writer, which writes intervals); add attribution cases where thin:
  mid-period transfer splits, backdated transfer corrects on the next query,
  no-interval fact → `unknown` never dropped. The <1s flagship benchmark is
  the join's performance gate.
*Implementation (2026-10-30):* the as-of join is an `ASOF LEFT JOIN` onto a
  `dim_assignment` lookup that appends a synthetic pre-history row per employee
  (the earliest interval's dimensions at `effective_from = 1970-01-01`), which
  reproduces `resolve_assignment`'s earliest-interval fallback in one join;
  employees with no intervals stay unmatched → `unknown`. The seniority
  boundary test was the plan's non-blocking unknown — it seeded stamped facts
  without interval rows, and now seeds the interval the fold writer always
  produced. New cases: a backdated transfer re-attributes an already-projected
  fact (stamp A → groups B, and a department filter follows the resolved
  assignment, not a pre-filtered interval set) and a no-dimension fact reports
  under `unknown` with its counts retained.
*Verify:* `make check-api` green; diff confined to `reports.py` + tests.

**T6 — Parity harness + CDC-fed failure/isolation coverage (AC 2–5).**
- `tests/integration/test_cdc_parity.py` (+ helper) — one scenario driven
  through real services (hire; mid-period transfer; backdated transfer after
  facts exist; overtime `requested → manager_approved → hr_verified` plus a
  rejected/un-verified claim and a payroll finalize → `included_in_payroll`;
  approved leave + pending leave; check-in/out with late + `minutes_late`;
  two tenants). Derive the fold path from `event_outbox.envelope` rows →
  `ReportsConsumer`; derive the CDC path from the captured row state (SELECT
  the D8 columns per touched row wrapped in D9 envelopes) → `CdcSinkConsumer`;
  drive both to quiescence deterministically (test-run consumers to head,
  F7 pattern). Compare the four report aggregates through **one** repository
  implementation over the canonical client vs the shadow client — exact
  equality per group/bucket (AC 2); assert per-department attribution for the
  mid-period and backdated transfers on both paths (AC 3); assert tenant-B
  invisibility on CDC-fed reads (AC 4); assert `ready = 0` on the shadow →
  `ReportsUnavailableError` 503 (AC 5, twin of the existing gate test).
*Verify:* `make check-api` green.
*Implementation (2026-10-30):* landed as `tests/integration/test_cdc_parity.py`
  + `tests/integration/cdc_parity_helpers.py`. The harness runs the production
  consumers over an in-memory one-page source (no broker — T4/T7 own the
  transport): relay-ordered outbox payloads for the fold path, D8 row-state
  snapshot envelopes for the CDC path. It asserts exact canonical-vs-shadow
  aggregate equality for all four reports (monthly + total), pins the
  mid-period/backdated-transfer attribution on both paths, proves tenant-B
  isolation on CDC-fed reads, and covers both the repository fence and the API
  `503` over a fenced shadow. The scenario also asserts the OLTP end states
  (`included_in_payroll` ×2, `rejected`, `hr_verified`) so the D6 mapping and
  the payroll flip are actually exercised.

**T7 — Real Debezium in the e2e hop + recorded fixtures (AC 1, 10, 11).**
- `Makefile` `check-e2e` prerequisites add `cdc-test-up`.
- `tests/e2e/full-hop.sh` — start `workers/cdc_sink` (env `CLICKHOUSE_URL` →
  clickhouse-test, `CDC_PROJECTION_DATABASE=workpulse_test_cdc`,
  `CDC_CONNECT_URL=:8084`) with the `cdc_sink_started` wait, alongside the
  existing workers; `reset_reports_fixtures` extended to (a) truncate the
  shadow projection tables first and (b) write the People Ops fixture's OLTP
  rows (employees/employments/overtime_requests/attendance_records/
  leave_requests matching the published envelopes — the "No OLTP row is
  touched" invariant becomes "rows + envelopes written together") so real
  Debezium projects the fixture; after `npx playwright test`, a bounded-poll
  step compares window-scoped aggregates between the serving and shadow
  projections and fails the hop on mismatch (AC 2 on real Debezium; AC 1:
  API writes during the specs land through the pipeline and appear).
- Record real captured messages (one per op class per table) into
  `tests/contract/fixtures/cdc/recorded/` from the running hop; extend
  `test_cdc_envelope.py` to validate them against the T1 schemas (AC 10 on
  real messages).
- `tests/e2e/reports.spec.ts` — unchanged (AC 11).
*Verify:* `make check-e2e` green with the spec expectations untouched.
*Implementation (2026-10-30):* `check-e2e` now depends on `cdc-test-up`;
  `full-hop.sh` starts the CDC sink against Connect-test and the
  `workpulse_test_cdc` shadow, writes the report fixture as both OLTP rows and
  fold envelopes, and after Playwright runs
  `apps/api/scripts/compare_reports_parity.py` (fixture employee scope, exact
  four-report equality, bounded poll). The real hop exposed three wire-format
  gaps the synthetic contracts missed: Debezium 3.2.4 uses
  `first`/`first_in_data_collection`/`last_in_data_collection` snapshot markers
  in addition to `true`/`last`, it emits `ts_us`/`ts_ns` top-level fields, and
  `column.include.list` can make an update's `before` null. The envelope schema
  and `workers/cdc_sink/mapping.py` were widened for those real messages, and
  the connector now pins `skipped.operations: none` (Debezium defaults to
  skipping truncates, which would hide the drop-and-count path). Kafka
  tombstones are skipped silently. Recorded real messages for all six tables ×
  five op classes live in `tests/contract/fixtures/cdc/recorded/` and are
  validated by `test_cdc_envelope.py`; a test pins the real markers/null-before
  behaviour.

**T8 — Observability (AC 13).**
- `workers/cdc_sink/` health poller (~15 s): `workpulse_cdc_connector_state`
  (Connect REST status), `workpulse_cdc_slot_lag_bytes`
  (`pg_replication_slots`, small PG read adapter), `workpulse_cdc_consumer_lag`
  (offset lag) on `cdc_sink_metrics_port`; the T4 counters ride the same
  target. Bounded labels only — no `FORBIDDEN_METRIC_LABELS` (ADR 0023).
- `ops/observability/prometheus/prometheus.yml` — the `workpulse-workers`
  job gains `host.docker.internal:9106`; Grafana: extend
  `workpulse-overview.json` (or add `workpulse-cdc.json` + dashboards.yml)
  with connector state, lag, dropped deletes, sink errors panels.
- `tests/unit/test_observability_config.py` — `WORKER_METRICS_PORTS` gains
  the sink port with `Settings.model_fields` parity (F5); the dashboard gate
  covers the new panels (bounded metrics, no forbidden labels, no UUIDs).
*Verify:* `make check-api` green; `make observability-up` shows the panels
(manual check noted in the record step).
*Implementation (2026-10-30):* `workers/cdc_sink/health.py` adds a 15 s
  best-effort poller and the three gauges on `cdc_sink_metrics_port` (9106):
  connector/task state from Connect REST (one-hot over a closed state set),
  `pg_replication_slots` WAL lag, and per-topic uncommitted offset lag. The
  poller is injected with its probes (unit-tested without infra), failures are
  logged by category and never stop the sink, and every label stays bounded
  (`connector`, `slot`, `topic`, state). Prometheus scrapes 9106 and the
  existing overview dashboard gained the connector-state, slot-lag,
  consumer-lag, dropped-delete and sink-error panels; the config gate covers
  the new metrics/panels and the settings port parity.

**T9 — Cutover: retire the fold, rebuild from snapshot + WAL, docs, ADR 0024 (AC 12).**
- Rebuild + flip (ops/cdc/README runbook executed): stop `workers/reports`
  and `workers/cdc_sink`; canonical `projection_state` `ready = 0`; truncate
  the five canonical projection tables; reset the connector offsets and
  re-register (`snapshot.mode=initial`) so a snapshot + WAL replay refills
  them; flip `Settings.cdc_projection_database` to canonical (empty = the
  `CLICKHOUSE_URL` database); start the sink; snapshot-done flips
  `ready = 1`; drop the shadow databases.
- Remove (F1 retirement unit): `workers/reports/`,
  `app/domains/reports/folds.py`, `ProjectionWriter`
  (`domains/reports/repository.py:37-78`), `ClickhouseProjectionWriter`
  (`app/infrastructure/clickhouse/projection.py`), fold unit tests,
  `tests/integration/test_reports_consumer_kafka.py` and the writer tests in
  `test_clickhouse_projection.py`, `reports_consumer_group` +
  `reports_metrics_port` settings, `Makefile` `dev-reports`,
  `scripts/dev.sh` reports entry, `full-hop.sh` reports-worker launch + the
  envelope branch of `reset_reports_fixtures` (fixture now rows-only via
  CDC), Prometheus :9105 target (T8's gate updates land with the removal).
- Docs in the same change: `analytics-reports.md` — the two Worker AC rows +
  the rebuild-basis text amended to CDC semantics; `ADR 0024` — attribution
  representation (D3, parity as arbiter), rebuild = snapshot + WAL (replacing
  truncate + replay), `updated_at` = source commit ts (D4 + tie note),
  `minutes_late` source (T2 + pre-migration null acceptance), plus D1/D2/D5–
  D7 (sink mechanism, shadow mechanics retired, readiness ownership, status
  map, delete-op handling, dim_assignment same-`effective_from` caveat);
  `.ai/domain.md` Analytics wording (pure consumer of events → projection of
  the database log) and `.ai/architecture.md` Phase-5 row; then tick this
  spec's ACs, `Status: done`, `.ai/features/README.md` backlog row and
  `tasks/TODO.md`.
*Verify:* `make check` green (check-api + check-web + check-e2e); a
`verifier` pass over the removal diff (nothing outside the retirement set);
OpenAPI diff empty (no `make gen-api` needed anywhere in this feature).
*Implementation (2026-10-30):* the canonical flip is `cdc_projection_database=""`
(the `CLICKHOUSE_URL` database) and `make cdc-up` / `cdc-test-up` register
against the canonical dev/test databases (`workpulse`, `workpulse_test`); the
shadow databases are retired. `folds.py`'s two surviving read-side helpers
(`seniority_band`, `resolve_ordered_assignment`) moved to
`domains/reports/attribution.py`; the read repository, `reports` DTOs and API
tests seed through `ClickhouseCdcWriter`. The fold-dependent parity harness
(`test_cdc_parity.py`/`cdc_parity_helpers.py`) and its e2e comparator script
retired with the fold — the cutover evidence stays recorded in this spec, and
`test_cdc_sink.py` + `test_reports_api.py` keep the sink and read path covered.
`workers/reports`, the fold unit/broker tests, the writer tests, the
`reports_*` settings, the `dev-reports` target, the dev-script process and the
Prometheus `:9105` target are gone; `full-hop.sh` writes the fixture as OLTP
rows only and the CDC sink lands them in the canonical test database.

### Test strategy

| Gate | Covers | Failure cases |
| --- | --- | --- |
| `make check-api` | T1 envelope/row contract tests; T2 minutes_late persistence; T3 config gates (tables, include lists, PII, compose, make); T4 sink units + real-broker integration (kafka-test + clickhouse-test); T5 read-side attribution + benchmark; T6 parity/isolation/ready-gate | PII column added to include list (gate names it), extra column in row schema, wrong status map, unknown leave type, delete op removing a row, redelivery duplicating aggregates, `ready = 0` served as empty, cross-tenant leak, pre-migration `minutes_late` null |
| `make check-web` | unchanged (no web diff) | — |
| `tests/e2e/` (`make check-e2e`) | T7 full hop with `kafka-connect-test` + `cdc-sink`: `reports.spec.ts` expectations unchanged; shadow-vs-serving parity step; recorded fixtures for the contract test | pipeline not landing a write (parity step fails), aggregate drift between paths, employee blocked (existing spec), hop regression for the other workers |

Mandatory rules honoured: failure case per touched surface; tenant isolation
(T6) and denied-role cases (existing tests keep passing against CDC-fed
data); no PII in rows/contracts/logs (D8 + sweep tests); contract before
producer/consumer (T1 → T3/T4); parity before retirement (T6/T7 green before
T9). No new dependencies: Debezium runs as a compose image; the sink reuses
`aiokafka` + `clickhouse-connect` + `prometheus_client` already in the
`apps/api` uv environment (ADR 0006/0012 precedent).

### Risks & unknowns

- **R1 — As-of join performance.** The attribution rewrite makes report SQL
  join `dim_assignment` per fact. At v1 volumes ClickHouse answers this in
  milliseconds, but the <1s flagship benchmark (T5) is the hard gate; if it
  regresses, prefer an `ASOF JOIN`/pre-aggregated formulation over
  reintroducing write-time stamps.
- **R2 — Real Debezium wire format drift.** Debezium encodings (epoch-day/
  micros, decimal-as-string) and envelope extras vary by connector version.
  Mitigations: pin the image tag (T3), D9 documents the encodings, recorded
  real fixtures (T7) re-validate the contract on every `make check-api`.
- **R3 — Kafka internal listener (D10).** Touching `kafka`/`kafka-test`
  listener maps risks the existing host consumers and the F5 compose gate.
  Mitigation: additive-only listener (host ports unchanged), full `make
  check` + `check-e2e` after T3.
- **R4 — E2E fixture rewrite.** `reset_reports_fixtures` must now write OLTP
  rows consistent with the published envelopes and truncate the shadow set —
  the largest e2e change. Mitigation: the post-spec parity step proves the
  fixture's two derivations agree; `reports.spec.ts` stays the oracle.
- **R5 — Equal commit-ts ties (D4) and same-`effective_from` employments.**
  Two same-row changes within 1 ms, or two `employments` rows sharing
  `effective_from` (collapsing under `ReplacingMergeTree`'s key), are
  pathological on these write paths; both are accepted and named in ADR 0024
  with the `(commit_ts, lsn)` / key-extension fallbacks.
- **R6 — Cross-topic reference miss (D7).** A brand-new leave type and its
  first request can invert across topics; the head-of-line retry + bounded
  `unknown` fallback keeps liveness without wrong buckets. Covered by a T4
  unit case.
- **R7 — Cutover rebuild window.** `ready = 0` during the snapshot rebuild
  serves 503s on reports — accepted (rule 10 semantics); the runbook keeps it
  bounded at dev scale and is exercised by the T7 hop's truncations.
- **Unknowns (non-blocking):** exact Debezium Connect image tag and REST
  version (pinned at T3 against the compose precedent); whether any existing
  reports test seeds stamped facts without interval rows (T5 verification
  will surface it — such seeding gains the intervals, matching what the fold
  writer always did); `test_observability_config.py`'s exact compose-port
  assertions may need widening for the connect pair (same task keeps the gate
  green).
