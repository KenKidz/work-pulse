# 24. CDC projection pipeline (Debezium → Kafka → ClickHouse)

- **Status:** Accepted
- **Date:** 2026-10-30

## Context

Phase 5 (`.ai/features/change-data-capture.md`) moves the Analytics & Reports
projection from application code to the database log:

`PostgreSQL → Debezium (Kafka Connect) → Kafka → workers/cdc_sink → ClickHouse`.

This amends ADR 0019, which pinned the hand-written projection and the
`workers/reports` fold. The parts of ADR 0019 that still hold are the five
current-state ClickHouse tables, `ReplacingMergeTree` dedupe, `FINAL` reads,
query-time aggregation, tenant isolation, the `projection_state` fence and the
read-only label/manager-scope ports. The parts that changed are attribution,
replay/rebuild, versioning, readiness ownership, and the retirement of the fold
and its Python tests.

Alternatives considered:

- **Keep the fold and add CDC beside it.** Two writers for one projection
  forever; the architecture roadmap says CDC replaces hand-written analytics
  projections. Rejected at cutover after the T6/T7 parity proof went green.
- **ClickHouse Kafka-engine tables + materialized views.** The pipeline would
  live in DDL, but delete-drop counting, the leave-type reference dictionary,
  readiness and testability would move out of application code. Rejected.
- **The official ClickHouse Kafka Connect sink.** Less code, but the same
  domain mapping (status map, approved-only leave) would still be needed and
  the readiness ownership would be spread across Connect and the store.
  Rejected.
- **Full-row CDC into Kafka (all columns).** PII would cross the connector
  boundary, contradicting `.ai/security.md` and the product's analytics-row
  rule. Rejected; the include lists carry identifiers, state and dates only.
- **Exactly-once delivery.** Unnecessary complexity; at-least-once plus
  idempotent replacement by source commit timestamp is sufficient and matches
  the existing Kafka/outbox conventions. Rejected.

## Decision

1. **CDC owns the projection.** Debezium captures exactly six tables —
   `employees`, `employments`, `attendance_records`, `overtime_requests`,
   `leave_requests`, `leave_types` — with per-table column include lists
   restricted to the rule-3/rule-4 sets. The topics are
   `workpulse.cdc.<table>`; `workers/cdc_sink` is the only writer of the five
   projection tables and of `projection_state`. `workers/reports`, the pure
   folds (`domains/reports/folds.py`), `ProjectionWriter` and
   `ClickhouseProjectionWriter` are retired. The outbox + `workpulse.events`
   backbone is untouched; Elasticsearch/search/audit sinks remain out of scope.

2. **Attribution is query-time as-of (D3).** Fact rows carry no live dimension
   stamps: the CDC sink leaves `department_id`/`position_id`/`location_id`/
   `hired_at` null on `fact_*` rows, and report SQL resolves the interval from
   `dim_assignment` with an `ASOF LEFT JOIN` whose lookup appends a synthetic
   pre-history row per employee (earliest interval's dimensions at
   `effective_from = 1970-01-01`). This reproduces the retired
   `resolve_assignment`/`stamp_for` semantics exactly: latest interval with
   `effective_from <= fact_date`, earliest-interval fallback, `unknown` when
   the employee has no intervals, never dropped. A backdated transfer therefore
   corrects attribution on the next query without a re-stamp pass; a late
   dimension row is accepted and converges. The T6 parity harness compared the
   fold and CDC paths through one repository implementation; exact aggregate
   equality was the arbiter and is recorded green before retirement. Stamped
   fact columns stay in the DDL (additive-only) but the read path no longer
   reads them.

3. **`updated_at` is the source commit timestamp (D4).** Every projected row's
   `ReplacingMergeTree` version is `source.ts_ms` from the Debezium envelope,
   rendered `DateTime64(6)` UTC — never ingest wall clock. Redelivery replaces
   the same row instead of duplicating it. Two same-row updates within one
   millisecond are accepted as a non-occurring tie on these write paths; the
   documented fallback is a `(commit_ts, lsn)` composite version.

4. **Rebuild = snapshot + WAL.** A fenced rebuild writes `projection_state`
   `ready = 0`, truncates the five tables (keeping the fence), resets the
   connector offsets (new connector/slot or deleted offsets topic), and lets
   `snapshot.mode=initial` replay the table snapshot followed by WAL. The sink
   flips `ready = 1` on snapshot-done evidence. Topic replay of
   `workpulse.events` is no longer part of reports recovery; the outbox remains
   the event backbone for its other consumers.

5. **Readiness is sink-owned in its target database (D5).** `ops/cdc/register.sh`
   writes `ready = 0` before creating a connector (closing the registration
   window). A fresh consumer group fences before its first fetch; the sink
   flips readiness on `source.snapshot == "last"`, a Debezium heartbeat (empty
   snapshot) or the first non-snapshot change, provided the evidence is at or
   after the fence timestamp. A restart with committed offsets never touches
   `ready`. Missing-row semantics are unchanged (serve normally).

6. **Status and delete semantics (D6/D7).** `overtime_requests.status` maps
   1:1 except `included_in_payroll` → `hr_verified`, because the fold never saw
   the payroll flip (ADR 0018 decision 6) and reports count `hr_verified`.
   `leave_requests` projects only `status = 'approved'` rows; pending/rejected/
   withdrawn rows produce no fact and are not deletes. `leave_type_code` is
   resolved from the captured `leave_types` stream through the sink's
   in-memory reference dictionary, warmed from the topic; an unknown type id
   retries head-of-line with backoff, then falls back to `unknown` and counts a
   reference miss. Delete/truncate ops are dropped and counted; the projection
   never removes a row (ADR 0007 soft-delete semantics).

7. **Minimum data, no PII.** Connector include lists are the security boundary.
   Names, emails, phones, salaries, reasons, notes, decision columns and
   document metadata never enter Kafka or ClickHouse via CDC. The gateway is
   `tests/unit/test_cdc_config.py` (exact table/column sets, mutation-tested
   PII rejection) plus the `contracts/cdc/` row contracts.

8. **`minutes_late` comes from the OLTP row.** The additive migration adds
   `attendance_records.minutes_late`, written by check-in with the same value
   the event carries. Rows written before the migration keep `null`; parity is
   proven on post-migration writes, and a rebuild reproduces `null` for those
   old rows — the event-only value is unrecoverable from row state. This is the
   accepted exception to "every projected column is derivable from captured row
   state" and is recorded here so the rebuild guarantee is honest.

9. **Same-`effective_from` caveat.** Two `employments` rows sharing an
   `effective_from` collapse under `dim_assignment`'s ordering key
   `(tenant_id, employee_id, effective_from)`. The write paths create one
   interval per start date; if a future feature allows duplicates, the key must
   gain a discriminator (and a rebuild/ADR follow-up). Named as a known
   limitation, not a silent assumption.

10. **Operations.** Kafka Connect is a compose pair (`kafka-connect` /
    `kafka-connect-test`) with dev/test REST ports; `make cdc-up`,
    `cdc-down`, `cdc-test-up`, `cdc-test-down` own the lifecycle. Health is
    metrics-first (connector state, replication-slot WAL lag, consumer lag,
    dropped deletes, sink errors) on the existing Prometheus/Grafana
    conventions (ADR 0023); there is no web surface. Sink-down retains WAL at
    the slot; retention is bounded by `max_slot_wal_keep_size` and the runbook
    documents recovery.

## Consequences

- The projection is a data pipeline, not application fold code: new analytical
  entities are added by capturing a table, extending a row contract and adding
  a mapping, with no replay of the event topic.
- The read path carries as-of joins. The flagship <1s benchmark remains the
  performance gate; `ASOF JOIN` is the chosen formulation and pre-aggregation
  stays the named fallback.
- The CDC stream is a second, narrower contract surface (`contracts/cdc/`).
  Real Debezium wire details are pinned by recorded fixtures: snapshot markers
  `true`/`first`/`first_in_data_collection`/`last_in_data_collection`/`last`,
  top-level `ts_us`/`ts_ns`, nullable `before` on updates with an include list,
  and epoch-day/epoch-micro temporal encodings. `decimal.handling.mode=string`
  keeps overtime `hours` an ADR 0017 decimal string.
- ADR 0019 decisions 4, 5, 6 and 12 are superseded by decisions 1–5 above;
  the remaining ADR 0019 decisions (table model, `FINAL`, query-time
  aggregation, ports, metric math, tenancy/PII, DDL idempotence) stand.
