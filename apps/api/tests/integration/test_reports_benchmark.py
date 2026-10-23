"""T5 — the flagship report's latency budget (`.ai/product.md` Phase-4 metric).

One bounded integration benchmark, deliberately the only wall-clock assertion
in the suite: the six-month overtime report grouped by department, location and
seniority must answer in under one second over a seeded projection volume.
ClickHouse answers this in milliseconds, so the bound carries roughly two
orders of magnitude of headroom; if it ever flakes on a loaded machine, widen
the seed, not the bound.
"""

import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from clickhouse_connect.driver.client import Client

from app.infrastructure.clickhouse.client import (
    dispose_clickhouse,
    get_clickhouse_client,
)
from app.infrastructure.clickhouse.schema import (
    SCHEMA_TABLES,
    apply_clickhouse_schema,
)
from tests.integration.conftest import TENANT_A, ClientFactory

COLUMNS = (
    "tenant_id",
    "overtime_id",
    "employee_id",
    "work_date",
    "hours",
    "status",
    "department_id",
    "position_id",
    "location_id",
    "hired_at",
    "updated_at",
)

ROWS = 3000
# The endpoint's overtime denominator folds the dimension state, so the seed
# must exercise the workforce scale the benchmark is meant to protect.
EMPLOYEES = 1000
DEPARTMENTS = 3
LOCATIONS = 3
START = date(2026, 1, 1)
DAYS = 181  # 2026-01-01 .. 2026-06-30


@pytest.fixture
def clickhouse() -> Iterator[Client]:
    client = get_clickhouse_client()
    for table in SCHEMA_TABLES:
        client.command(f"DROP TABLE IF EXISTS {table}")
    apply_clickhouse_schema(client)
    try:
        yield client
    finally:
        for table in SCHEMA_TABLES:
            client.command(f"DROP TABLE IF EXISTS {table}")
        dispose_clickhouse()


def _seed(clickhouse: Client) -> None:
    stamp = datetime(2026, 7, 1, tzinfo=UTC)
    department_ids = [UUID(int=0xD000 + index) for index in range(DEPARTMENTS)]
    position_ids = [UUID(int=0xE000 + index) for index in range(DEPARTMENTS)]
    location_ids = [UUID(int=0xF000 + index) for index in range(LOCATIONS)]
    employee_ids = [UUID(int=0xA000 + index) for index in range(EMPLOYEES)]

    rows = []
    for index in range(ROWS):
        work_date = START + timedelta(days=index % DAYS)
        employee_index = index % EMPLOYEES
        employee = employee_ids[employee_index]
        # Keep each fact's stamps consistent with the employee's assignment.
        department = employee_index % DEPARTMENTS
        rows.append(
            [
                TENANT_A,
                UUID(int=0x100000 + index),
                employee,
                work_date,
                Decimal("2.50"),
                "hr_verified",
                department_ids[department],
                position_ids[department],
                location_ids[employee_index % LOCATIONS],
                date(2025, 6, 1),
                stamp,
            ]
        )
    clickhouse.insert("fact_overtime", rows, column_names=list(COLUMNS))

    # The overtime rate's denominator comes from the dimension tables.
    clickhouse.insert(
        "dim_employee",
        [
            [TENANT_A, employee, date(2025, 6, 1), None, stamp]
            for employee in employee_ids
        ],
        column_names=[
            "tenant_id",
            "employee_id",
            "hired_at",
            "terminated_at",
            "updated_at",
        ],
    )
    clickhouse.insert(
        "dim_assignment",
        [
            [
                TENANT_A,
                employee_ids[index],
                date(2025, 6, 1),
                None,
                department_ids[index % DEPARTMENTS],
                position_ids[index % DEPARTMENTS],
                location_ids[index % LOCATIONS],
                stamp,
            ]
            for index in range(EMPLOYEES)
        ],
        column_names=[
            "tenant_id",
            "employee_id",
            "effective_from",
            "effective_to",
            "department_id",
            "position_id",
            "location_id",
            "updated_at",
        ],
    )


@pytest.mark.asyncio
async def test_six_month_flagship_query_is_under_one_second(
    client_factory: ClientFactory, clickhouse: Client
) -> None:
    _seed(clickhouse)

    async with client_factory() as client:
        started = time.perf_counter()
        response = await client.get(
            "/api/v1/reports/overtime",
            params={
                "from": START.isoformat(),
                "to": "2026-06-30",
                "group_by": "department,location,seniority",
                "interval": "month",
            },
        )
        elapsed = time.perf_counter() - started

    assert response.status_code == 200, response.text
    report = response.json()
    # Six months × groups; every row is a real aggregate, not an empty shell.
    assert report["rows"]
    assert sum(row["verified_request_count"] for row in report["rows"]) == ROWS
    assert elapsed < 1.0, f"flagship report took {elapsed:.3f}s"
