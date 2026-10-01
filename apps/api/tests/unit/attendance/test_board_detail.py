"""Unit tests for `AttendanceService.board_detail` — the shared projection.

Every call passes an explicit `now`; no test reads the wall clock. The rows
mirror `GET /attendance/board` wire semantics (ADR 0009): one status plus the
orthogonal `late` flag, `minutes_late` recomputed at read time.
"""

from datetime import UTC, datetime, time

from app.domains.attendance.schemas import WorkMode
from app.domains.attendance.service import AttendanceService
from app.shared.identity import Role
from tests.unit.attendance.conftest import (
    ACTOR,
    NOW,
    OTHER,
    REPORT,
    TODAY,
    identity,
    seed_employee,
    seed_record,
    seed_shift,
)
from tests.unit.attendance.fakes import FakeAttendanceRepo, FakeShiftRepo

LATE_30 = datetime(2026, 9, 24, 2, 40, tzinfo=UTC)  # 30 minutes past start+grace


async def test_board_detail_projects_rows_counts_and_activity(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
) -> None:
    seed_employee(attendance_repo)  # ACTOR
    seed_employee(attendance_repo, employee_id=REPORT)
    seed_employee(attendance_repo, employee_id=OTHER)
    shift = await seed_shift(shift_repo, start_time=time(9, 0), grace_minutes=10)

    actor_record = await seed_record(
        attendance_repo,
        employee_id=ACTOR,
        checked_in_at=LATE_30,
        late=True,
        shift_id=shift.id,
    )
    report_record = await seed_record(
        attendance_repo, employee_id=REPORT, work_mode=WorkMode.REMOTE
    )
    other_record = await seed_record(
        attendance_repo,
        employee_id=OTHER,
        checked_in_at=datetime(2026, 9, 24, 1, 0, tzinfo=UTC),
        checked_out_at=datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        duration_minutes=540,
    )

    detail = await service.board_detail(identity(), TODAY, NOW)
    rows = {row.employee_id: row for row in detail.rows}

    assert detail.date == TODAY
    assert set(rows) == {ACTOR, REPORT, OTHER}
    assert detail.counts.model_dump() == {
        "present": 1,
        "remote": 1,
        "late": 1,
        "on_leave": 0,
        "absent": 0,
        "checked_out": 1,
    }

    actor_row = rows[ACTOR]
    assert actor_row.status == "present"
    assert actor_row.late is True
    assert actor_row.minutes_late == 30  # recomputed from the record's shift
    assert actor_row.since == LATE_30
    assert actor_row.attendance_id == actor_record.id

    report_row = rows[REPORT]
    assert report_row.status == "remote"
    assert report_row.late is False
    assert report_row.minutes_late is None
    assert report_row.since == NOW
    assert report_row.attendance_id == report_record.id

    other_row = rows[OTHER]
    assert other_row.status == "checked_out"
    assert other_row.since == other_record.checked_out_at
    assert other_row.attendance_id == other_record.id

    occurred = [entry.occurred_at for entry in detail.activity]
    assert occurred == sorted(occurred, reverse=True)


async def test_board_detail_absent_rows_follow_the_injected_clock(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
) -> None:
    seed_employee(attendance_repo)
    await seed_shift(shift_repo, start_time=time(9, 0), grace_minutes=10)

    before = await service.board_detail(
        identity(), TODAY, datetime(2026, 9, 24, 2, 9, tzinfo=UTC)
    )
    assert before.rows == []
    assert before.counts.absent == 0

    after = await service.board_detail(
        identity(), TODAY, datetime(2026, 9, 24, 2, 11, tzinfo=UTC)
    )
    assert after.counts.absent == 1
    assert len(after.rows) == 1  # absent == len(absent rows) by construction
    row = after.rows[0]
    assert row.employee_id == ACTOR
    assert row.status == "absent"
    assert row.late is False
    assert row.minutes_late is None
    assert row.since is None
    assert row.attendance_id is None


async def test_board_detail_rows_follow_the_manager_scope(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
) -> None:
    seed_employee(attendance_repo, employee_id=ACTOR)
    seed_employee(attendance_repo, employee_id=REPORT, manager_id=ACTOR)
    seed_employee(attendance_repo, employee_id=OTHER)  # not the manager's report
    await seed_record(attendance_repo, employee_id=ACTOR)
    await seed_record(attendance_repo, employee_id=REPORT)
    await seed_record(attendance_repo, employee_id=OTHER)

    detail = await service.board_detail(identity(role=Role.MANAGER), TODAY, NOW)

    assert {row.employee_id for row in detail.rows} == {ACTOR, REPORT}
    assert detail.counts.present == 2


async def test_board_detail_counts_follow_the_current_record_per_employee(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
) -> None:
    """A re-check-in replaces its earlier record's contribution (recount rule).

    Counts must equal a recount of the presence rows, or the live wall's first
    delta would contradict its own snapshot and the board (realtime spec
    finding 2).
    """
    seed_employee(attendance_repo)  # ACTOR
    await seed_record(
        attendance_repo,
        employee_id=ACTOR,
        checked_in_at=datetime(2026, 9, 24, 2, 40, tzinfo=UTC),
        checked_out_at=datetime(2026, 9, 24, 6, 0, tzinfo=UTC),
        late=True,
        duration_minutes=200,
    )
    current = await seed_record(
        attendance_repo,
        employee_id=ACTOR,
        checked_in_at=datetime(2026, 9, 24, 8, 0, tzinfo=UTC),
    )

    detail = await service.board_detail(identity(), TODAY, NOW)

    row = {row.employee_id: row for row in detail.rows}[ACTOR]
    assert row.attendance_id == current.id  # newest record wins (pinned)
    assert row.late is False
    # One contribution per employee: the superseded late record never counts.
    assert detail.counts.model_dump() == {
        "present": 1,
        "remote": 0,
        "late": 0,
        "on_leave": 0,
        "absent": 0,
        "checked_out": 0,
    }
    # Both transitions still reach the activity feed — a re-check-in is a fact.
    actions = [entry.action.value for entry in detail.activity]
    assert actions.count("checked_in") == 2
    assert actions.count("checked_out") == 1
