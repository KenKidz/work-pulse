"""Shared fixtures for the Overtime domain unit tests (no database, no broker).

`NOW` is the injected clock every test passes explicitly — the service never
reads the wall clock (`.ai/testing.md`: no wall-time assertions). `TODAY` is
`NOW`'s server date, the boundary for the "hours already worked" rule.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from uuid_utils.compat import uuid7

from app.domains.employees.schemas import EmployeeStatus
from app.domains.overtime.repository import EmployeeFacts
from app.domains.overtime.schemas import (
    OvertimeDecision,
    OvertimeRequest,
    OvertimeRequestCreate,
    OvertimeRequestStatus,
)
from app.domains.overtime.service import OvertimeService
from app.shared.identity import Identity, Role
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox
from tests.unit.overtime.fakes import CREATED_AT, FakeOvertimeRepo

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


def manager(*, employee_id: UUID = MANAGER, actor_id: UUID = USER) -> Identity:
    """A manager identity (manager step only, no HR standing)."""
    return identity(role=Role.MANAGER, employee_id=employee_id, actor_id=actor_id)


@pytest.fixture
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def overtime_repo() -> FakeOvertimeRepo:
    return FakeOvertimeRepo()


@pytest.fixture
def outbox() -> RecordingEventOutbox:
    return RecordingEventOutbox()


@pytest.fixture
def service(
    overtime_repo: FakeOvertimeRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> OvertimeService:
    return OvertimeService(repo=overtime_repo, audit=audit, outbox=outbox)


# --- seeding helpers ---------------------------------------------------------


def seed_employee(
    repo: FakeOvertimeRepo,
    *,
    employee_id: UUID = EMPLOYEE,
    tenant_id: UUID = TENANT_A,
    status: EmployeeStatus = EmployeeStatus.ACTIVE,
    manager_id: UUID | None = None,
) -> EmployeeFacts:
    facts = EmployeeFacts(id=employee_id, status=status, manager_id=manager_id)
    repo.employees[(tenant_id, employee_id)] = facts
    return facts


def seed_request(
    repo: FakeOvertimeRepo,
    *,
    employee_id: UUID = EMPLOYEE,
    tenant_id: UUID = TENANT_A,
    work_date: date = TODAY,
    hours: Decimal = Decimal("2.00"),
    status: OvertimeRequestStatus = OvertimeRequestStatus.REQUESTED,
    reason: str | None = None,
    included_in_run_id: UUID | None = None,
) -> OvertimeRequest:
    """Insert a row directly — only for states no service path can produce."""
    row = OvertimeRequest(
        id=uuid7(),
        tenant_id=tenant_id,
        employee_id=employee_id,
        work_date=work_date,
        hours=hours,
        status=status,
        reason=reason,
        included_in_run_id=included_in_run_id,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )
    repo.requests[(tenant_id, row.id)] = row
    return row


async def submit(
    service: OvertimeService,
    *,
    caller: Identity | None = None,
    work_date: date = TODAY,
    hours: Decimal = Decimal("2.50"),
    reason: str | None = None,
    now: datetime = NOW,
) -> OvertimeRequest:
    return await service.create_request(
        caller or identity(),
        OvertimeRequestCreate(
            work_date=work_date,
            hours=hours,
            reason=reason,
        ),
        now,
    )


async def approve(
    service: OvertimeService,
    request_id: UUID,
    *,
    caller: Identity | None = None,
    note: str | None = None,
    now: datetime = NOW,
) -> OvertimeRequest:
    return await service.approve(
        caller or decider(), request_id, OvertimeDecision(note=note), now
    )


async def verify(
    service: OvertimeService,
    request_id: UUID,
    *,
    caller: Identity | None = None,
    note: str | None = None,
    now: datetime = NOW,
) -> OvertimeRequest:
    return await service.verify(
        caller or decider(), request_id, OvertimeDecision(note=note), now
    )


async def reject(
    service: OvertimeService,
    request_id: UUID,
    *,
    caller: Identity | None = None,
    note: str | None = None,
    now: datetime = NOW,
) -> OvertimeRequest:
    return await service.reject(
        caller or decider(), request_id, OvertimeDecision(note=note), now
    )
