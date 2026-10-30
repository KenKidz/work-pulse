"""T3 — ClickHouse schema smoke over the throwaway `clickhouse-test` (:8124).

`tests/conftest.py` pins `CLICKHOUSE_URL` before any `app.*` import, so the
lazily-created client always targets the test store. The DDL is applied twice
to prove idempotence, then the engine/keys are asserted so the ADR 0019 table
design cannot drift silently. The fixture drops the projection before and after
each test, leaving the shared test store empty for the next suite.
"""

from collections.abc import Iterator

import pytest
from clickhouse_connect.driver.client import Client

from app.infrastructure.clickhouse.client import (
    dispose_clickhouse,
    get_clickhouse_client,
)
from app.infrastructure.clickhouse.schema import (
    PROJECTION_STATE_TABLE,
    PROJECTION_TABLES,
    SCHEMA_TABLES,
    apply_clickhouse_schema,
)

# The pinned table design (ADR 0019 decision 2): sorting key, partition key.
# The full `ReplacingMergeTree(updated_at)` version is asserted on `engine_full`.
_EXPECTED_KEYS = {
    "dim_employee": ("tenant_id, employee_id", ""),
    "dim_assignment": ("tenant_id, employee_id, effective_from", ""),
    "fact_overtime": ("tenant_id, overtime_id", "toYYYYMM(work_date)"),
    "fact_attendance": ("tenant_id, attendance_id", "toYYYYMM(workday)"),
    "fact_leave": ("tenant_id, leave_request_id", "toYYYYMM(start_date)"),
    "projection_state": ("key", ""),
}


def _drop_projection_tables(client: Client) -> None:
    for table in SCHEMA_TABLES:
        client.command(f"DROP TABLE IF EXISTS {table}")


def _table_facts(client: Client) -> dict[str, tuple[str, str, str]]:
    """Every table in the test database, from `system.tables`.

    No filter by `SCHEMA_TABLES`: the idempotence test asserts exact equality,
    so a table added to `schema.sql` without updating the Python constants
    fails the gate instead of silently escaping the rebuild set.
    """
    result = client.query(
        "SELECT name, engine_full, sorting_key, partition_key "
        "FROM system.tables WHERE database = currentDatabase()"
    )
    return {row[0]: (row[1], row[2], row[3]) for row in result.result_rows}


@pytest.fixture
def clickhouse() -> Iterator[Client]:
    client = get_clickhouse_client()
    _drop_projection_tables(client)
    try:
        yield client
    finally:
        _drop_projection_tables(client)
        dispose_clickhouse()


def test_schema_applies_idempotently(clickhouse: Client) -> None:
    apply_clickhouse_schema(clickhouse)

    # A fresh projection has no `reports` marker: a missing row means "ready".
    # Seeding `ready = 1` in the DDL would defeat the rebuild fence (ADR 0019
    # decision 7), so the schema must leave the marker to the rebuild procedure.
    assert clickhouse.command("SELECT count() FROM projection_state") == 0

    # Seed a fact and an in-progress rebuild fence before re-applying: a real
    # rebuild restart must preserve both. A future DROP/TRUNCATE slipped into
    # the DDL would fail this test instead of silently destroying data.
    clickhouse.command(
        "INSERT INTO fact_overtime "
        "(tenant_id, overtime_id, employee_id, work_date, hours, status, "
        "updated_at) VALUES "
        "('00000000-0000-4000-8000-00000000000a', "
        "'00000000-0000-4000-8000-0000000000a1', "
        "'00000000-0000-4000-8000-000000000001', "
        "'2026-01-05', toDecimal64(1.5, 2), 'hr_verified', now64(6))"
    )
    clickhouse.command(
        "INSERT INTO projection_state (key, ready, updated_at) "
        "VALUES ('reports', 0, now64(6))"
    )

    apply_clickhouse_schema(clickhouse)

    facts = _table_facts(clickhouse)
    assert set(facts) == set(SCHEMA_TABLES)
    assert clickhouse.command("SELECT count() FROM fact_overtime") == 1
    assert (
        clickhouse.command("SELECT ready FROM projection_state WHERE key = 'reports'")
        == 0
    )
    # The truncate set is the five current-state tables, never the fence.
    assert PROJECTION_STATE_TABLE not in PROJECTION_TABLES


def test_schema_pins_the_adr_table_design(clickhouse: Client) -> None:
    apply_clickhouse_schema(clickhouse)
    facts = _table_facts(clickhouse)

    for table, (sorting_key, partition_key) in _EXPECTED_KEYS.items():
        engine_full, actual_sorting_key, actual_partition_key = facts[table]
        assert "ReplacingMergeTree(updated_at)" in engine_full, table
        assert actual_sorting_key == sorting_key, table
        assert actual_partition_key == partition_key, table


def test_fact_leave_day_count_is_wide_enough_for_cdc(clickhouse: Client) -> None:
    """Unpaid leave is not balance-capped; the projection must hold Int32 days."""
    apply_clickhouse_schema(clickhouse)

    column_type = clickhouse.command(
        "SELECT type FROM system.columns "
        "WHERE database = currentDatabase() "
        "AND table = 'fact_leave' AND name = 'approved_day_count'"
    )
    assert column_type == "UInt32"
