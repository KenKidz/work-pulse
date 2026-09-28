"""Unit tests for `AttendanceService.check_out` — pinned rules and failures."""

from datetime import UTC, date, datetime

import pytest
from uuid_utils.compat import uuid7

from app.domains.attendance.exceptions import (
    AttendanceNotOpenError,
    AttendanceRecordNotFoundError,
    EmployeeNotActiveError,
)
from app.domains.attendance.schemas import CheckOutRequest
from app.domains.attendance.service import AttendanceService
from app.domains.employees.schemas import EmployeeStatus
from tests.unit.attendance.conftest import (
    NOW,
    REPORT,
    TODAY,
    check_in,
    identity,
    seed_employee,
    seed_record,
)
from tests.unit.attendance.fakes import FakeAttendanceRepo
from tests.unit.organizations.fakes import RecordingAuditWriter

EARLY = datetime(2026, 9, 24, 2, 0, tzinfo=UTC)  # 09:00 in Asia/Ho_Chi_Minh
EVENING = datetime(2026, 9, 24, 10, 30, tzinfo=UTC)  # 17:30 — 510 minutes later


async def test_check_out_closes_record_and_reports_duration(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    audit: RecordingAuditWriter,
) -> None:
    seed_employee(attendance_repo)
    opened = await check_in(service, now=EARLY)

    mutation = await service.check_out(
        identity(),
        opened.record.id,
        CheckOutRequest(check_out_note="done"),
        EVENING,
    )

    record = mutation.record
    assert record.checked_out_at == EVENING
    assert record.duration_minutes == 510
    assert record.check_out_note == "done"
    assert record.workday == TODAY

    assert [event["event_type"] for event in mutation.events] == [
        "attendance.checked_out"
    ]
    payload = mutation.events[0]["data"]
    assert payload["attendance_id"] == str(record.id)
    assert payload["duration_minutes"] == 510

    entry = audit.entries[-1]
    assert entry.action == "attendance.checked_out"
    assert entry.entity_type == "attendance"
    assert entry.entity_id == record.id
    assert entry.occurred_at == EVENING
    assert entry.changes == {
        "checked_out_at": {"old": None, "new": EVENING.isoformat()},
        "duration_minutes": {"old": None, "new": 510},
    }
    assert "done" not in str(entry.changes)  # notes are PII — never audited


async def test_check_out_across_midnight_keeps_the_check_in_workday(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo)
    # Check-in at 23:58 on 09-24 (local), check-out at 00:10 on 09-25 (local).
    opened = await check_in(service, now=datetime(2026, 9, 24, 16, 58, tzinfo=UTC))

    mutation = await service.check_out(
        identity(),
        opened.record.id,
        CheckOutRequest(),
        datetime(2026, 9, 24, 17, 10, tzinfo=UTC),
    )

    assert mutation.record.workday == date(2026, 9, 24)  # never recomputed
    assert mutation.record.duration_minutes == 12


async def test_check_out_without_an_open_record_is_conflict(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo)
    closed = await seed_record(
        attendance_repo,
        checked_in_at=EARLY,
        checked_out_at=EVENING,
        duration_minutes=510,
    )

    with pytest.raises(AttendanceNotOpenError) as exc:
        await service.check_out(identity(), closed.id, CheckOutRequest(), NOW)

    assert exc.value.code == "attendance_not_open"
    assert exc.value.status_code == 409


async def test_check_out_unknown_record_is_not_found(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo)

    with pytest.raises(AttendanceRecordNotFoundError) as exc:
        await service.check_out(identity(), uuid7(), CheckOutRequest(), NOW)

    assert exc.value.code == "attendance_record_not_found"
    assert exc.value.status_code == 404


async def test_check_out_of_another_employees_record_is_not_found(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo)  # the actor
    seed_employee(attendance_repo, employee_id=REPORT)
    record = await seed_record(attendance_repo, employee_id=REPORT)

    with pytest.raises(AttendanceRecordNotFoundError):
        await service.check_out(identity(), record.id, CheckOutRequest(), NOW)


async def test_terminated_actor_cannot_check_out(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo, status=EmployeeStatus.TERMINATED)
    record = await seed_record(attendance_repo)

    with pytest.raises(EmployeeNotActiveError) as exc:
        await service.check_out(identity(), record.id, CheckOutRequest(), NOW)

    assert exc.value.code == "employee_not_active"
