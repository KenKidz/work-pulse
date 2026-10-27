"""Shared fixtures for the Performance domain unit tests (no database, no broker).

`NOW` is the injected clock every test passes explicitly — the service never
reads the wall clock (`.ai/testing.md`: no wall-time assertions). `identity()`
keeps the acting **user** id and the optional **employee** link distinct, the
ADR 0010 shape every policy check depends on.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domains.performance.repository import EmployeeFacts
from app.shared.identity import Identity, Role
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox
from tests.unit.performance.fakes import FakeEmployeeScope, FakePerformanceRepo

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
USER = UUID("00000000-0000-4000-8000-000000000031")  # the acting user id
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000002")
MANAGER = UUID("00000000-0000-4000-8000-000000000003")
REPORT = UUID("00000000-0000-4000-8000-000000000004")
PEER = UUID("00000000-0000-4000-8000-000000000005")
OTHER = UUID("00000000-0000-4000-8000-000000000006")

# NOW = 2026-10-26 12:00 UTC — the pinned clock for every mutation test.
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def identity(
    tenant_id: UUID = TENANT_A,
    role: Role = Role.HR,
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
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def repo() -> FakePerformanceRepo:
    return FakePerformanceRepo()


@pytest.fixture
def scope(repo: FakePerformanceRepo) -> FakeEmployeeScope:
    """The manager scope shares the repo's employee directory by default."""
    return FakeEmployeeScope(repo.employees)


@pytest.fixture
def outbox() -> RecordingEventOutbox:
    return RecordingEventOutbox()


# --- seeding helpers ---------------------------------------------------------


def seed_employee(
    repo: FakePerformanceRepo,
    *,
    employee_id: UUID = EMPLOYEE,
    tenant_id: UUID = TENANT_A,
    active: bool = True,
    manager_id: UUID | None = None,
) -> EmployeeFacts:
    """Register an employee in the shared facts directory (repo + scope)."""
    facts = EmployeeFacts(id=employee_id, active=active, manager_id=manager_id)
    repo.employees[(tenant_id, employee_id)] = facts
    return facts
