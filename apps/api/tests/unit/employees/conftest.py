"""Shared fixtures: an EmployeeService wired to in-memory fakes (no DB)."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from uuid_utils.compat import uuid7

from app.domains.employees.schemas import (
    CreateEmployee,
    Employee,
    EmployeeStatus,
    EmploymentType,
)
from app.domains.employees.service import EmployeeService
from app.domains.organizations.repository import NullDepartmentUsage
from app.domains.organizations.service import (
    DepartmentService,
    LocationService,
    PositionService,
)
from app.shared.identity import Identity, Role
from tests.unit.employees.fakes import FakeEmployeeRepo, FakeEmploymentRepo
from tests.unit.organizations.fakes import (
    FakeDepartmentRepo,
    FakeLocationRepo,
    FakePositionRepo,
    RecordingAuditWriter,
)
from tests.unit.outbox.fakes import RecordingEventOutbox

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
ACTOR = UUID("00000000-0000-4000-8000-000000000002")  # the acting user id
EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000021")  # its employee link
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
TODAY = date(2026, 9, 24)
HIRED_AT = date(2025, 1, 1)


def identity(
    tenant_id: UUID = TENANT_A,
    role: Role = Role.HR,
    *,
    employee_id: UUID | None = None,
) -> Identity:
    return Identity(
        tenant_id=tenant_id, actor_id=ACTOR, role=role, employee_id=employee_id
    )


@dataclass
class OrgWorld:
    """Organizations domain (real services, fake repos) for reference checks."""

    department_repo: FakeDepartmentRepo
    position_repo: FakePositionRepo
    location_repo: FakeLocationRepo
    departments: DepartmentService
    positions: PositionService
    locations: LocationService

    async def seed(self, tenant_id: UUID = TENANT_A) -> tuple[UUID, UUID, UUID]:
        department_id, position_id, location_id = uuid7(), uuid7(), uuid7()
        await self.department_repo.add(tenant_id, department_id, "Engineering", None)
        await self.position_repo.add(tenant_id, position_id, "Engineer", None)
        await self.location_repo.add(tenant_id, location_id, "HQ", None, "UTC")
        return department_id, position_id, location_id


@pytest.fixture
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def outbox() -> RecordingEventOutbox:
    return RecordingEventOutbox()


@pytest.fixture
def repo() -> FakeEmployeeRepo:
    return FakeEmployeeRepo()


@pytest.fixture
def employments() -> FakeEmploymentRepo:
    return FakeEmploymentRepo()


@pytest.fixture
def orgs(audit: RecordingAuditWriter) -> OrgWorld:
    department_repo = FakeDepartmentRepo()
    position_repo = FakePositionRepo()
    location_repo = FakeLocationRepo()
    return OrgWorld(
        department_repo=department_repo,
        position_repo=position_repo,
        location_repo=location_repo,
        departments=DepartmentService(
            repo=department_repo, audit=audit, usage=NullDepartmentUsage()
        ),
        positions=PositionService(repo=position_repo, audit=audit),
        locations=LocationService(repo=location_repo, audit=audit),
    )


@pytest.fixture
def service(
    repo: FakeEmployeeRepo,
    employments: FakeEmploymentRepo,
    orgs: OrgWorld,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> EmployeeService:
    return EmployeeService(
        repo=repo,
        employments=employments,
        departments=orgs.departments,
        positions=orgs.positions,
        locations=orgs.locations,
        audit=audit,
        outbox=outbox,
    )


async def seed_actor_employee(
    repo: FakeEmployeeRepo, refs: tuple[UUID, UUID, UUID]
) -> Employee:
    """Seed the employee the acting user is linked to (`EMPLOYEE_ID`).

    The actor is a user id (`ACTOR`); the employee is a separate row — the
    link between them is `identity(employee_id=EMPLOYEE_ID)`.
    """
    department_id, position_id, location_id = refs
    return await repo.add(
        TENANT_A,
        EMPLOYEE_ID,
        employee_code="EMP-00009",
        first_name="Actor",
        last_name="Self",
        email="self@example.test",
        phone=None,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        manager_id=None,
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        hired_at=HIRED_AT,
    )


async def create_employee(
    service: EmployeeService,
    refs: tuple[UUID, UUID, UUID],
    *,
    caller: Identity | None = None,
    first_name: str = "Ada",
    last_name: str = "Lovelace",
    email: str = "ada@example.test",
    manager_id: UUID | None = None,
    employee_code: str | None = None,
    hired_at: date = HIRED_AT,
) -> Employee:
    department_id, position_id, location_id = refs
    return await service.create(
        caller or identity(),
        CreateEmployee(
            first_name=first_name,
            last_name=last_name,
            email=email,
            department_id=department_id,
            position_id=position_id,
            location_id=location_id,
            manager_id=manager_id,
            employment_type=EmploymentType.FULL_TIME,
            hired_at=hired_at,
            employee_code=employee_code,
        ),
        NOW,
    )
