"""Shared fixtures for the Leave domain unit tests (no database, no broker).

`NOW` is the injected clock every test passes explicitly — the service never
reads the wall clock (`.ai/testing.md`: no wall-time assertions).
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from app.domains.employees.schemas import EmployeeStatus
from app.domains.leave.repository import EmployeeFacts
from app.domains.leave.schemas import (
    LeaveRequest,
    LeaveRequestCreate,
    LeaveType,
    LeaveTypeCode,
)
from app.domains.leave.service import LeaveService
from app.shared.identity import Identity, Role
from tests.unit.leave.fakes import FakeLeaveRepo
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
USER = UUID("00000000-0000-4000-8000-000000000031")  # the acting user id
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000002")  # requester employee link
MANAGER = UUID("00000000-0000-4000-8000-000000000003")
REPORT = UUID("00000000-0000-4000-8000-000000000004")
OTHER = UUID("00000000-0000-4000-8000-000000000005")

# NOW = 2026-10-19 12:00 UTC; the pinned server date for every submit test.
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
TODAY = date(2026, 10, 19)

ANNUAL = LeaveTypeCode.ANNUAL
SICK = LeaveTypeCode.SICK
UNPAID = LeaveTypeCode.UNPAID


def identity(
    tenant_id: UUID = TENANT_A,
    role: Role = Role.EMPLOYEE,
    *,
    employee_id: UUID | None = EMPLOYEE,
    actor_id: UUID = USER,
) -> Identity:
    """The acting user (`actor_id`) plus its optional employee link."""
    return Identity(
        tenant_id=tenant_id,
        actor_id=actor_id,
        role=role,
        employee_id=employee_id,
    )


def decider(*, employee_id: UUID = MANAGER, actor_id: UUID = USER) -> Identity:
    """An HR identity whose employee link is not the requester's."""
    return identity(role=Role.HR, employee_id=employee_id, actor_id=actor_id)


@pytest.fixture
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def leave_repo() -> FakeLeaveRepo:
    return FakeLeaveRepo()


@pytest.fixture
def outbox() -> RecordingEventOutbox:
    return RecordingEventOutbox()


@pytest.fixture
def service(
    leave_repo: FakeLeaveRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> LeaveService:
    return LeaveService(repo=leave_repo, audit=audit, outbox=outbox)


# --- seeding helpers ---------------------------------------------------------


def seed_employee(
    repo: FakeLeaveRepo,
    *,
    employee_id: UUID = EMPLOYEE,
    tenant_id: UUID = TENANT_A,
    status: EmployeeStatus = EmployeeStatus.ACTIVE,
    manager_id: UUID | None = None,
) -> EmployeeFacts:
    facts = EmployeeFacts(id=employee_id, status=status, manager_id=manager_id)
    repo.employees[(tenant_id, employee_id)] = facts
    return facts


async def seed_types(
    repo: FakeLeaveRepo, *, tenant_id: UUID = TENANT_A
) -> dict[LeaveTypeCode, LeaveType]:
    return {row.code: row for row in await repo.ensure_leave_types(tenant_id)}


async def submit(
    service: LeaveService,
    *,
    leave_type_id: UUID,
    caller: Identity | None = None,
    start_date: date = TODAY,
    end_date: date | None = None,
    reason: str | None = None,
    now: datetime = NOW,
) -> LeaveRequest:
    return await service.create_request(
        caller or identity(),
        LeaveRequestCreate(
            leave_type_id=leave_type_id,
            start_date=start_date,
            end_date=end_date if end_date is not None else start_date,
            reason=reason,
        ),
        now,
    )
