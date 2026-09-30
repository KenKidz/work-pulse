"""Unit tests for `AttendanceService.check_in` — rules pinned in the spec.

Every call passes an explicit `now`; no test reads the wall clock.
"""

from datetime import UTC, date, datetime, time
from uuid import UUID

import pytest

from app.domains.attendance.exceptions import (
    AttendanceAlreadyCheckedInError,
    EmployeeNotActiveError,
    InvalidWorkModeError,
)
from app.domains.attendance.schemas import AttendanceRecord, WorkMode
from app.domains.attendance.service import AttendanceService
from app.domains.employees.exceptions import EmployeeNotFoundError
from app.domains.employees.schemas import EmployeeStatus
from tests.unit.attendance.conftest import (
    LOCATION,
    REPORT,
    TODAY,
    check_in,
    identity,
    seed_employee,
    seed_record,
    seed_shift,
)
from tests.unit.attendance.fakes import FakeAttendanceRepo, FakeShiftRepo
from tests.unit.organizations.fakes import RecordingAuditWriter

EARLY = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)  # 08:00 in Asia/Ho_Chi_Minh
BOUNDARY = datetime(2026, 9, 24, 2, 10, tzinfo=UTC)  # exactly start + grace
LATE_30 = datetime(2026, 9, 24, 2, 40, tzinfo=UTC)  # 30 minutes past the boundary


async def test_check_in_records_open_record_and_audit(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
    audit: RecordingAuditWriter,
) -> None:
    seed_employee(attendance_repo)
    shift = await seed_shift(shift_repo, start_time=time(9, 0), grace_minutes=10)

    mutation = await check_in(service, now=EARLY, note="commute")

    record = mutation.record
    assert record.workday == TODAY
    assert record.checked_in_at == EARLY
    assert record.checked_out_at is None
    assert record.work_mode == WorkMode.ONSITE
    assert record.late is False
    assert record.shift_id == shift.id
    assert record.check_in_note == "commute"
    assert record.duration_minutes is None

    assert [event["event_type"] for event in mutation.events] == [
        "attendance.checked_in"
    ]
    assert mutation.events[0]["data"]["late"] is False
    assert mutation.events[0]["data"]["work_mode"] == "onsite"

    assert [entry.action for entry in audit.entries] == ["attendance.checked_in"]
    entry = audit.entries[0]
    assert entry.entity_type == "attendance"
    assert entry.entity_id == record.id
    assert entry.occurred_at == EARLY
    assert entry.changes is not None
    assert entry.changes["late"] == {"old": None, "new": False}
    assert entry.changes["work_mode"] == {"old": None, "new": "onsite"}
    # Notes are PII: neither the field nor its value reaches audit `changes`.
    assert "check_in_note" not in entry.changes
    assert "commute" not in str(entry.changes)


async def test_check_in_at_start_plus_grace_is_not_late(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
) -> None:
    seed_employee(attendance_repo)
    await seed_shift(shift_repo, start_time=time(9, 0), grace_minutes=10)

    mutation = await check_in(service, now=BOUNDARY)

    assert mutation.record.late is False
    assert [event["event_type"] for event in mutation.events] == [
        "attendance.checked_in"
    ]


async def test_check_in_past_boundary_is_late_with_minutes_and_late_event(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
) -> None:
    seed_employee(attendance_repo)
    shift = await seed_shift(shift_repo, start_time=time(9, 0), grace_minutes=10)

    mutation = await check_in(service, now=LATE_30)

    assert mutation.record.late is True
    assert [event["event_type"] for event in mutation.events] == [
        "attendance.checked_in",
        "attendance.late",
    ]
    assert mutation.events[0]["data"]["late"] is True
    late_payload = mutation.events[1]["data"]
    assert late_payload["attendance_id"] == str(mutation.record.id)
    assert late_payload["employee_id"] == str(mutation.record.employee_id)
    assert late_payload["workday"] == TODAY.isoformat()
    assert late_payload["shift_id"] == str(shift.id)
    assert late_payload["minutes_late"] == 30


async def test_check_in_without_shift_is_never_late(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo)

    mutation = await check_in(service, now=LATE_30)

    assert mutation.record.late is False
    assert mutation.record.shift_id is None
    assert [event["event_type"] for event in mutation.events] == [
        "attendance.checked_in"
    ]


async def test_check_in_resolves_location_shift_before_tenant_default(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
) -> None:
    seed_employee(attendance_repo)  # located at LOCATION
    # The default would make 08:00 late; the location's shift must win.
    await seed_shift(shift_repo, name="Default", start_time=time(0, 0), grace_minutes=0)
    location_shift = await seed_shift(
        shift_repo,
        name="Site",
        start_time=time(9, 0),
        grace_minutes=10,
        location_id=LOCATION,
    )

    mutation = await check_in(service, now=EARLY)

    assert mutation.record.shift_id == location_shift.id
    assert mutation.record.late is False


async def test_workday_uses_location_timezone_not_utc(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo)
    seed_employee(attendance_repo, employee_id=REPORT)

    # 23:58 in Asia/Ho_Chi_Minh on 09-24 …
    late_night = datetime(2026, 9, 24, 16, 58, tzinfo=UTC)
    first = await check_in(service, now=late_night)
    assert first.record.workday == date(2026, 9, 24)

    # … and 00:10 on 09-25 in the location tz while UTC is still on 09-24.
    after_midnight = datetime(2026, 9, 24, 17, 10, tzinfo=UTC)
    second = await check_in(
        service, caller=identity(employee_id=REPORT), now=after_midnight
    )
    assert second.record.workday == date(2026, 9, 25)


async def test_second_check_in_while_open_is_rejected(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo)
    await check_in(service, now=EARLY)

    with pytest.raises(AttendanceAlreadyCheckedInError) as exc:
        await check_in(service, now=LATE_30)

    assert exc.value.code == "attendance_already_checked_in"
    assert exc.value.status_code == 409


async def test_terminated_employee_cannot_check_in(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo, status=EmployeeStatus.TERMINATED)

    with pytest.raises(EmployeeNotActiveError) as exc:
        await check_in(service)

    assert exc.value.code == "employee_not_active"
    assert exc.value.status_code == 409


class _RaceLostRepo(FakeAttendanceRepo):
    """The pre-check missed an open record — the insert backstop must fire."""

    async def open_for_employee(
        self, tenant_id: UUID, employee_id: UUID
    ) -> AttendanceRecord | None:
        return None

    async def add(
        self,
        tenant_id: UUID,
        record_id: UUID,
        *,
        employee_id: UUID,
        workday: date,
        checked_in_at: datetime,
        work_mode: WorkMode,
        late: bool,
        shift_id: UUID | None,
        check_in_note: str | None,
    ) -> AttendanceRecord:
        # What the Postgres adapter raises after mapping the partial unique
        # index violation.
        raise AttendanceAlreadyCheckedInError()


async def test_insert_backstop_still_rejects_the_second_check_in(
    shift_repo: FakeShiftRepo, audit: RecordingAuditWriter
) -> None:
    """Concurrent two-tab check-in: one record, one `409` (T1, spec Risk 5)."""
    raced = FakeAttendanceRepo()
    seed_employee(raced)
    await seed_record(raced)  # the other tab's insert landed first
    repo = _RaceLostRepo()
    repo.employees = raced.employees
    repo.records = raced.records
    service = AttendanceService(repo=repo, shifts=shift_repo, audit=audit)

    with pytest.raises(AttendanceAlreadyCheckedInError):
        await check_in(service)


async def test_invalid_work_mode_is_rejected(
    service: AttendanceService, attendance_repo: FakeAttendanceRepo
) -> None:
    seed_employee(attendance_repo)

    with pytest.raises(InvalidWorkModeError) as exc:
        await check_in(service, work_mode="hybrid")

    assert exc.value.code == "invalid_work_mode"
    assert exc.value.status_code == 400


async def test_actor_without_employee_record_is_not_found(
    service: AttendanceService,
) -> None:
    with pytest.raises(EmployeeNotFoundError) as exc:
        await check_in(service)

    assert exc.value.code == "employee_not_found"
    assert exc.value.status_code == 404


async def test_check_in_without_employee_link_is_not_found(
    service: AttendanceService,
) -> None:
    """A user with no employee link fails closed (404, never a server error)."""
    with pytest.raises(EmployeeNotFoundError) as exc:
        await check_in(service, caller=identity(employee_id=None))

    assert exc.value.code == "employee_not_found"
    assert exc.value.status_code == 404
