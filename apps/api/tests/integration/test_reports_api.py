"""T5 — Reports read API over real ClickHouse (`clickhouse-test`, :8124).

Every test drives the real router, report service, ClickHouse repository and
the two Postgres lookup ports. The projection is seeded through
`ClickhouseCdcWriter` (the production CDC landing adapter: stamp-free fact rows
and as-of dimension intervals); Postgres holds the Organizations/Employees
reference rows so labels and manager scope resolve for real.

Covered (`.ai/features/analytics-reports.md` → acceptance criteria):

- exact per-group per-month overtime aggregates, the manager slice and the
  Employee-role denial;
- attendance/lateness, leave (incl. `group_by=leave_type`) and headcount
  (`interval=total`, joiners/leavers, mid-range transfer split);
- `422` validation, empty results for unknown/foreign filters and tenant B,
  `503` when ClickHouse is down or the rebuild fence is set;
- pre-extension rows: first-assignment hire anchor + `unknown` seniority;
- the PII sweep (no names, emails or employee ids in any response).
"""

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from clickhouse_connect.driver.client import Client
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.reports.attribution import seniority_band
from app.domains.reports.schemas import (
    AssignmentRow,
    AttendanceFact,
    EmployeeRow,
    LeaveFact,
    OvertimeFact,
)
from app.infrastructure.clickhouse.cdc_projection import ClickhouseCdcWriter
from app.infrastructure.clickhouse.client import (
    dispose_clickhouse,
    get_clickhouse_client,
)
from app.infrastructure.clickhouse.schema import (
    SCHEMA_TABLES,
    apply_clickhouse_schema,
)
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.repositories.leave import PostgresLeaveRepository
from app.shared.identity import Role
from tests.integration.conftest import (
    TENANT_A,
    TENANT_B,
    ClientFactory,
    create_org_refs,
    employee_payload,
    identity,
)

RANGE_FROM = date(2026, 1, 1)
RANGE_TO = date(2026, 6, 30)
FEB_FROM = date(2026, 2, 1)
FEB_TO = date(2026, 2, 28)

HIRED = date(2026, 1, 5)
LEAVER_HIRED = date(2026, 1, 10)
TRANSFER_DATE = date(2026, 3, 1)
TERMINATED = date(2026, 3, 15)


@dataclass(frozen=True, slots=True)
class Seed:
    """The ids/labels the report assertions refer to."""

    manager_id: UUID
    manager2_id: UUID
    report_id: UUID
    other_id: UUID
    leaver_id: UUID
    transferee_id: UUID
    refs_a: tuple[str, str, str]
    refs_b: tuple[str, str, str]


@pytest.fixture
def clickhouse() -> Iterator[Client]:
    client = get_clickhouse_client()
    _drop_tables(client)
    apply_clickhouse_schema(client)
    try:
        yield client
    finally:
        _drop_tables(client)
        dispose_clickhouse()


def _drop_tables(client: Client) -> None:
    for table in SCHEMA_TABLES:
        client.command(f"DROP TABLE IF EXISTS {table}")


_BASE_STAMP = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
_stamp_sequence = 0


def _next_stamp() -> datetime:
    """A monotonically increasing write version for one test process.

    `ReplacingMergeTree` resolves equal versions non-deterministically, and the
    tests rewrite the same dimension/fact key (for example a backdated transfer
    closing an interval). The source commit timestamp plays this role in
    production; here each seed write is one microsecond newer than the last.
    """
    global _stamp_sequence
    _stamp_sequence += 1
    return _BASE_STAMP + timedelta(microseconds=_stamp_sequence)


async def _create_employee(
    admin: AsyncClient,
    refs: tuple[str, str, str],
    *,
    first_name: str,
    last_name: str,
    email: str,
    hired_at: str = HIRED.isoformat(),
    manager_id: str | None = None,
) -> dict[str, Any]:
    response = await admin.post(
        "/api/v1/employees",
        json=employee_payload(
            refs,
            first_name=first_name,
            last_name=last_name,
            email=email,
            hired_at=hired_at,
            manager_id=manager_id,
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _seed_dimension(
    writer: ClickhouseCdcWriter,
    employee_id: UUID,
    *,
    hired_at: date | None,
    assignments: tuple[AssignmentRow, ...],
    terminated_at: date | None = None,
) -> None:
    writer.upsert_employee(
        EmployeeRow(
            tenant_id=TENANT_A,
            employee_id=employee_id,
            hired_at=hired_at,
            terminated_at=terminated_at,
        ),
        _next_stamp(),
    )
    for assignment in assignments:
        writer.upsert_assignment(assignment, _next_stamp())
    writer.flush()


def _assignment(
    employee_id: UUID,
    *,
    effective_from: date,
    effective_to: date | None,
    refs: tuple[str, str, str],
) -> AssignmentRow:
    department_id, position_id, location_id = refs
    return AssignmentRow(
        tenant_id=TENANT_A,
        employee_id=employee_id,
        effective_from=effective_from,
        effective_to=effective_to,
        department_id=UUID(department_id),
        position_id=UUID(position_id),
        location_id=UUID(location_id),
    )


def _overtime(
    writer: ClickhouseCdcWriter,
    overtime_id: UUID,
    employee_id: UUID,
    *,
    work_date: date,
    hours: str,
    status: str,
    refs: tuple[str, str, str],
    hired_at: date | None = HIRED,
) -> None:
    # `refs`/`hired_at` stay in the signature because callers describe the
    # fixture's dimensions; the CDC writer leaves the stamps null and the read
    # side resolves them as of `work_date` (ADR 0024 D3).
    del refs, hired_at
    writer.upsert_overtime(
        OvertimeFact(
            tenant_id=TENANT_A,
            overtime_id=overtime_id,
            employee_id=employee_id,
            work_date=work_date,
            hours=Decimal(hours),
            status=status,
        ),
        _next_stamp(),
    )
    writer.flush()


def _attendance(
    writer: ClickhouseCdcWriter,
    attendance_id: UUID,
    employee_id: UUID,
    *,
    workday: date,
    duration_minutes: int | None,
    late: bool,
    minutes_late: int | None,
    refs: tuple[str, str, str],
    hired_at: date | None = HIRED,
) -> None:
    del refs, hired_at
    writer.upsert_attendance(
        AttendanceFact(
            tenant_id=TENANT_A,
            attendance_id=attendance_id,
            employee_id=employee_id,
            workday=workday,
            duration_minutes=duration_minutes,
            late=late,
            minutes_late=minutes_late,
        ),
        _next_stamp(),
    )
    writer.flush()


def _leave(
    writer: ClickhouseCdcWriter,
    leave_request_id: UUID,
    employee_id: UUID,
    *,
    start_date: date,
    leave_type_code: str,
    days: int,
    refs: tuple[str, str, str],
    hired_at: date | None = HIRED,
) -> None:
    del refs, hired_at
    writer.upsert_leave(
        LeaveFact(
            tenant_id=TENANT_A,
            leave_request_id=leave_request_id,
            employee_id=employee_id,
            start_date=start_date,
            leave_type_code=leave_type_code,
            approved_day_count=days,
        ),
        _next_stamp(),
    )
    writer.flush()


@pytest.fixture
async def seeded(
    client_factory: ClientFactory,
    clickhouse: Client,
    session: AsyncSession,
) -> AsyncIterator[Seed]:
    """Create real reference rows + the projected facts every report reads."""
    # Production provisions the seeded leave types on first use; reports resolve
    # `leave_type_code` through the same reference table, so seed it here.
    await PostgresLeaveRepository(session).ensure_leave_types(TENANT_A)

    async with client_factory() as admin:
        refs_a = await create_org_refs(admin, suffix=" A")
        refs_b = await create_org_refs(admin, suffix=" B")

        manager = await _create_employee(
            admin,
            refs_a,
            first_name="Mia",
            last_name="Manager",
            email="mia.manager@example.test",
        )
        report = await _create_employee(
            admin,
            refs_a,
            first_name="Riley",
            last_name="Report",
            email="riley.report@example.test",
            manager_id=manager["id"],
        )
        manager_two = await _create_employee(
            admin,
            refs_b,
            first_name="Mona",
            last_name="Second",
            email="mona.second@example.test",
        )
        other = await _create_employee(
            admin,
            refs_b,
            first_name="Owen",
            last_name="Other",
            email="owen.other@example.test",
            manager_id=manager_two["id"],
        )
        leaver = await _create_employee(
            admin,
            refs_a,
            first_name="Lee",
            last_name="Leaver",
            email="lee.leaver@example.test",
            hired_at=LEAVER_HIRED.isoformat(),
        )
        transferee = await _create_employee(
            admin,
            refs_a,
            first_name="Tara",
            last_name="Transfer",
            email="tara.transfer@example.test",
        )

    writer = ClickhouseCdcWriter(clickhouse)
    manager_id = UUID(manager["id"])
    manager_two_id = UUID(manager_two["id"])
    report_id = UUID(report["id"])
    other_id = UUID(other["id"])
    leaver_id = UUID(leaver["id"])
    transferee_id = UUID(transferee["id"])

    for employee_id, refs, hired_at in (
        (manager_id, refs_a, HIRED),
        (report_id, refs_a, HIRED),
        (other_id, refs_b, HIRED),
        (transferee_id, refs_a, HIRED),
    ):
        _seed_dimension(
            writer,
            employee_id,
            hired_at=hired_at,
            assignments=(
                _assignment(
                    employee_id,
                    effective_from=hired_at,
                    effective_to=None,
                    refs=refs,
                ),
            ),
        )
    _seed_dimension(
        writer,
        leaver_id,
        hired_at=LEAVER_HIRED,
        terminated_at=TERMINATED,
        assignments=(
            _assignment(
                leaver_id,
                effective_from=LEAVER_HIRED,
                effective_to=TERMINATED,
                refs=refs_a,
            ),
        ),
    )
    # The transferee's mid-range transfer splits their facts at 2026-03-01.
    _seed_dimension(
        writer,
        transferee_id,
        hired_at=HIRED,
        assignments=(
            _assignment(
                transferee_id,
                effective_from=HIRED,
                effective_to=TRANSFER_DATE,
                refs=refs_a,
            ),
            _assignment(
                transferee_id,
                effective_from=TRANSFER_DATE,
                effective_to=None,
                refs=refs_b,
            ),
        ),
    )

    _overtime(
        writer,
        UUID("00000000-0000-4000-8000-0000000000c1"),
        manager_id,
        work_date=date(2026, 2, 10),
        hours="2.50",
        status="hr_verified",
        refs=refs_a,
    )
    _overtime(
        writer,
        UUID("00000000-0000-4000-8000-0000000000c2"),
        report_id,
        work_date=date(2026, 2, 11),
        hours="3.00",
        status="hr_verified",
        refs=refs_a,
    )
    _overtime(
        writer,
        UUID("00000000-0000-4000-8000-0000000000c3"),
        other_id,
        work_date=date(2026, 2, 12),
        hours="1.25",
        status="hr_verified",
        refs=refs_b,
    )
    _overtime(
        writer,
        UUID("00000000-0000-4000-8000-0000000000c4"),
        manager_id,
        work_date=date(2026, 2, 13),
        hours="5.00",
        status="requested",
        refs=refs_a,
    )
    _overtime(
        writer,
        UUID("00000000-0000-4000-8000-0000000000c5"),
        transferee_id,
        # Before the 2026-03-01 transfer: the as-of join resolves the old
        # department (a post-transfer fact would resolve to B).
        work_date=date(2026, 2, 20),
        hours="2.00",
        status="hr_verified",
        refs=refs_a,
    )
    _overtime(
        writer,
        UUID("00000000-0000-4000-8000-0000000000c6"),
        transferee_id,
        work_date=date(2026, 6, 10),
        hours="3.00",
        status="hr_verified",
        refs=refs_b,
    )

    _attendance(
        writer,
        UUID("00000000-0000-4000-8000-0000000000d1"),
        manager_id,
        workday=date(2026, 2, 10),
        duration_minutes=480,
        late=False,
        minutes_late=None,
        refs=refs_a,
    )
    _attendance(
        writer,
        UUID("00000000-0000-4000-8000-0000000000d2"),
        report_id,
        workday=date(2026, 2, 11),
        duration_minutes=450,
        late=True,
        minutes_late=15,
        refs=refs_a,
    )
    _attendance(
        writer,
        UUID("00000000-0000-4000-8000-0000000000d3"),
        other_id,
        workday=date(2026, 2, 12),
        duration_minutes=None,
        late=False,
        minutes_late=None,
        refs=refs_b,
    )

    _leave(
        writer,
        UUID("00000000-0000-4000-8000-0000000000e1"),
        manager_id,
        start_date=date(2026, 2, 2),
        leave_type_code="annual",
        days=2,
        refs=refs_a,
    )
    _leave(
        writer,
        UUID("00000000-0000-4000-8000-0000000000e2"),
        report_id,
        start_date=date(2026, 2, 3),
        leave_type_code="sick",
        days=1,
        refs=refs_a,
    )
    _leave(
        writer,
        UUID("00000000-0000-4000-8000-0000000000e3"),
        other_id,
        start_date=date(2026, 2, 4),
        leave_type_code="annual",
        days=3,
        refs=refs_b,
    )

    yield Seed(
        manager_id=manager_id,
        manager2_id=manager_two_id,
        report_id=report_id,
        other_id=other_id,
        leaver_id=leaver_id,
        transferee_id=transferee_id,
        refs_a=refs_a,
        refs_b=refs_b,
    )


def _get_params(**overrides: Any) -> dict[str, str]:
    params = {
        "from": FEB_FROM.isoformat(),
        "to": FEB_TO.isoformat(),
    }
    params.update(overrides)
    return params


def _rows_by_label(report: dict[str, Any], index: int = 0) -> dict[str, dict[str, Any]]:
    return {row["groups"][index]["label"]: row for row in report["rows"]}


@pytest.mark.asyncio
async def test_overtime_flagship_aggregates_and_manager_slice(
    client_factory: ClientFactory, clickhouse: Client, seeded: Seed
) -> None:
    async with client_factory() as client:
        response = await client.get(
            "/api/v1/reports/overtime",
            params=_get_params(
                group_by="department,location,seniority",
                to=RANGE_TO.isoformat(),
                **{"from": RANGE_FROM.isoformat()},
            ),
        )
        assert response.status_code == 200, response.text
        report = response.json()

        assert report["from"] == RANGE_FROM.isoformat()
        assert report["to"] == RANGE_TO.isoformat()
        assert report["group_by"] == ["department", "location", "seniority"]

        rows = {
            (
                row["bucket"],
                row["groups"][0]["label"],
            ): row
            for row in report["rows"]
        }
        feb_a = rows[("2026-02-01", "Engineering A")]
        assert feb_a["groups"][1]["label"] == "HQ A"
        assert feb_a["groups"][2]["label"] == "< 1 year"
        # ADR 0019 decision 9: fixed-scale decimal strings, not JSON numbers.
        # February A holds the manager, the report and the transferee's
        # pre-transfer claim (2.50 + 3.00 + 2.00).
        assert feb_a["verified_hours"] == "7.50"
        assert feb_a["verified_request_count"] == 3
        assert feb_a["verified_day_count"] == 3
        # The denominator is everyone employed in the group during the bucket:
        # manager, report, the March leaver and the pre-transfer transferee.
        assert feb_a["active_employee_count"] == 4
        assert feb_a["hours_per_employee"] == "1.88"  # 7.5 / 4, half-even 2dp

        feb_b = rows[("2026-02-01", "Engineering B")]
        assert feb_b["verified_hours"] == "1.25"
        assert feb_b["verified_request_count"] == 1
        assert feb_b["active_employee_count"] == 1
        assert feb_b["hours_per_employee"] == "1.25"

        # The unverified 5.00-hour request never reaches the aggregate.
        total_hours = sum(Decimal(row["verified_hours"]) for row in report["rows"])
        assert total_hours == Decimal("11.75")

        # Mid-range transfer attribution: the pre-transfer fact is in the
        # February A row (above) and the post-transfer fact is in June B; no
        # fact is attributed to May in the old department.
        assert ("2026-05-01", "Engineering A") not in rows
        assert rows[("2026-06-01", "Engineering B")]["verified_hours"] == "3.00"
        assert rows[("2026-06-01", "Engineering B")]["active_employee_count"] == 2
        assert rows[("2026-06-01", "Engineering B")]["hours_per_employee"] == "1.50"

        # A manager sees exactly self + direct reports (the transferee and the
        # other team are out of scope).
        manager = identity(role=Role.MANAGER, employee_id=seeded.manager_id)
        async with client_factory(manager) as manager_client:
            scoped = await manager_client.get(
                "/api/v1/reports/overtime",
                params=_get_params(
                    group_by="department",
                    **{"from": RANGE_FROM.isoformat(), "to": RANGE_TO.isoformat()},
                ),
            )
            assert scoped.status_code == 200, scoped.text
            assert scoped.json()["rows"] == [
                {
                    "bucket": "2026-02-01",
                    "groups": [
                        {
                            "dimension": "department",
                            "key": feb_a["groups"][0]["key"],
                            "label": "Engineering A",
                        }
                    ],
                    "verified_hours": "5.50",
                    "verified_request_count": 2,
                    "verified_day_count": 2,
                    "active_employee_count": 2,
                    "hours_per_employee": "2.75",
                }
            ]

        # The second manager sees only their own team — the two scoped reads
        # never leak across manager boundaries.
        manager_two = identity(role=Role.MANAGER, employee_id=seeded.manager2_id)
        async with client_factory(manager_two) as manager_two_client:
            scoped_two = await manager_two_client.get(
                "/api/v1/reports/overtime",
                params=_get_params(
                    group_by="department",
                    **{"from": RANGE_FROM.isoformat(), "to": RANGE_TO.isoformat()},
                ),
            )
            assert scoped_two.status_code == 200, scoped_two.text
            assert scoped_two.json()["rows"] == [
                {
                    "bucket": "2026-02-01",
                    "groups": [
                        {
                            "dimension": "department",
                            "key": feb_b["groups"][0]["key"],
                            "label": "Engineering B",
                        }
                    ],
                    "verified_hours": "1.25",
                    "verified_request_count": 1,
                    "verified_day_count": 1,
                    "active_employee_count": 1,
                    "hours_per_employee": "1.25",
                }
            ]

        # The Employee role is denied by policy, not by the UI.
        employee = identity(role=Role.EMPLOYEE, employee_id=seeded.report_id)
        async with client_factory(employee) as employee_client:
            denied = await employee_client.get(
                "/api/v1/reports/overtime", params=_get_params()
            )
            assert denied.status_code == 403
            assert denied.json()["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_day_week_and_total_buckets(
    client_factory: ClientFactory, clickhouse: Client, seeded: Seed
) -> None:
    async with client_factory() as client:
        daily = await client.get(
            "/api/v1/reports/overtime",
            params=_get_params(group_by="department", interval="day"),
        )
        assert daily.status_code == 200, daily.text
        day_rows = {
            (row["bucket"], row["groups"][0]["label"]): row
            for row in daily.json()["rows"]
        }
        assert day_rows[("2026-02-10", "Engineering A")]["verified_hours"] == "2.50"
        assert day_rows[("2026-02-11", "Engineering A")]["verified_hours"] == "3.00"
        assert day_rows[("2026-02-12", "Engineering B")]["verified_hours"] == "1.25"
        assert day_rows[("2026-02-10", "Engineering A")]["active_employee_count"] == 4

        weekly = await client.get(
            "/api/v1/reports/overtime",
            params=_get_params(group_by="department", interval="week"),
        )
        assert weekly.status_code == 200, weekly.text
        week_rows = {
            (row["bucket"], row["groups"][0]["label"]): row
            for row in weekly.json()["rows"]
        }
        # 2026-02-10 is a Tuesday; 2026-02-20 is the Friday of the next week.
        assert week_rows[("2026-02-09", "Engineering A")]["verified_hours"] == "5.50"
        assert week_rows[("2026-02-09", "Engineering B")]["verified_hours"] == "1.25"
        assert week_rows[("2026-02-16", "Engineering A")]["verified_hours"] == "2.00"

        total = await client.get(
            "/api/v1/reports/overtime",
            params=_get_params(
                group_by="department",
                interval="total",
                **{"from": RANGE_FROM.isoformat(), "to": RANGE_TO.isoformat()},
            ),
        )
        assert total.status_code == 200, total.text
        total_rows = _rows_by_label(total.json())
        assert total_rows["Engineering A"]["bucket"] is None
        assert total_rows["Engineering A"]["verified_hours"] == "7.50"
        assert total_rows["Engineering A"]["active_employee_count"] == 4
        assert total_rows["Engineering A"]["hours_per_employee"] == "1.88"
        assert total_rows["Engineering B"]["verified_hours"] == "4.25"
        assert total_rows["Engineering B"]["active_employee_count"] == 2
        assert total_rows["Engineering B"]["hours_per_employee"] == "2.12"


@pytest.mark.asyncio
async def test_attendance_leave_and_headcount_metrics(
    client_factory: ClientFactory, clickhouse: Client, seeded: Seed
) -> None:
    async with client_factory() as client:
        attendance = await client.get(
            "/api/v1/reports/attendance",
            params=_get_params(group_by="department"),
        )
        assert attendance.status_code == 200, attendance.text
        rows = _rows_by_label(attendance.json())
        assert rows["Engineering A"]["checkin_count"] == 2
        assert rows["Engineering A"]["worked_day_count"] == 2
        assert rows["Engineering A"]["worked_minutes"] == 930
        assert rows["Engineering A"]["late_count"] == 1
        assert rows["Engineering A"]["late_rate"] == "0.5000"
        assert rows["Engineering A"]["avg_minutes_late"] == "15.0"

        # Checked in but never out: a check-in, no worked day, no minutes.
        assert rows["Engineering B"]["checkin_count"] == 1
        assert rows["Engineering B"]["worked_day_count"] == 0
        assert rows["Engineering B"]["worked_minutes"] == 0
        assert rows["Engineering B"]["late_rate"] == "0.0000"
        assert rows["Engineering B"]["avg_minutes_late"] == "0.0"

        leave = await client.get(
            "/api/v1/reports/leave",
            params=_get_params(group_by="leave_type"),
        )
        assert leave.status_code == 200, leave.text
        leave_rows = _rows_by_label(leave.json())
        assert leave_rows["Annual leave"]["approved_day_count"] == 5
        assert leave_rows["Annual leave"]["approved_request_count"] == 2
        assert leave_rows["Sick leave"]["approved_day_count"] == 1
        assert leave_rows["Sick leave"]["approved_request_count"] == 1

        headcount = await client.get(
            "/api/v1/reports/headcount",
            params={
                "from": RANGE_FROM.isoformat(),
                "to": RANGE_TO.isoformat(),
                "group_by": "department",
                "interval": "month",
            },
        )
        assert headcount.status_code == 200, headcount.text
        by_bucket = {
            (row["bucket"], row["groups"][0]["label"]): row
            for row in headcount.json()["rows"]
        }
        assert by_bucket[("2026-01-01", "Engineering A")]["headcount"] == 4
        assert by_bucket[("2026-01-01", "Engineering A")]["joiners"] == 4
        assert by_bucket[("2026-01-01", "Engineering B")]["headcount"] == 1
        assert by_bucket[("2026-03-01", "Engineering A")]["headcount"] == 2
        assert by_bucket[("2026-03-01", "Engineering A")]["leavers"] == 1
        assert by_bucket[("2026-03-01", "Engineering B")]["headcount"] == 2
        assert by_bucket[("2026-03-01", "Engineering B")]["joiners"] == 0

        total = await client.get(
            "/api/v1/reports/headcount",
            params={
                "from": RANGE_FROM.isoformat(),
                "to": RANGE_TO.isoformat(),
                "group_by": "department",
                "interval": "total",
            },
        )
        assert total.status_code == 200, total.text
        total_rows = _rows_by_label(total.json())
        # The transferee is grouped by their assignment at the as-of date, so
        # their January join lands in B for a total-range report (headcount and
        # joiners share the same group attribution).
        assert total_rows["Engineering A"] == {
            "bucket": None,
            "groups": total_rows["Engineering A"]["groups"],
            "headcount": 2,
            "joiners": 3,
            "leavers": 1,
        }
        assert total_rows["Engineering B"]["headcount"] == 2
        assert total_rows["Engineering B"]["joiners"] == 2
        assert total_rows["Engineering B"]["leavers"] == 0


@pytest.mark.asyncio
async def test_validation_empty_results_and_tenant_isolation(
    client_factory: ClientFactory, clickhouse: Client, seeded: Seed
) -> None:
    async with client_factory() as client:
        invalid = (
            {"from": "2026-06-01", "to": "2026-05-01"},
            {"from": "2026-01-01", "to": "2028-01-02"},
            {"from": "2026-01-01", "to": "2026-02-01", "group_by": "x"},
            {
                "from": "2026-01-01",
                "to": "2026-02-01",
                "group_by": "department,department",
            },
            {
                "from": "2026-01-01",
                "to": "2026-02-01",
                "group_by": "department,location,position,seniority",
            },
        )
        for params in invalid:
            response = await client.get("/api/v1/reports/overtime", params=params)
            assert response.status_code == 422, (params, response.text)
            assert response.json()["error"]["code"] == "validation_error"

        # leave_type is leave-only.
        response = await client.get(
            "/api/v1/reports/attendance",
            params=_get_params(group_by="leave_type"),
        )
        assert response.status_code == 422

        # Unknown and foreign filter ids are empty results, never an error.
        for params in (
            _get_params(department_id="00000000-0000-4000-8000-000000000999"),
            _get_params(position_id="00000000-0000-4000-8000-000000000999"),
            _get_params(location_id="00000000-0000-4000-8000-000000000999"),
        ):
            response = await client.get("/api/v1/reports/overtime", params=params)
            assert response.status_code == 200
            assert response.json()["rows"] == []

        # An unlinked manager's scope is the empty tuple: `200 rows: []`, never
        # company-wide data.
        unlinked = identity(role=Role.MANAGER, employee_id=None)
        async with client_factory(unlinked) as unlinked_client:
            empty_scope = await unlinked_client.get(
                "/api/v1/reports/overtime", params=_get_params()
            )
            assert empty_scope.status_code == 200
            assert empty_scope.json()["rows"] == []

        # Tenant B sees nothing, even with a tenant-A dimension filter.
        tenant_b = identity(tenant=TENANT_B)
        async with client_factory(tenant_b) as tenant_b_client:
            empty = await tenant_b_client.get(
                "/api/v1/reports/overtime", params=_get_params()
            )
            assert empty.status_code == 200
            assert empty.json()["rows"] == []

            filtered = await tenant_b_client.get(
                "/api/v1/reports/overtime",
                params=_get_params(department_id=seeded.refs_a[0]),
            )
            assert filtered.status_code == 200
            assert filtered.json()["rows"] == []


@pytest.mark.asyncio
async def test_clickhouse_down_and_rebuild_fence_answer_503(
    client_factory: ClientFactory,
    clickhouse: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unreachable() -> Any:
        raise RuntimeError("clickhouse down")

    # Creation connects eagerly: a down store must still be a 503, not a 500.
    monkeypatch.setattr("app.dependencies.get_clickhouse_client", _unreachable)
    async with client_factory() as client:
        response = await client.get("/api/v1/reports/overtime", params=_get_params())
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "reports_unavailable"

    class _BrokenClient:
        def query(self, *_: Any, **__: Any) -> Any:
            raise RuntimeError("clickhouse down")

    # A client that exists but fails its first query maps through the adapter.
    monkeypatch.setattr(
        "app.dependencies.get_clickhouse_client", lambda: _BrokenClient()
    )
    async with client_factory() as client:
        query_failed = await client.get(
            "/api/v1/reports/overtime", params=_get_params()
        )
        assert query_failed.status_code == 503
        assert query_failed.json()["error"]["code"] == "reports_unavailable"

    monkeypatch.undo()

    clickhouse.insert(
        "projection_state",
        [["reports", 0, datetime.now(UTC)]],
        column_names=["key", "ready", "updated_at"],
    )
    async with client_factory() as client:
        fenced = await client.get("/api/v1/reports/overtime", params=_get_params())
        assert fenced.status_code == 503
        assert fenced.json()["error"]["code"] == "reports_unavailable"


@pytest.mark.asyncio
async def test_pre_extension_employee_uses_first_assignment_anchor(
    client_factory: ClientFactory, clickhouse: Client
) -> None:
    writer = ClickhouseCdcWriter(clickhouse)
    employee_id = UUID("00000000-0000-4000-8000-0000000000f1")
    department_id = UUID("00000000-0000-4000-8000-0000000000d9")
    _seed_dimension(
        writer,
        employee_id,
        hired_at=None,
        assignments=(
            AssignmentRow(
                tenant_id=TENANT_A,
                employee_id=employee_id,
                effective_from=HIRED,
                effective_to=None,
                department_id=department_id,
                position_id=None,
                location_id=None,
            ),
        ),
    )

    async with client_factory() as client:
        response = await client.get(
            "/api/v1/reports/headcount",
            params={
                "from": RANGE_FROM.isoformat(),
                "to": RANGE_TO.isoformat(),
                "group_by": "seniority",
                "interval": "total",
            },
        )
        assert response.status_code == 200, response.text
        [row] = response.json()["rows"]
        # The first assignment is the hire anchor: active and a January joiner.
        assert row["headcount"] == 1
        assert row["joiners"] == 1
        # Tenure itself is unknown without `hired_at` (honest degradation).
        assert row["groups"][0]["key"] == "unknown"
        assert row["groups"][0]["label"] == "Unknown"


@pytest.mark.asyncio
async def test_termination_on_bucket_start_keeps_the_denominator_group(
    client_factory: ClientFactory, clickhouse: Client
) -> None:
    """A termination date is active even though the assignment is half-open.

    Without the covering-assignment fallback the employee would be counted in
    the denominator under `unknown` while their fact row is grouped by the
    department, making `hours_per_employee` 0.00 for that day.
    """
    writer = ClickhouseCdcWriter(clickhouse)
    employee_id = UUID("00000000-0000-4000-8000-0000000000f2")
    department_id = UUID("00000000-0000-4000-8000-0000000000d8")
    position_id = UUID("00000000-0000-4000-8000-0000000000e8")
    location_id = UUID("00000000-0000-4000-8000-0000000000f8")
    termination = date(2026, 2, 1)

    _seed_dimension(
        writer,
        employee_id,
        hired_at=date(2026, 1, 1),
        terminated_at=termination,
        assignments=(
            AssignmentRow(
                tenant_id=TENANT_A,
                employee_id=employee_id,
                effective_from=date(2026, 1, 1),
                effective_to=termination,
                department_id=department_id,
                position_id=position_id,
                location_id=location_id,
            ),
        ),
    )
    _overtime(
        writer,
        UUID("00000000-0000-4000-8000-0000000000c7"),
        employee_id,
        work_date=termination,
        hours="2.50",
        status="hr_verified",
        refs=(str(department_id), str(position_id), str(location_id)),
        hired_at=date(2026, 1, 1),
    )

    async with client_factory() as client:
        response = await client.get(
            "/api/v1/reports/overtime",
            params={
                "from": termination.isoformat(),
                "to": termination.isoformat(),
                "group_by": "department",
                "interval": "day",
            },
        )
        assert response.status_code == 200, response.text
        [row] = response.json()["rows"]
        assert row["groups"][0]["key"] == str(department_id)
        assert row["verified_hours"] == "2.50"
        assert row["active_employee_count"] == 1
        assert row["hours_per_employee"] == "2.50"


@pytest.mark.asyncio
async def test_backdated_transfer_corrects_attribution_without_restamp(
    client_factory: ClientFactory, clickhouse: Client
) -> None:
    """Attribution reads captured interval history, not the fact's stamp.

    The fact is projected before the transfer is known, so its stored
    `department_id` still points at A. When the backdated `dim_assignment`
    interval lands, the next query must attribute the fact to B — the CDC
    path's replacement for the retired fold's re-stamp pass (rule 6 / D3).
    Dimension
    filters apply to the *resolved* assignment, never to a pre-filtered
    interval set, so the same read filtered by A is empty.
    """
    writer = ClickhouseCdcWriter(clickhouse)
    employee_id = UUID("00000000-0000-4000-8000-0000000000f4")
    department_a = UUID("00000000-0000-4000-8000-0000000000d4")
    department_b = UUID("00000000-0000-4000-8000-0000000000d5")
    position_id = UUID("00000000-0000-4000-8000-0000000000e4")
    location_id = UUID("00000000-0000-4000-8000-0000000000f5")
    fact_date = date(2026, 2, 20)
    transfer_date = date(2026, 2, 15)

    _seed_dimension(
        writer,
        employee_id,
        hired_at=HIRED,
        assignments=(
            AssignmentRow(
                tenant_id=TENANT_A,
                employee_id=employee_id,
                effective_from=HIRED,
                effective_to=None,
                department_id=department_a,
                position_id=position_id,
                location_id=location_id,
            ),
        ),
    )
    _overtime(
        writer,
        UUID("00000000-0000-4000-8000-0000000000c9"),
        employee_id,
        work_date=fact_date,
        hours="2.50",
        status="hr_verified",
        refs=(str(department_a), str(position_id), str(location_id)),
    )

    async def hours_by_department(
        department_id: UUID | None = None,
    ) -> dict[str, str]:
        params: dict[str, str] = {
            "from": fact_date.isoformat(),
            "to": fact_date.isoformat(),
            "group_by": "department",
            "interval": "total",
        }
        if department_id is not None:
            params["department_id"] = str(department_id)
        async with client_factory() as client:
            response = await client.get("/api/v1/reports/overtime", params=params)
            assert response.status_code == 200, response.text
            return {
                row["groups"][0]["key"]: row["verified_hours"]
                for row in response.json()["rows"]
            }

    assert await hours_by_department() == {str(department_a): "2.50"}

    # The transfer is backdated to before the already-projected fact: A closes
    # and B opens at `transfer_date`, with no fact rewrite.
    _seed_dimension(
        writer,
        employee_id,
        hired_at=HIRED,
        assignments=(
            AssignmentRow(
                tenant_id=TENANT_A,
                employee_id=employee_id,
                effective_from=HIRED,
                effective_to=transfer_date,
                department_id=department_a,
                position_id=position_id,
                location_id=location_id,
            ),
            AssignmentRow(
                tenant_id=TENANT_A,
                employee_id=employee_id,
                effective_from=transfer_date,
                effective_to=None,
                department_id=department_b,
                position_id=position_id,
                location_id=location_id,
            ),
        ),
    )

    assert await hours_by_department() == {str(department_b): "2.50"}
    assert await hours_by_department(department_a) == {}
    assert await hours_by_department(department_b) == {str(department_b): "2.50"}


@pytest.mark.asyncio
async def test_fact_without_dimension_history_groups_as_unknown(
    client_factory: ClientFactory, clickhouse: Client
) -> None:
    """A fact whose employee has no captured intervals is never dropped.

    The stream can start after the employee existed, so neither `dim_employee`
    nor `dim_assignment` has a row; the fact still reports under `unknown`
    dimensions (rule 6 / analytics-reports rule 3).
    """
    writer = ClickhouseCdcWriter(clickhouse)
    employee_id = UUID("00000000-0000-4000-8000-0000000000f9")
    _overtime(
        writer,
        UUID("00000000-0000-4000-8000-0000000000ca"),
        employee_id,
        work_date=date(2026, 2, 17),
        hours="2.50",
        status="hr_verified",
        refs=(
            "00000000-0000-4000-8000-0000000000d3",
            "00000000-0000-4000-8000-0000000000e3",
            "00000000-0000-4000-8000-0000000000f0",
        ),
    )

    async with client_factory() as client:
        response = await client.get(
            "/api/v1/reports/overtime",
            params={
                "from": "2026-02-17",
                "to": "2026-02-17",
                "group_by": "department,seniority",
                "interval": "total",
            },
        )
        assert response.status_code == 200, response.text
        [row] = response.json()["rows"]
        assert [group["key"] for group in row["groups"]] == ["unknown", "unknown"]
        assert row["verified_hours"] == "2.50"
        assert row["verified_request_count"] == 1
        # No dimension history means no denominator employee, never a dropped
        # fact row.
        assert row["active_employee_count"] == 0


@pytest.mark.asyncio
async def test_seniority_sql_matches_python_attribution_at_boundaries(
    client_factory: ClientFactory, clickhouse: Client
) -> None:
    """The ClickHouse band expression and `attribution.seniority_band` agree.

    Covers the leap-day clamp (2020-02-29 + 5y = 2025-02-28) and both sides of
    the 1y/3y/5y anniversaries, plus the no-tenure `unknown` band. Expected
    keys are computed with the Python helper, so a threshold drift on either
    side fails here.
    """
    writer = ClickhouseCdcWriter(clickhouse)
    department_id = UUID("00000000-0000-4000-8000-0000000000d6")
    position_id = UUID("00000000-0000-4000-8000-0000000000e6")
    location_id = UUID("00000000-0000-4000-8000-0000000000f6")

    cases = (
        (UUID(int=0xA101), date(2020, 2, 29), date(2025, 2, 27)),
        (UUID(int=0xA102), date(2020, 2, 29), date(2025, 2, 28)),
        (UUID(int=0xA103), date(2022, 7, 1), date(2025, 6, 30)),
        (UUID(int=0xA104), date(2022, 7, 1), date(2025, 7, 1)),
        (UUID(int=0xA105), date(2025, 7, 1), date(2026, 6, 30)),
        (UUID(int=0xA106), date(2025, 7, 1), date(2026, 7, 1)),
        (UUID(int=0xA107), None, date(2026, 7, 1)),
    )
    expected_by_date: dict[date, set[str]] = {}
    for index, (employee_id, hired_at, fact_date) in enumerate(cases):
        expected_by_date.setdefault(fact_date, set()).add(
            seniority_band(hired_at, fact_date)
        )
        # Attribution is query-time from the dimension tables (CDC D3), so the
        # employee needs the interval the retired fold writer would have
        # produced.
        first_start = hired_at or fact_date
        _seed_dimension(
            writer,
            employee_id,
            hired_at=hired_at,
            assignments=(
                AssignmentRow(
                    tenant_id=TENANT_A,
                    employee_id=employee_id,
                    effective_from=first_start,
                    effective_to=None,
                    department_id=department_id,
                    position_id=position_id,
                    location_id=location_id,
                ),
            ),
        )
        _overtime(
            writer,
            UUID(int=0xC200 + index),
            employee_id,
            work_date=fact_date,
            hours="1.00",
            status="hr_verified",
            refs=(str(department_id), str(position_id), str(location_id)),
            hired_at=hired_at,
        )

    async with client_factory() as client:
        for fact_date, expected in expected_by_date.items():
            response = await client.get(
                "/api/v1/reports/overtime",
                params={
                    "from": fact_date.isoformat(),
                    "to": fact_date.isoformat(),
                    "group_by": "seniority",
                    "interval": "total",
                },
            )
            assert response.status_code == 200, response.text
            keys = {row["groups"][0]["key"] for row in response.json()["rows"]}
            assert keys == expected, (fact_date, keys, expected)


@pytest.mark.asyncio
async def test_replacing_merge_tree_rows_are_deduplicated(
    client_factory: ClientFactory, clickhouse: Client
) -> None:
    """The read side must use `FINAL`: a corrected row counts once, newest wins."""
    writer = ClickhouseCdcWriter(clickhouse)
    employee_id = UUID("00000000-0000-4000-8000-0000000000f3")
    department_id = UUID("00000000-0000-4000-8000-0000000000d7")
    position_id = UUID("00000000-0000-4000-8000-0000000000e7")
    location_id = UUID("00000000-0000-4000-8000-0000000000f7")
    _seed_dimension(
        writer,
        employee_id,
        hired_at=HIRED,
        assignments=(
            AssignmentRow(
                tenant_id=TENANT_A,
                employee_id=employee_id,
                effective_from=HIRED,
                effective_to=None,
                department_id=department_id,
                position_id=position_id,
                location_id=location_id,
            ),
        ),
    )
    overtime_id = UUID("00000000-0000-4000-8000-0000000000c8")
    refs = (str(department_id), str(position_id), str(location_id))
    _overtime(
        writer,
        overtime_id,
        employee_id,
        work_date=date(2026, 2, 1),
        hours="2.50",
        status="hr_verified",
        refs=refs,
    )
    # The monotonically increasing `updated_at` makes this the winning version.
    _overtime(
        writer,
        overtime_id,
        employee_id,
        work_date=date(2026, 2, 1),
        hours="3.00",
        status="hr_verified",
        refs=refs,
    )

    async with client_factory() as client:
        response = await client.get(
            "/api/v1/reports/overtime",
            params={
                "from": "2026-02-01",
                "to": "2026-02-28",
                "group_by": "department",
                "interval": "total",
            },
        )
        assert response.status_code == 200, response.text
        [row] = response.json()["rows"]
        assert row["verified_hours"] == "3.00"
        assert row["verified_request_count"] == 1
        assert row["verified_day_count"] == 1
        assert row["hours_per_employee"] == "3.00"


@pytest.mark.asyncio
async def test_reports_write_no_audit_entries_or_events(
    client_factory: ClientFactory,
    clickhouse: Client,
    seeded: Seed,
    session: AsyncSession,
) -> None:
    async def written() -> tuple[int, int]:
        audit = await session.scalar(select(func.count()).select_from(AuditEntry))
        outbox = await session.scalar(select(func.count()).select_from(EventOutboxRow))
        return int(audit or 0), int(outbox or 0)

    before = await written()
    async with client_factory() as client:
        for path in (
            "/api/v1/reports/overtime",
            "/api/v1/reports/attendance",
            "/api/v1/reports/leave",
            "/api/v1/reports/headcount",
        ):
            response = await client.get(path, params=_get_params())
            assert response.status_code == 200, response.text

    # A report is a read: no audit entry, no event (spec rule 11).
    assert await written() == before


@pytest.mark.asyncio
async def test_report_response_carries_no_pii(
    client_factory: ClientFactory, clickhouse: Client, seeded: Seed
) -> None:
    async with client_factory() as client:
        responses = [
            await client.get("/api/v1/reports/overtime", params=_get_params()),
            await client.get("/api/v1/reports/attendance", params=_get_params()),
            await client.get("/api/v1/reports/leave", params=_get_params()),
            await client.get(
                "/api/v1/reports/headcount",
                params={
                    "from": RANGE_FROM.isoformat(),
                    "to": RANGE_TO.isoformat(),
                },
            ),
        ]

    forbidden_values = (
        "Mia",
        "Manager",
        "Mona",
        "Second",
        "Riley",
        "Report",
        "Owen",
        "Other",
        "Lee",
        "Leaver",
        "Tara",
        "Transfer",
        "example.test",
        str(seeded.manager_id),
        str(seeded.manager2_id),
        str(seeded.report_id),
        str(seeded.other_id),
        str(seeded.leaver_id),
        str(seeded.transferee_id),
    )
    for response in responses:
        assert response.status_code == 200, response.text
        assert "employee_id" not in response.text
        for value in forbidden_values:
            assert value not in response.text, (value, response.text)
