"""Shared fixtures: attendance services wired to in-memory fakes (no DB).

`NOW` is the injected clock every test passes explicitly — the services never
read the wall clock (`.ai/testing.md`: no wall-time assertions).
"""

from datetime import UTC, date, datetime, time
from uuid import UUID

import pytest
from uuid_utils.compat import uuid7

from app.domains.attendance.repository import EmployeeContext
from app.domains.attendance.schemas import (
    AttendanceRecord,
    CheckInRequest,
    Shift,
    WorkMode,
)
from app.domains.attendance.service import (
    AttendanceMutation,
    AttendanceService,
    ShiftService,
)
from app.domains.employees.schemas import EmployeeStatus
from app.domains.organizations.schemas import Location
from app.domains.organizations.service import LocationService
from app.shared.identity import Identity, Role
from tests.unit.attendance.fakes import (
    FakeAttendanceRepo,
    FakePresencePublisher,
    FakeShiftRepo,
)
from tests.unit.organizations.fakes import FakeLocationRepo, RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
USER = UUID("00000000-0000-4000-8000-000000000031")  # the acting user id
ACTOR = UUID("00000000-0000-4000-8000-000000000002")  # its employee link
REPORT = UUID("00000000-0000-4000-8000-000000000003")
OTHER = UUID("00000000-0000-4000-8000-000000000004")
LOCATION = UUID("00000000-0000-4000-8000-00000000000c")
LOCATION_B = UUID("00000000-0000-4000-8000-00000000000d")
HCMC = "Asia/Ho_Chi_Minh"

# NOW = 2026-09-24 12:00 UTC = 19:00 in Asia/Ho_Chi_Minh.
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
TODAY = date(2026, 9, 24)


def identity(
    tenant_id: UUID = TENANT_A,
    role: Role = Role.HR,
    *,
    employee_id: UUID | None = ACTOR,
    actor_id: UUID = USER,
) -> Identity:
    """The acting user (`actor_id`) plus its optional employee link.

    The user id and the employee id are deliberately different values: the
    identity the services consume carries both (ADR 0004 swap).
    """
    return Identity(
        tenant_id=tenant_id,
        actor_id=actor_id,
        role=role,
        employee_id=employee_id,
    )


@pytest.fixture
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def attendance_repo() -> FakeAttendanceRepo:
    return FakeAttendanceRepo()


@pytest.fixture
def shift_repo() -> FakeShiftRepo:
    return FakeShiftRepo()


@pytest.fixture
def location_repo() -> FakeLocationRepo:
    return FakeLocationRepo()


@pytest.fixture
def presence() -> FakePresencePublisher:
    return FakePresencePublisher()


@pytest.fixture
def outbox() -> RecordingEventOutbox:
    return RecordingEventOutbox()


@pytest.fixture
def service(
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
    audit: RecordingAuditWriter,
    presence: FakePresencePublisher,
    outbox: RecordingEventOutbox,
) -> AttendanceService:
    return AttendanceService(
        repo=attendance_repo,
        shifts=shift_repo,
        audit=audit,
        presence=presence,
        outbox=outbox,
    )


@pytest.fixture
def shift_service(
    shift_repo: FakeShiftRepo,
    location_repo: FakeLocationRepo,
    audit: RecordingAuditWriter,
) -> ShiftService:
    return ShiftService(
        repo=shift_repo,
        locations=LocationService(repo=location_repo, audit=audit),
        audit=audit,
    )


# --- seeding helpers ---------------------------------------------------------


def seed_employee(
    repo: FakeAttendanceRepo,
    *,
    employee_id: UUID = ACTOR,
    tenant_id: UUID = TENANT_A,
    status: EmployeeStatus = EmployeeStatus.ACTIVE,
    location_id: UUID = LOCATION,
    timezone: str = HCMC,
    manager_id: UUID | None = None,
) -> EmployeeContext:
    context = EmployeeContext(
        id=employee_id,
        status=status,
        location_id=location_id,
        timezone=timezone,
        manager_id=manager_id,
    )
    repo.employees[(tenant_id, employee_id)] = context
    return context


async def seed_shift(
    repo: FakeShiftRepo,
    *,
    tenant_id: UUID = TENANT_A,
    name: str = "Day",
    start_time: time = time(9, 0),
    end_time: time = time(18, 0),
    grace_minutes: int = 10,
    location_id: UUID | None = None,
    shift_id: UUID | None = None,
) -> Shift:
    return await repo.add(
        tenant_id,
        shift_id or uuid7(),
        name=name,
        start_time=start_time,
        end_time=end_time,
        grace_minutes=grace_minutes,
        location_id=location_id,
    )


async def seed_location(
    repo: FakeLocationRepo,
    *,
    location_id: UUID = LOCATION,
    tenant_id: UUID = TENANT_A,
    name: str = "HQ",
    timezone: str = HCMC,
) -> Location:
    return await repo.add(tenant_id, location_id, name, None, timezone)


async def seed_record(
    repo: FakeAttendanceRepo,
    *,
    tenant_id: UUID = TENANT_A,
    employee_id: UUID = ACTOR,
    workday: date = TODAY,
    checked_in_at: datetime = NOW,
    checked_out_at: datetime | None = None,
    work_mode: WorkMode = WorkMode.ONSITE,
    late: bool = False,
    shift_id: UUID | None = None,
    duration_minutes: int | None = None,
    check_in_note: str | None = None,
    check_out_note: str | None = None,
) -> AttendanceRecord:
    record = await repo.add(
        tenant_id,
        uuid7(),
        employee_id=employee_id,
        workday=workday,
        checked_in_at=checked_in_at,
        work_mode=work_mode,
        late=late,
        shift_id=shift_id,
        check_in_note=check_in_note,
    )
    if checked_out_at is None:
        return record
    closed = await repo.close(
        tenant_id,
        record.id,
        checked_out_at=checked_out_at,
        duration_minutes=duration_minutes if duration_minutes is not None else 0,
        check_out_note=check_out_note,
    )
    assert closed is not None
    return closed


async def check_in(
    service: AttendanceService,
    *,
    caller: Identity | None = None,
    work_mode: str = "onsite",
    note: str | None = None,
    now: datetime = NOW,
) -> AttendanceMutation:
    return await service.check_in(
        caller or identity(),
        CheckInRequest(work_mode=work_mode, check_in_note=note),
        now,
    )
