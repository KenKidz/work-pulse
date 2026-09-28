"""Unit tests for `AttendanceService.list_records` — filters and visibility."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.domains.attendance.service import AttendanceService
from app.domains.employees.exceptions import EmployeeNotFoundError
from app.shared.identity import Role
from app.shared.pagination import PageParams
from tests.unit.attendance.conftest import (
    ACTOR,
    OTHER,
    REPORT,
    TODAY,
    identity,
    seed_employee,
    seed_record,
)
from tests.unit.attendance.fakes import FakeAttendanceRepo


async def test_employee_role_is_scoped_to_self_and_out_of_scope_filter_is_404(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo)  # ACTOR
    seed_employee(attendance_repo, employee_id=REPORT)
    await seed_record(attendance_repo, employee_id=REPORT)

    own = await service.list_records(identity(role=Role.EMPLOYEE), page=PageParams())
    assert own.items == []  # another employee's record is never visible

    with pytest.raises(EmployeeNotFoundError) as exc:
        await service.list_records(
            identity(role=Role.EMPLOYEE), employee_id=REPORT, page=PageParams()
        )
    assert exc.value.code == "employee_not_found"


async def test_manager_sees_self_and_direct_reports_only(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo, employee_id=ACTOR)
    seed_employee(attendance_repo, employee_id=REPORT, manager_id=ACTOR)
    seed_employee(attendance_repo, employee_id=OTHER)
    await seed_record(attendance_repo, employee_id=REPORT)
    await seed_record(attendance_repo, employee_id=OTHER)

    page = await service.list_records(identity(role=Role.MANAGER), page=PageParams())
    assert [item.employee_id for item in page.items] == [REPORT]

    with pytest.raises(EmployeeNotFoundError):
        await service.list_records(
            identity(role=Role.MANAGER), employee_id=OTHER, page=PageParams()
        )


async def test_hr_sees_all_and_filters_apply(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo)  # ACTOR
    seed_employee(attendance_repo, employee_id=REPORT)
    await seed_record(
        attendance_repo,
        workday=date(2026, 9, 23),
        checked_in_at=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
        late=True,
    )
    await seed_record(attendance_repo, employee_id=REPORT, workday=TODAY)

    everything = await service.list_records(identity(), page=PageParams())
    assert len(everything.items) == 2

    filtered = await service.list_records(
        identity(),
        from_date=TODAY,
        to_date=TODAY,
        late=False,
        page=PageParams(),
    )
    assert [item.employee_id for item in filtered.items] == [REPORT]


async def test_records_are_newest_first_and_paginated(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo)
    base = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)
    for index in range(3):
        start = base + timedelta(minutes=index)
        await seed_record(
            attendance_repo,
            checked_in_at=start,
            checked_out_at=start + timedelta(hours=8),
            duration_minutes=480,
        )

    first = await service.list_records(identity(), page=PageParams(page=1, page_size=2))
    assert first.page.total_items == 3
    assert first.page.total_pages == 2
    assert len(first.items) == 2
    assert first.items[0].checked_in_at > first.items[1].checked_in_at

    second = await service.list_records(
        identity(), page=PageParams(page=2, page_size=2)
    )
    assert len(second.items) == 1
