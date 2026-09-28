"""Unit tests for `AttendanceService.board` — count semantics and the feed."""

from datetime import UTC, date, datetime, time, timedelta

from app.domains.attendance.schemas import BoardAction, WorkMode
from app.domains.attendance.service import AttendanceService
from app.domains.employees.schemas import EmployeeStatus
from app.shared.identity import Role
from tests.unit.attendance.conftest import (
    ACTOR,
    LOCATION,
    LOCATION_B,
    NOW,
    OTHER,
    REPORT,
    TENANT_B,
    TODAY,
    identity,
    seed_employee,
    seed_record,
    seed_shift,
)
from tests.unit.attendance.fakes import FakeAttendanceRepo, FakeShiftRepo

BOUNDARY = datetime(2026, 9, 24, 2, 10, tzinfo=UTC)  # 09:10 in Asia/Ho_Chi_Minh


async def test_counts_are_independent_not_a_partition(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
) -> None:
    seed_employee(attendance_repo)  # ACTOR
    seed_employee(attendance_repo, employee_id=REPORT)
    seed_employee(attendance_repo, employee_id=OTHER)
    await seed_shift(shift_repo)

    await seed_record(attendance_repo, employee_id=ACTOR, late=True)  # open onsite
    await seed_record(attendance_repo, employee_id=REPORT, work_mode=WorkMode.REMOTE)
    await seed_record(
        attendance_repo,
        employee_id=OTHER,
        checked_in_at=BOUNDARY,
        checked_out_at=datetime(2026, 9, 24, 10, 30, tzinfo=UTC),
        duration_minutes=500,
    )

    board = await service.board(identity(), TODAY, NOW)

    assert board.date == TODAY
    # ACTOR is present AND late at the same time — counts overlap by design.
    assert board.counts.present == 1
    assert board.counts.remote == 1
    assert board.counts.late == 1
    assert board.counts.checked_out == 1
    assert board.counts.on_leave == 0  # always 0 until Leave lands (Phase 3)
    assert board.counts.absent == 0  # everyone has a record for the date


async def test_absent_only_after_start_plus_grace_and_only_with_zero_records(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
) -> None:
    seed_employee(attendance_repo)
    await seed_shift(shift_repo, start_time=time(9, 0), grace_minutes=10)

    before = await service.board(
        identity(), TODAY, datetime(2026, 9, 24, 2, 9, tzinfo=UTC)
    )
    assert before.counts.absent == 0

    exactly = await service.board(identity(), TODAY, BOUNDARY)
    assert exactly.counts.absent == 0  # boundary is exclusive

    after = await service.board(
        identity(), TODAY, datetime(2026, 9, 24, 2, 11, tzinfo=UTC)
    )
    assert after.counts.absent == 1

    # Any record for the date (even a closed one) removes the employee from
    # `absent` — a late check-in moves them into present/remote + late instead.
    await seed_record(
        attendance_repo,
        checked_in_at=datetime(2026, 9, 24, 2, 30, tzinfo=UTC),
        checked_out_at=datetime(2026, 9, 24, 10, 30, tzinfo=UTC),
        duration_minutes=480,
    )
    recorded = await service.board(
        identity(), TODAY, datetime(2026, 9, 24, 2, 11, tzinfo=UTC)
    )
    assert recorded.counts.absent == 0
    assert recorded.counts.checked_out == 1


async def test_absent_requires_an_expected_shift_and_active_status(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
) -> None:
    seed_employee(attendance_repo, employee_id=ACTOR, location_id=LOCATION)
    seed_employee(attendance_repo, employee_id=REPORT, location_id=LOCATION_B)
    seed_employee(
        attendance_repo,
        employee_id=OTHER,
        status=EmployeeStatus.TERMINATED,
        location_id=LOCATION,
    )
    # Only LOCATION has a shift; there is no tenant default.
    await seed_shift(shift_repo, location_id=LOCATION)

    board = await service.board(identity(), TODAY, NOW)

    assert board.counts.absent == 1  # ACTOR only — no shift for REPORT, inactive OTHER


async def test_activity_is_newest_first_and_capped_at_20(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
) -> None:
    seed_employee(attendance_repo)
    await seed_shift(shift_repo)
    workday = date(2026, 9, 23)
    base = datetime(2026, 9, 23, 1, 0, tzinfo=UTC)
    for index in range(11):  # 11 closed records → 22 activity entries
        start = base + timedelta(minutes=index)
        await seed_record(
            attendance_repo,
            workday=workday,
            checked_in_at=start,
            checked_out_at=start + timedelta(hours=8),
            duration_minutes=480,
        )

    board = await service.board(identity(), workday, NOW)

    assert len(board.activity) == 20
    # The newest occurrence is the last record's check-out.
    assert board.activity[0].occurred_at == base + timedelta(minutes=10, hours=8)
    assert board.activity[0].action == BoardAction.CHECKED_OUT
    occurred = [entry.occurred_at for entry in board.activity]
    assert occurred == sorted(occurred, reverse=True)


async def test_board_for_manager_scopes_to_self_and_direct_reports(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
) -> None:
    seed_employee(attendance_repo, employee_id=ACTOR)
    seed_employee(attendance_repo, employee_id=REPORT, manager_id=ACTOR)
    seed_employee(attendance_repo, employee_id=OTHER)  # not the manager's report
    await seed_shift(shift_repo)
    await seed_record(attendance_repo, employee_id=ACTOR)
    await seed_record(attendance_repo, employee_id=REPORT, work_mode=WorkMode.REMOTE)
    await seed_record(attendance_repo, employee_id=OTHER)

    board = await service.board(identity(role=Role.MANAGER), TODAY, NOW)

    assert board.counts.present == 1
    assert board.counts.remote == 1
    assert {entry.employee_id for entry in board.activity} == {ACTOR, REPORT}


async def test_board_is_tenant_scoped(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
) -> None:
    seed_employee(attendance_repo)  # tenant A employee with a record
    await seed_record(attendance_repo)

    board = await service.board(identity(tenant_id=TENANT_B), TODAY, NOW)

    assert board.counts.present == 0
    assert board.activity == []
