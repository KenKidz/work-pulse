-- WorkPulse — Analytics & Reports projection (Phase 4–5, ADR 0019/0024).
--
-- Current-state rows per entity, not an event log: the durable source is the
-- PostgreSQL log captured by Debezium (Phase 5, ADR 0024); `workpulse.events`
-- remains the domain-event backbone for its other consumers. `updated_at` is
-- the ReplacingMergeTree version (the source commit timestamp) and report math
-- never reads it. Reads must use `FINAL` (or an equivalent argMax fold); a query
-- that forgets it doubles aggregates.
--
-- Applied idempotently by `app.infrastructure.clickhouse.schema` (the CDC sink
-- fails loudly, the API is best-effort — ADR 0019 decision 11 / ADR 0024). Schema
-- changes are additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements
-- appended here; destructive changes need a rebuild plus a new ADR.

CREATE TABLE IF NOT EXISTS dim_employee
(
    tenant_id UUID,
    employee_id UUID,
    hired_at Nullable(Date),
    terminated_at Nullable(Date),
    updated_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (tenant_id, employee_id);

CREATE TABLE IF NOT EXISTS dim_assignment
(
    tenant_id UUID,
    employee_id UUID,
    effective_from Date,
    effective_to Nullable(Date),
    department_id Nullable(UUID),
    position_id Nullable(UUID),
    location_id Nullable(UUID),
    updated_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (tenant_id, employee_id, effective_from);

CREATE TABLE IF NOT EXISTS fact_overtime
(
    tenant_id UUID,
    overtime_id UUID,
    employee_id UUID,
    work_date Date,
    hours Decimal(9, 2),
    status LowCardinality(String),
    department_id Nullable(UUID),
    position_id Nullable(UUID),
    location_id Nullable(UUID),
    hired_at Nullable(Date),
    updated_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(work_date)
ORDER BY (tenant_id, overtime_id);

CREATE TABLE IF NOT EXISTS fact_attendance
(
    tenant_id UUID,
    attendance_id UUID,
    employee_id UUID,
    workday Date,
    duration_minutes Nullable(Int32),
    late Bool,
    minutes_late Nullable(Int32),
    department_id Nullable(UUID),
    position_id Nullable(UUID),
    location_id Nullable(UUID),
    hired_at Nullable(Date),
    updated_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(workday)
ORDER BY (tenant_id, attendance_id);

CREATE TABLE IF NOT EXISTS fact_leave
(
    tenant_id UUID,
    leave_request_id UUID,
    employee_id UUID,
    start_date Date,
    leave_type_code LowCardinality(String),
    -- UInt32 since Phase 5: `leave_requests.days` spans the full calendar
    -- range for balance-free types (unpaid), and every positive int32 the
    -- OLTP check allows must fit the CDC projection (ADR 0024).
    approved_day_count UInt32,
    department_id Nullable(UUID),
    position_id Nullable(UUID),
    location_id Nullable(UUID),
    hired_at Nullable(Date),
    updated_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(start_date)
ORDER BY (tenant_id, leave_request_id);

-- Widen projections created before Phase 5 in place. `MODIFY COLUMN` to the
-- same type is a no-op, so applying the DDL repeatedly stays idempotent.
ALTER TABLE fact_leave
    MODIFY COLUMN approved_day_count UInt32;

-- Rebuild fence (ADR 0019 decision 7): the rebuild procedure writes
-- `ready = 0` before truncating and `ready = 1` once the replay has caught up.
-- A missing `reports` row is a fresh projection and is served normally, so this
-- script deliberately does not seed it — seeding would defeat the fence when
-- an API/worker restart applies the DDL mid-rebuild.
CREATE TABLE IF NOT EXISTS projection_state
(
    key LowCardinality(String),
    ready UInt8,
    updated_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (key);
