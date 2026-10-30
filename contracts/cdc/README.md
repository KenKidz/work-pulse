# CDC contracts

Versioned JSON Schema for the Phase-5 change-data-capture stream. Debezium tails
PostgreSQL WAL and publishes one topic per captured table:

```
workpulse.cdc.employees
workpulse.cdc.employments
workpulse.cdc.attendance_records
workpulse.cdc.overtime_requests
workpulse.cdc.leave_requests
workpulse.cdc.leave_types
```

The contract exists before the connector or the sink
(`.ai/features/change-data-capture.md` T1): `tests/contract/test_cdc_envelope.py`
validates synthetic captured messages against it, and the connector's include
lists are gate-checked against the same column sets.

## Envelope

`change-envelope.v1.json` is the Debezium JSON change envelope with
`value.converter.schemas.enable=false`:

```json
{
  "before": null,
  "after": {"id": "...", "tenant_id": "...", "hired_at": 20103, "terminated_at": null},
  "source": {
    "ts_ms": 1767225600000,
    "lsn": 24023168,
    "snapshot": "false",
    "db": "workpulse",
    "schema": "public",
    "table": "employees"
  },
  "op": "c",
  "ts_ms": 1767225600001,
  "transaction": null
}
```

- `op`: `c` create · `u` update · `d` delete · `r` snapshot read · `t` truncate.
  The sink drops `d`/`t` and never deletes projection rows.
- `before`/`after`: row images, validated against `rows/<table>.v1.json` when
  present. Debezium emits only replica-identity columns in `before` for
  update/delete, so they can be a filtered subset of the row schema.
- `source.ts_ms`: source commit timestamp in epoch milliseconds — the
  projection row's `updated_at` (never ingest wall clock).
- `source.snapshot`: `"true"` during the initial snapshot, `"last"` on its final
  row, `"false"` when streaming.
- Extra Debezium `source` fields (`version`, `connector`, `name`, `sequence`,
  `txId`, `xmin`) are allowed; the pinned fields above are the contract.

## Row schemas

`rows/` pins the exact captured column set per table — identifiers, state and
timestamps only. There is no PII carve-out: names, emails, phones, salaries,
reasons, decision notes and document metadata never enter Kafka or ClickHouse
(`.ai/security.md`). Row schemas are strict (`additionalProperties: false`), and
`tests/contract/test_cdc_envelope.py` fails if a column set drifts from the
plan's D8 sets or a PII-shaped column appears.

| Table | Captured columns |
| --- | --- |
| `employees` | id, tenant_id, hired_at, terminated_at |
| `employments` | tenant_id, employee_id, department_id, position_id, location_id, effective_from, effective_to |
| `attendance_records` | id, tenant_id, employee_id, workday, duration_minutes, late, minutes_late |
| `overtime_requests` | id, tenant_id, employee_id, work_date, hours, status |
| `leave_requests` | id, tenant_id, employee_id, leave_type_id, start_date, days, status |
| `leave_types` | id, tenant_id, code |

## Wire encodings

Debezium's JSON encoding is kept as-is (no application-side coercion):

- UUID columns → hyphenated strings.
- `date` columns → integer epoch days (`.ai/features/change-data-capture.md` D9).
- `timestamptz` columns → Debezium/ISO-8601 strings (none are captured today).
- `numeric` columns → decimal strings when `decimal.handling.mode=string`
  (ADR 0017); `hours` is always a string, never a float.

## Fixtures

`apps/api/tests/contract/fixtures/cdc/` holds deterministic synthetic envelopes
for every captured table × operation class (`created`, `updated`, `deleted`,
`snapshot`, `truncated`). T7 adds `fixtures/cdc/recorded/` with real messages
from the running Debezium connector; the same test validates them.
