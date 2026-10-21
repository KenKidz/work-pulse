"""Shared fixtures for the Payroll domain unit tests (no database, no broker).

`NOW` is the injected clock every future service test passes explicitly — the
payroll service never reads the wall clock (`.ai/testing.md` rule 5). The
seam-port fakes stand in for overtime (not built until P0), attendance and
leave, so payroll is fully testable before those adapters exist (ADR 0018).
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.shared.identity import Identity, Role
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox
from tests.unit.payroll.fakes import (
    FakeAttendanceHoursPort,
    FakeLeaveDaysPort,
    FakeOvertimeHoursPort,
    FakePayrollRepository,
)

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
USER = UUID("00000000-0000-4000-8000-000000000031")  # the acting user id
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000002")  # the user's link
OTHER = UUID("00000000-0000-4000-8000-000000000003")
MANAGER = UUID("00000000-0000-4000-8000-000000000004")

# A fixed payroll clock: 2026-10-21 12:00 UTC. Periods used in tests are
# closed months (e.g. 2026-09), never the current wall-clock month.
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
PERIOD = "2026-09"


def identity(
    tenant_id: UUID = TENANT_A,
    role: Role = Role.ADMIN,
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


@pytest.fixture
def payroll_repo() -> FakePayrollRepository:
    return FakePayrollRepository()


@pytest.fixture
def overtime() -> FakeOvertimeHoursPort:
    return FakeOvertimeHoursPort()


@pytest.fixture
def attendance() -> FakeAttendanceHoursPort:
    return FakeAttendanceHoursPort()


@pytest.fixture
def leave_days() -> FakeLeaveDaysPort:
    return FakeLeaveDaysPort()


@pytest.fixture
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def outbox() -> RecordingEventOutbox:
    return RecordingEventOutbox()
