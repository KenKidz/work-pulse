# 19. ClickHouse projection and reports

- **Status:** Accepted
- **Date:** 2026-10-22

## Context

Phase 4's first feature (`.ai/features/analytics-reports.md`) must answer "overtime
rate by department, location and seniority over the last six months" in under a
second without scanning the OLTP tables that serve check-ins, approvals and
payroll (`.ai/product.md`; `.ai/architecture.md` Phase-4 success metric).
Analytics & Reports is a pure consumer (`.ai/domain.md`): every analytical row
is a projection of the `workpulse.events` stream, history is events-only (PO
Decision 7 — no OLTP backfill), and a rebuild from the stream must reproduce the
same aggregates. ClickHouse is the Phase-4 store; `clickhouse-connect` is
ADR 0006's pre-agreed dependency.

Choices that are expensive to reverse once the worker and the API ship: the
table/aggregation model (current-state rows vs. raw event log vs. pre-aggregated
views), how replay safety is achieved without a dedupe store, consumer offset
semantics, how dimension state and display labels reach the projection, and the
rebuild path. This ADR pins those before any ClickHouse code (plan T2).

Alternatives considered:

- **Aggregate against PostgreSQL (materialized views or a read replica).**
  Aggregations would still compete with check-ins and approvals for the OLTP
  system, which is the premise Phase 4 exists to remove. Rejected.
- **Make CDC (Debezium) the projection now.** It is Phase 5 and replaces
  hand-written projections; pulling it into Phase 4 would collapse two roadmap
  steps and remove the event-contract pressure the product depends on. Rejected.
- **Kafka Streams / ksqlDB.** A second streaming runtime and state store for
  aggregates ClickHouse already computes; no operational gain at v1 scale.
  Rejected.
- **ClickHouse materialized views / `AggregatingMergeTree` now.** Premature:
  query-time `GROUP BY` over stamped facts is simple, debuggable and measurable
  against the <1s metric. Pre-aggregation is the named upgrade if the budget is
  missed, not a v1 deliverable (PO/spec scope). Rejected for v1.
- **One wide "event" or "fact" table.** The four fact families have different
  grains and keys (overtime request, attendance record, leave request,
  employee/day state); a single table forces nullable columns and per-type
  reinterpretation of the same rows. Rejected in favour of per-entity tables.
- **Dual-write from the API into ClickHouse.** Violates "one fact, one event",
  makes the write path depend on the analytics store, and duplicates the
  outbox. Rejected (ADR 0012).
- **Store raw envelopes in ClickHouse and aggregate at read time.** The event
  log already exists in Kafka/`event_outbox`; re-deriving every fact on every
  query defeats the <1s budget and duplicates the transport's retention
  problem. Rejected.
- **Persist a consumed-`event_id` dedupe table.** Unbounded growth for a
  guarantee the ordered fold already provides (see Decision 4). Rejected.
- **Store display labels (names) in ClickHouse.** Labels are reference data,
  change independently of facts, and names are PII-adjacent. Rejected in favour
  of query-time read-only ports (Decision 8), the ADR 0015 carve-out.

## Decision

1. **ClickHouse is the analytical projection for Analytics & Reports.**
   `clickhouse-connect` (ADR 0006's pre-agreed Phase-4 gate, justified by this
   spec per architecture rule 10) joins `apps/api`. The worker
   `workers/reports/` is the only writer; the API reads through a repository.
   `docker-compose.yml` pairs `clickhouse` (named volume) with
   `clickhouse-test` (tmpfs), like the Postgres/Kafka pairs.

2. **Five projection tables plus a rebuild marker, `ReplacingMergeTree(updated_at)`.**
   The projection is current state per entity, not an append-only event log; the
   durable event log is `event_outbox`/Kafka (ADR 0012 decision 8). Identifiers
   are ClickHouse `UUID`; dates are `Date`; `updated_at` is
   `DateTime64(6, 'UTC')` and is the replacement version (bookkeeping only,
   never report math).

   | Table | Columns | Key / partition |
   | --- | --- | --- |
   | `dim_employee` | `tenant_id UUID`, `employee_id UUID`, `hired_at Nullable(Date)`, `terminated_at Nullable(Date)`, `updated_at` | `ORDER BY (tenant_id, employee_id)`; unpartitioned |
   | `dim_assignment` | `tenant_id UUID`, `employee_id UUID`, `effective_from Date`, `effective_to Nullable(Date)`, `department_id Nullable(UUID)`, `position_id Nullable(UUID)`, `location_id Nullable(UUID)`, `updated_at` | `ORDER BY (tenant_id, employee_id, effective_from)`; unpartitioned |
   | `fact_overtime` | `tenant_id UUID`, `overtime_id UUID`, `employee_id UUID`, `work_date Date`, `hours Decimal(9,2)`, `status LowCardinality(String)`, dimension stamps, `hired_at Nullable(Date)`, `updated_at` | `ORDER BY (tenant_id, overtime_id)`; `PARTITION BY toYYYYMM(work_date)` |
   | `fact_attendance` | `tenant_id UUID`, `attendance_id UUID`, `employee_id UUID`, `workday Date`, `duration_minutes Nullable(Int32)`, `late Bool`, `minutes_late Nullable(Int32)`, dimension stamps, `hired_at Nullable(Date)`, `updated_at` | `ORDER BY (tenant_id, attendance_id)`; `PARTITION BY toYYYYMM(workday)` |
   | `fact_leave` | `tenant_id UUID`, `leave_request_id UUID`, `employee_id UUID`, `start_date Date`, `leave_type_code LowCardinality(String)`, `approved_day_count UInt16`, dimension stamps, `hired_at Nullable(Date)`, `updated_at` | `ORDER BY (tenant_id, leave_request_id)`; `PARTITION BY toYYYYMM(start_date)` |
   | `projection_state` | `key LowCardinality(String)`, `ready UInt8`, `updated_at` | `ORDER BY (key)`; unpartitioned; one row `key = 'reports'` |

   The five fact/dimension tables are the projection; `projection_state` is the
   rebuild fence (Decision 7). "Dimension stamps" = `department_id`,
   `position_id`, `location_id` (`Nullable(UUID)`) as of the fact's own date.
   Fact rows carry `hired_at` so seniority bands are computed at query time
   against the row's bucket end (tenure grows; a stamp would freeze the wrong
   band). Fact grains are one row per overtime request, one per attendance
   record (`checked_in` + `checked_out` merged by `attendance_id`;
   `attendance.late` is not consumed — the check-in payload already carries
   `late`/`minutes_late`), and one per approved leave request. `status` is one
   of `requested|manager_approved|hr_verified|rejected|withdrawn`; payroll's
   `included_in_payroll` flip emits no event (ADR 0018 decision 6) and is
   therefore never observed — a paid claim stays `hr_verified` and counted once.

3. **Writes are complete-row upserts; reads dedupe.** Every handler writes the
   entity's whole row under its ordering key with a fresh `updated_at`; there
   are no in-place updates or deletes. `ReplacingMergeTree` keeps the row with
   the highest version, so every report query reads with `FINAL` (or an
   equivalent `argMax` fold) and never sees duplicate parts. At v1 volumes
   `FINAL` is cheap; a query that forgets it surfaces as doubled aggregates in
   the integration tests, and `argMax` is the documented optimisation, not a
   semantic change. Inserts are synchronous (visible to the next SELECT part)
   so a fact fold can read its own dimension state; merges stay asynchronous.

4. **Idempotency = convergent, ordered, per-entity folds.** The stream is keyed
   by `data.employee_id` (ADR 0012 decision 2), so all state for one employee
   is consumed in order. Each fold is a guarded function of (prior row,
   envelope): re-applying a transition that is already current yields the
   identical row, and a duplicate `event_id` therefore collapses to the same
   upsert with a new version. This is ADR 0012 decision 5's `event_id` dedupe
   requirement satisfied the same way the realtime consumer satisfies it —
   harmless identical upserts — with no dedupe table. Folds ignore events that
   would move an entity's state backwards. Malformed messages (broken JSON, or
   a known `event_type` whose required payload fields cannot be mapped) are
   skipped and committed per Decision 6; the fold functions are total over the
   contract's required fields, and any gap there is a code defect surfaced by
   tests, never a runtime stall.

5. **Dimension state is folded from the employee stream and resolved per fact date.**
   `employee.created` opens `dim_employee` (tenure anchor `hired_at` when
   present) and the first `dim_assignment` interval; `employee.transferred`
   closes the open interval at `effective_from` and opens the next using
   `to_department_id` plus `to_position_id`/`to_location_id` when present
   (absent → carry the previous values, the pre-extension degradation);
   `employee.terminated` closes the interval and stamps `terminated_at`. A fact
   is stamped with the assignment covering its own fact date — the latest
   interval with `effective_from <= fact_date`, with the earliest known interval
   as the fallback for facts that predate it (a pre-extension `employee.created`
   cannot know the real hire date; the interval starts at `occurred_at`'s date
   and tenure is `unknown`). A fact is normally stamped when it is folded, and a
   mid-month transfer splits the employee's facts across groups at
   `effective_from` without touching earlier rows. One case needs a correction:
   the service allows a transfer to be recorded after facts dated on or after
   its `effective_from` have already been projected (backdated transfer;
   publish order is commit order, not fact-date order). The transfer handler
   therefore also runs a bounded re-stamp through the projection writer after
   the interval upserts: every existing fact row for that employee with
   `fact_date >= effective_from` re-resolves its assignment against the updated
   interval history (latest interval with `effective_from <= fact_date`) and is
   upserted with the corrected stamps. Facts before `effective_from` are
   untouched. The pass is idempotent and limited to one employee's rows; a later
   out-of-order transfer re-resolves each affected fact independently, so facts
   after an already-known later transfer keep that transfer's dimensions. This
   keeps "as of the fact date" true regardless of publish order (spec rule 3 /
   AC 4).

6. **Consumer semantics: at-least-once in, idempotent out.** Group
   `workpulse-reports` (`settings.reports_consumer_group`) with
   `auto_offset_reset="earliest"` within the configured retention window: the
   first start builds the whole picture the topic still holds, restarts resume
   committed offsets. The topic must retain at least the 24-month maximum report
   span plus a margin (the relay's topic creation sets `retention.ms`; ops
   verifies it), otherwise old history is silently absent and the outbox replay
   procedure below is the only recovery. Manual commit after the ClickHouse
   write succeeds. A ClickHouse failure leaves the offset uncommitted and
   retries the same message with backoff (head-of-line, accepted); a malformed
   message or an unknown/irrelevant `event_type` is logged ids-only
   (`consumer_message_skipped`), skipped and committed — a consumer never
   poisons its stream (ADR 0012/0013 precedent). One worker instance in v1;
   multi-instance offset claiming is out of scope (relay precedent).

7. **Query-time aggregation; no materialized views in v1.** Reports answer with
   `GROUP BY` over stamped fact columns and ClickHouse bucket functions
   (`toStartOfMonth`, `toStartOfWeek(..., 1)` for Monday, `toStartOfDay`);
   `interval=total` groups all rows under `bucket=null`. Headcount metrics read
   the dimension tables, not facts: as-of `headcount` at `min(bucket_end, to)`,
   `joiners`/`leavers` from the hire/termination dates, and the overtime
   report's `active_employee_count` counts employees whose employment overlaps
   the bucket (spec rules 8–9; plan R4). A pre-extension `employee.created`
   carries no `hired_at`, so headcount/joiners use
   `coalesce(hired_at, first dim_assignment.effective_from)` as the approximate
   hire anchor while tenure stays `unknown` — legacy employees are counted at
   their first-seen date instead of silently disappearing from the report. The
   six-month flagship query is benchmarked against the <1s metric.
   Pre-aggregated tables or materialized views are the documented upgrade if
   the benchmark is missed.

   Every report query first reads `projection_state` (`key = 'reports'`):
   `ready = 0` → `ReportsUnavailableError` (`503 reports_unavailable`); a
   missing row is a fresh projection and is served normally (empty rows, not an
   error). The rebuild procedure writes `ready = 0` before truncating and
   `ready = 1` once the replay has caught up (Consequences).

8. **Labels and manager scope are read-only query-time ports.** Display labels
   (`department`/`position`/`location` names, leave-type labels) are resolved by
   a domain-owned `ReferenceLabels` protocol whose infrastructure adapter reads
   the owning contexts' reference tables read-only and tenant-scoped — never a
   projection input, never written to ClickHouse (the ADR 0015 enrichment
   carve-out). Manager scoping uses an `EmployeeScope` protocol whose adapter
   runs the same self + direct-reports `visible_employee_ids` query as
   leave/overtime, so report scope and inbox scope cannot disagree. HR /
   Finance / Admin / Super Admin are company-wide; Employees are denied by
   policy. Reports mutate nothing: no audit entries, no events.

9. **Metric math is `Decimal` end to end; the wire carries fixed-scale decimal
   strings.** Hours are stored as `Decimal(9,2)` and parsed from event JSON via
   `Decimal(str(value))` — never through binary float. Ratios are computed in
   the service and quantized once at the wire scale with `ROUND_HALF_EVEN`
   (hours 2dp, `late_rate` 4dp, `avg_minutes_late` 1dp; zero denominators yield
   `0.00`/`0.0000`/`0.0`, never a division error). The API serializes them as
   decimal strings so scale is preserved for the typed client and the table/
   chart formatting; ADR 0017's money-on-the-wire rule is untouched because
   these are hours/rates, not amounts.

10. **Tenant isolation and PII are structural.** `tenant_id` is the first
    ordering-key column and the first predicate of every query; it is applied
    in the reports repository, never in the router or the service. Projection
    rows and report responses contain identifiers, dates, counts and hours
    only — no names, emails, salaries, leave reasons or employee identifiers in
    report rows. Logs carry ids and error types only (`.ai/security.md`).

11. **DDL is idempotent; the worker applies it, the API best-effort.**
    `database/clickhouse/` holds `CREATE TABLE IF NOT EXISTS` statements
    generated from this ADR, applied by the worker at startup (it cannot
    project without tables, so failure stops the worker loudly). The API also
    attempts the same idempotent DDL at startup but **best-effort**: a failure
    is logged and boot continues, and report reads then return `503
    reports_unavailable`. ClickHouse being down must never block the OLTP API
    from starting. Schema changes in v1 are additive (`ALTER TABLE ... ADD
    COLUMN IF NOT EXISTS`) appended to the same script; destructive or
    type-changing changes require a rebuild plus a new ADR, because ClickHouse
    has no migration history here. No ClickHouse TTL/data-retention pruning in
    v1 (Kafka topic retention is pinned in Decision 6).

12. **Wiring.** `workers/reports/` runs inside the `apps/api` uv environment
    (`python -m workers.reports.main`, ADR 0012 decision 10 / ADR 0013
    decision 5; the fifth worker keeps that debt recorded, per spec R6).
    `clickhouse-connect` speaks the HTTP interface (:8123); the API adapter runs
    its synchronous calls off the event loop (`anyio.to_thread`, ADR 0006's
    concurrency rule), the single-threaded worker serializes its writes.

## Consequences

- **Rebuild is a fenced ops procedure, not application code.** The exact order
  is: (1) upsert `projection_state.ready = 0` — report reads immediately return
  `503 reports_unavailable`; (2) stop the reports worker gracefully;
  (3) reset the `workpulse-reports` group to `earliest` while the group is
  inactive (Kafka refuses an offset reset for a live group); (4) `TRUNCATE` the
  five projection tables; (5) start the worker and wait until the group's lag
  reaches 0; (6) upsert `ready = 1`. The fold is a pure function of the stream,
  so aggregates reproduce exactly, and no partial rebuild is ever served.
- **Retention and the outbox replay source.** The topic must be created with
  `retention.ms` covering the 24-month maximum report span plus a margin; the
  relay's `NewTopic` creation is the enforcement point and ops verifies it.
  Within that window a rebuild replays from `earliest`. If the needed history
  predates retention, `event_outbox` (ADR 0012 decision 8) is the durable
  record but is not consumed directly: an operator re-publishes the required
  rows by clearing `published_at` for the range and restarting the relay in
  ID order, which re-delivers to every Kafka consumer. That is safe because
  ADR 0012 makes `event_id`-tolerant redelivery mandatory (realtime/notifications
  are replay-safe; reports folds converge) — but it is a deliberate
  cross-consumer replay and is announced as such. A rebuild must never run
  while the topic already lacks the required range.
- Replay safety comes from ordering plus convergence, not from a dedupe store:
  at-least-once redelivery and full replays are the same upsert path, and the
  tests prove byte-identical aggregates.
- `FINAL` is a hard correctness requirement (spec risk R2). Omitting it doubles
  aggregates; it is pinned in the repository and covered by API/worker tests.
- A backdated transfer (recorded after facts dated on/after its
  `effective_from` were projected) triggers the bounded per-employee re-stamp,
  so per-fact-date attribution holds without a replay; only an out-of-order
  history that is already gone from the topic needs the retention/outbox
  procedure above.
- The analytics store is optional for the write path: check-ins, approvals and
  payroll commit with ClickHouse down. The reports surface degrades to `503
  reports_unavailable`, which the web renders as its error state. The API's
  startup DDL is best-effort for the same reason (Decision 11).
- Labels are always current reference data rather than event-time snapshots;
  that is deliberate (a renamed department is displayed by its current name)
  and never changes aggregate math.
- The event payload extensions (`hired_at`, `to_position_id`,
  `to_location_id`) are additive v1 (ADR 0013 pattern); pre-extension messages
  remain valid and degrade honestly — `unknown` seniority, carried-forward
  position/location — rather than being silently dropped.
- Adding payroll analytics later needs its own amounts decision + ADR; this
  projection deliberately carries no amounts and does not consume `payroll.*`.
