"""T4 — projection writer over the real throwaway ClickHouse (:8124).

Proves the ADR 0019 write contract end to end:

- complete-row upserts round-trip through `FINAL` reads and re-writing the
  same row converges (redelivery shape — no dedupe table needed);
- a transfer's bounded re-stamp corrects facts dated on or after
  `effective_from` and leaves earlier facts untouched (backdated transfer);
- a pre-extension `employee.created` projects `hired_at = None` and a
  pre-extension transfer carries the previous position/location forward.

The fixture drops and recreates the projection around every test, so this file
leaves the shared `clickhouse-test` store empty for the next suite.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from clickhouse_connect.driver.client import Client

from app.domains.attendance.events import (
    attendance_checked_in,
    attendance_checked_out,
)
from app.domains.employees.events import employee_created, employee_transferred
from app.domains.leave.events import leave_approved
from app.domains.overtime.events import overtime_verified
from app.domains.reports.folds import (
    fold_attendance,
    fold_employee_created,
    fold_employee_transferred,
    fold_leave,
    fold_overtime,
)
from app.domains.reports.schemas import (
    AttendanceFact,
    DimensionState,
    LeaveFact,
    OvertimeFact,
)
from app.infrastructure.clickhouse.client import (
    dispose_clickhouse,
    get_clickhouse_client,
)
from app.infrastructure.clickhouse.projection import ClickhouseProjectionWriter
from app.infrastructure.clickhouse.schema import (
    SCHEMA_TABLES,
    apply_clickhouse_schema,
)

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000001")
ACTOR = UUID("00000000-0000-4000-8000-000000000031")
DEPARTMENT_A = UUID("00000000-0000-4000-8000-0000000000d1")
DEPARTMENT_B = UUID("00000000-0000-4000-8000-0000000000d2")
POSITION_A = UUID("00000000-0000-4000-8000-0000000000e1")
POSITION_B = UUID("00000000-0000-4000-8000-0000000000e2")
LOCATION_A = UUID("00000000-0000-4000-8000-0000000000f1")
LOCATION_B = UUID("00000000-0000-4000-8000-0000000000f2")
OVERTIME_BEFORE = UUID("00000000-0000-4000-8000-0000000000c1")
OVERTIME_AFTER = UUID("00000000-0000-4000-8000-0000000000c2")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000c3")
LEAVE_REQUEST = UUID("00000000-0000-4000-8000-0000000000c4")

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
HIRED_AT = date(2026, 1, 5)


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


def _writer(client: Client) -> ClickhouseProjectionWriter:
    return ClickhouseProjectionWriter(client)


def _created(
    *, hired_at: date | None = HIRED_AT, occurred_at: datetime = NOW
) -> dict[str, Any]:
    envelope = employee_created(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=occurred_at,
        employee_id=EMPLOYEE,
        department_id=DEPARTMENT_A,
        position_id=POSITION_A,
        location_id=LOCATION_A,
        employment_type="full_time",
        hired_at=hired_at,
    )
    if hired_at is None:
        # Pre-extension envelope: the key is absent, not null.
        del envelope["data"]["hired_at"]
    return envelope


def _seed_employee(
    writer: ClickhouseProjectionWriter, *, hired_at: date | None = HIRED_AT
) -> DimensionState:
    writer.apply_dimension_fold(fold_employee_created(_created(hired_at=hired_at)))
    return writer.load_dimensions(TENANT, EMPLOYEE)


def _overtime(
    writer: ClickhouseProjectionWriter,
    overtime_id: UUID,
    work_date: date,
) -> OvertimeFact:
    state = writer.load_dimensions(TENANT, EMPLOYEE)
    fact = fold_overtime(
        "overtime.verified",
        overtime_verified(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            overtime_id=overtime_id,
            employee_id=EMPLOYEE,
            work_date=work_date,
            hours=Decimal("2.50"),
            verified_by=ACTOR,
        ),
        dimensions=state,
    )
    assert fact is not None
    return fact


def _attendance(writer: ClickhouseProjectionWriter, workday: date) -> AttendanceFact:
    state = writer.load_dimensions(TENANT, EMPLOYEE)
    opened = fold_attendance(
        "attendance.checked_in",
        attendance_checked_in(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            attendance_id=ATTENDANCE,
            employee_id=EMPLOYEE,
            workday=workday,
            work_mode="onsite",
            late=False,
            minutes_late=None,
        ),
        dimensions=state,
    )
    assert opened is not None
    closed = fold_attendance(
        "attendance.checked_out",
        attendance_checked_out(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            attendance_id=ATTENDANCE,
            employee_id=EMPLOYEE,
            workday=workday,
            duration_minutes=480,
            late=False,
            minutes_late=None,
        ),
        prior=opened,
        dimensions=state,
    )
    assert closed is not None
    return closed


def _leave(writer: ClickhouseProjectionWriter, start_date: date) -> LeaveFact:
    state = writer.load_dimensions(TENANT, EMPLOYEE)
    fact = fold_leave(
        "leave.approved",
        leave_approved(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            leave_request_id=LEAVE_REQUEST,
            employee_id=EMPLOYEE,
            leave_type_code="annual",
            start_date=start_date,
            end_date=start_date,
            days=1,
            decided_by=ACTOR,
        ),
        dimensions=state,
    )
    assert fact is not None
    return fact


def _transfer(
    writer: ClickhouseProjectionWriter,
    *,
    effective_from: date,
    to_position_id: UUID | None = POSITION_B,
    to_location_id: UUID | None = LOCATION_B,
) -> None:
    state = writer.load_dimensions(TENANT, EMPLOYEE)
    envelope = employee_transferred(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        employee_id=EMPLOYEE,
        from_department_id=DEPARTMENT_A,
        to_department_id=DEPARTMENT_B,
        effective_from=effective_from,
        to_position_id=to_position_id,
        to_location_id=to_location_id,
    )
    writer.apply_dimension_fold(
        fold_employee_transferred(
            envelope,
            prior=state.employee,
            assignments=state.assignments,
        )
    )


def test_writer_round_trips_rows_and_rewrites_converge(
    clickhouse: Client,
) -> None:
    writer = _writer(clickhouse)
    state = _seed_employee(writer)

    assert state.employee is not None
    assert state.employee.hired_at == HIRED_AT
    [assignment] = state.assignments
    assert assignment.department_id == DEPARTMENT_A

    overtime = _overtime(writer, OVERTIME_BEFORE, date(2026, 2, 10))
    writer.upsert_overtime(overtime)
    writer.upsert_overtime(overtime)
    assert writer.get_overtime(TENANT, OVERTIME_BEFORE) == overtime
    assert clickhouse.command("SELECT count() FROM fact_overtime FINAL") == 1

    attendance = _attendance(writer, date(2026, 2, 11))
    writer.upsert_attendance(attendance)
    writer.upsert_attendance(attendance)
    assert writer.get_attendance(TENANT, ATTENDANCE) == attendance
    assert clickhouse.command("SELECT count() FROM fact_attendance FINAL") == 1

    leave = _leave(writer, date(2026, 2, 12))
    writer.upsert_leave(leave)
    writer.upsert_leave(leave)
    assert writer.get_leave(TENANT, LEAVE_REQUEST) == leave
    assert clickhouse.command("SELECT count() FROM fact_leave FINAL") == 1


def test_transfer_restamp_corrects_facts_on_or_after_effective_from(
    clickhouse: Client,
) -> None:
    writer = _writer(clickhouse)
    _seed_employee(writer)

    before = _overtime(writer, OVERTIME_BEFORE, date(2026, 2, 10))
    after = _overtime(writer, OVERTIME_AFTER, date(2026, 4, 1))
    attendance = _attendance(writer, date(2026, 3, 15))
    leave = _leave(writer, date(2026, 4, 5))
    for fact in (before, after):
        writer.upsert_overtime(fact)
    writer.upsert_attendance(attendance)
    writer.upsert_leave(leave)

    # Facts are projected before the transfer is recorded: all carry A.
    assert before.department_id == DEPARTMENT_A
    assert after.department_id == DEPARTMENT_A

    _transfer(writer, effective_from=date(2026, 3, 1))

    corrected_before = writer.get_overtime(TENANT, OVERTIME_BEFORE)
    corrected_after = writer.get_overtime(TENANT, OVERTIME_AFTER)
    assert corrected_before is not None
    assert corrected_before.department_id == DEPARTMENT_A
    assert corrected_after is not None
    assert corrected_after.department_id == DEPARTMENT_B
    assert corrected_after.position_id == POSITION_B
    assert corrected_after.location_id == LOCATION_B

    corrected_attendance = writer.get_attendance(TENANT, ATTENDANCE)
    assert corrected_attendance is not None
    assert corrected_attendance.department_id == DEPARTMENT_B

    corrected_leave = writer.get_leave(TENANT, LEAVE_REQUEST)
    assert corrected_leave is not None
    assert corrected_leave.department_id == DEPARTMENT_B

    # The outcome is one deduped row per key, not one per write.
    assert clickhouse.command("SELECT count() FROM fact_overtime FINAL") == 2
    assert clickhouse.command("SELECT count() FROM fact_attendance FINAL") == 1
    assert clickhouse.command("SELECT count() FROM fact_leave FINAL") == 1

    # Redelivering the transfer re-applies the same fold: no drift.
    _transfer(writer, effective_from=date(2026, 3, 1))
    assert writer.get_overtime(TENANT, OVERTIME_BEFORE) == corrected_before
    assert writer.get_overtime(TENANT, OVERTIME_AFTER) == corrected_after
    assert writer.get_attendance(TENANT, ATTENDANCE) == corrected_attendance
    assert writer.get_leave(TENANT, LEAVE_REQUEST) == corrected_leave


def test_pre_extension_create_and_transfer_round_trip_nulls(
    clickhouse: Client,
) -> None:
    writer = _writer(clickhouse)
    state = _seed_employee(writer, hired_at=None)

    assert state.employee is not None
    assert state.employee.hired_at is None
    [assignment] = state.assignments
    assert assignment.effective_from == NOW.date()

    overtime = _overtime(writer, OVERTIME_BEFORE, date(2026, 2, 10))
    assert overtime.hired_at is None
    writer.upsert_overtime(overtime)
    assert writer.get_overtime(TENANT, OVERTIME_BEFORE) == overtime

    # Pre-extension transfer dated before the first-seen interval: carry
    # position/location from the earliest known interval.
    _transfer(
        writer,
        effective_from=date(2026, 3, 1),
        to_position_id=None,
        to_location_id=None,
    )

    updated = writer.load_dimensions(TENANT, EMPLOYEE)
    opened = [a for a in updated.assignments if a.effective_from == date(2026, 3, 1)]
    assert len(opened) == 1
    assert opened[0].department_id == DEPARTMENT_B
    assert opened[0].position_id == POSITION_A
    assert opened[0].location_id == LOCATION_A
