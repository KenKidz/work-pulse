# CDC pipeline (Debezium)

Phase-5 analytics projection: PostgreSQL row changes are captured by Debezium,
published to Kafka as `workpulse.cdc.<table>`, and landed in the ADR 0019
ClickHouse projection by `workers/cdc_sink`. The outbox +
`workpulse.events` backbone is untouched — CDC serves analytics only
(`.ai/features/change-data-capture.md`, ADR 0024).

## What is captured

Exactly the sources of the five projection tables plus the leave-type
reference (feature rules 3–4):

| Table | Columns |
| --- | --- |
| `employees` | id, tenant_id, hired_at, terminated_at |
| `employments` | tenant_id, employee_id, department_id, position_id, location_id, effective_from, effective_to |
| `attendance_records` | id, tenant_id, employee_id, workday, duration_minutes, late, minutes_late |
| `overtime_requests` | id, tenant_id, employee_id, work_date, hours, status |
| `leave_requests` | id, tenant_id, employee_id, leave_type_id, start_date, days, status |
| `leave_types` | id, tenant_id, code |

No PII crosses the connector boundary: names, emails, phones, salaries,
reasons, notes and decision columns are excluded by `column.include.list`.
`tests/unit/test_cdc_config.py` fails if the captured-table set or an include
list drifts, naming the offending column.

## Prerequisites

- Docker compose services: PostgreSQL, Kafka, ClickHouse and Kafka Connect
  (`make cdc-up` starts all of them and applies migrations).
- Migrations applied (`make cdc-up` runs `make db-migrate`): the captured
  tables and `attendance_records.minutes_late` (0020) must exist.
- PostgreSQL logical WAL: compose starts both databases with
  `wal_level=logical` and caps replication-slot retention at 1 GB; a custom
  PostgreSQL needs logical WAL, `max_wal_senders`, free slots and a
  `max_slot_wal_keep_size` bound.
- Python 3 (the API venv is used when present) for the connector's JSON
  parsing; no `jq`/`sed` config splitting.

## Lifecycle

```bash
make cdc-up        # PostgreSQL + migrations + ClickHouse + Kafka + Connect; seeds workpulse_cdc
make cdc-down      # stop kafka-connect (connector state lives in Kafka)
make cdc-test-up   # PostgreSQL + migrations + clickhouse-test + kafka-test + Connect-test
make cdc-test-down # stop kafka-connect-test
```

`make cdc-up` runs `ops/cdc/register.sh`, which:

1. leaves an existing connector alone apart from updating its config — an
   update never re-fences a running projection;
2. for a **new** connector, creates the target database, applies
   `database/clickhouse/schema.sql`, and writes `projection_state.ready = 0`
   before the connector exists (closes the registration window, feature D5);
3. waits for the Connect REST API and fails unless the connector and every
   task reach `RUNNING`.

The dev target is the D2 shadow database `workpulse_cdc`; the fold-served
canonical `workpulse` database is only fenced during the T9 cutover. The test
target is `workpulse_test_cdc`. The dev connector uses PostgreSQL
`postgres:5432` / `workpulse` and slot `workpulse_cdc`. The test instance points
at the same dev PostgreSQL but uses `CDC_SLOT_NAME=workpulse_cdc_test` so the
two connectors never share a slot:

```bash
CONNECT_URL=http://localhost:8084 \
CDC_SLOT_NAME=workpulse_cdc_test \
CDC_CLICKHOUSE_SERVICE=clickhouse-test \
CDC_PROJECTION_DATABASE=workpulse_test_cdc \
bash ops/cdc/register.sh
```

The sink (`make dev-cdc-sink`, Phase-5 T4) consumes `earliest` with a stable
group, so it can start before or after `make cdc-up`; starting it first makes
readiness flips and lag easiest to read. When the broker disables
auto-topic-creation (`auto.create.topics.enable=false`), start the connector
first or pre-create the six `workpulse.cdc.<table>` topics and
`__debezium-heartbeat.workpulse.cdc`; the sink retries startup with backoff
while they are missing instead of exiting.

## Verify

```bash
curl -s localhost:8083/connectors/workpulse-cdc/status | jq
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --list | grep '^workpulse.cdc'
```

Topic naming is `workpulse.cdc.<table>`. Debezium's PostgreSQL default would be
`workpulse.cdc.public.<table>` (schema included), so `connector.json` pins a
`RegexRouter` transform that drops the schema segment. Values are Debezium JSON
envelopes with `schemas.enable=false`; `source.ts_ms` is the projection row's
`updated_at` version, never ingest wall clock. The Debezium heartbeat topic
stays `__debezium-heartbeat.workpulse.cdc` (SMTs do not rewrite it).

## Rebuild = snapshot + WAL

A projection rebuild replays the connector snapshot and then streams WAL from
the slot — it never replays `workpulse.events`:

1. stop the sink and the connector (`make cdc-down` or
   `curl -X DELETE $CONNECT_URL/connectors/workpulse-cdc`);
2. write `projection_state.ready = 0` (the reports API answers `503` while
   the fence holds);
3. truncate the five projection tables (keep `projection_state`);
4. drop the inactive replication slot once the connector is gone:
   `SELECT pg_drop_replication_slot('workpulse_cdc');`
5. drop the connector's offsets by registering a **new** connector name with a
   **fresh** slot/publication, or delete the `workpulse.cdc.connect.offsets`
   topic and re-register the original name;
6. start the sink, then register the connector (`snapshot.mode=initial`);
7. readiness flips to `ready = 1` when the final snapshot row arrives
   (`source.snapshot == "last"`), a heartbeat lands for an empty snapshot, or
   the first non-snapshot change arrives — provided the evidence is at or
   after the fence timestamp (replayed pre-rebuild history cannot un-fence a
   truncated projection).

## Sink/connector down

PostgreSQL retains WAL at the replication slot while the sink or connector is
down. No committed change is lost within WAL retention, but disk use grows.
Compose caps retention at 1 GB (`max_slot_wal_keep_size=1GB`); if the slot is
invalidated, delete the connector, drop the slot, and re-register it so a fresh
snapshot + WAL replay rebuilds the projection. Watch
`workpulse_cdc_slot_lag_bytes` (Phase-5 T8) and restart the components before
the bound is reached. The sink retries
head-of-line with bounded backoff; redelivered changes replace by
`ReplacingMergeTree(updated_at)`.

## Files

- `connector.json` — the Postgres connector config (tables, include lists,
  snapshot and wire-format pins).
- `register.sh` — idempotent registration + readiness seed.
- `../../database/clickhouse/schema.sql` — the projection DDL the sink applies.
- `../../contracts/cdc/` — the change-envelope and per-table row contracts.
