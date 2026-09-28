"""Repository integration tests: tenant scoping, filters, code generation, history."""

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.domains.employees.exceptions import (
    EmployeeCodeTakenError,
    EmployeeEmailTakenError,
)
from app.domains.employees.repository import EmployeeFilters
from app.domains.employees.schemas import (
    Employee,
    EmployeeStatus,
    EmploymentType,
)
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
    PostgresEmploymentRepository,
)
from app.infrastructure.postgres.repositories.organizations import (
    PostgresDepartmentRepository,
    PostgresLocationRepository,
    PostgresPositionRepository,
)
from app.shared.pagination import PageParams
from tests.integration.conftest import TENANT_A, TENANT_B

HIRED_AT = date(2026, 1, 5)
EFFECTIVE_AT = date(2026, 6, 1)


@pytest.fixture
def employees(session: AsyncSession) -> PostgresEmployeeRepository:
    return PostgresEmployeeRepository(session)


@pytest.fixture
def employments(session: AsyncSession) -> PostgresEmploymentRepository:
    return PostgresEmploymentRepository(session)


class OrgRefs:
    """Deterministic organization rows to reference from employees."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.departments = PostgresDepartmentRepository(session)
        self.positions = PostgresPositionRepository(session)
        self.locations = PostgresLocationRepository(session)

    async def seed(self, tenant_id: UUID, suffix: str) -> tuple[UUID, UUID, UUID]:
        department_id, position_id, location_id = uuid7(), uuid7(), uuid7()
        await self.departments.add(tenant_id, department_id, f"Dept {suffix}", None)
        await self.positions.add(tenant_id, position_id, f"Position {suffix}", None)
        await self.locations.add(
            tenant_id, location_id, f"Location {suffix}", None, "UTC"
        )
        return department_id, position_id, location_id


async def _add_employee(
    employees: PostgresEmployeeRepository,
    tenant_id: UUID,
    refs: tuple[UUID, UUID, UUID],
    *,
    employee_code: str,
    first_name: str = "Ada",
    last_name: str = "Lovelace",
    email: str = "ada@example.test",
    phone: str | None = None,
    manager_id: UUID | None = None,
    status: EmployeeStatus = EmployeeStatus.ACTIVE,
    employment_type: EmploymentType = EmploymentType.FULL_TIME,
) -> Employee:
    department_id, position_id, location_id = refs
    return await employees.add(
        tenant_id,
        uuid7(),
        employee_code=employee_code,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        manager_id=manager_id,
        employment_type=employment_type,
        status=status,
        hired_at=HIRED_AT,
    )


async def test_employee_scoping_both_directions(
    employees: PostgresEmployeeRepository, session: AsyncSession
) -> None:
    refs_a = await OrgRefs(session).seed(TENANT_A, "A")
    refs_b = await OrgRefs(session).seed(TENANT_B, "B")
    employee_a = await _add_employee(
        employees, TENANT_A, refs_a, employee_code="EMP-00001"
    )
    employee_b = await _add_employee(
        employees, TENANT_B, refs_b, employee_code="EMP-00001", email="b@example.test"
    )

    assert (await employees.get(TENANT_A, employee_a.id)) is not None
    assert (
        await employees.get(TENANT_B, employee_a.id) is None
    )  # A's row invisible to B
    assert await employees.get(TENANT_A, employee_b.id) is None

    items_a, total_a = await employees.list(TENANT_A, EmployeeFilters(), PageParams())
    items_b, total_b = await employees.list(TENANT_B, EmployeeFilters(), PageParams())
    assert total_a == 1 and [item.id for item in items_a] == [employee_a.id]
    assert total_b == 1 and [item.id for item in items_b] == [employee_b.id]

    # Uniqueness is per tenant: the same email/code may exist in another tenant.
    assert await employees.email_taken(TENANT_A, "ada@example.test") is True
    assert await employees.email_taken(TENANT_B, "ada@example.test") is False
    assert await employees.code_taken(TENANT_A, "EMP-00001") is True
    assert await employees.code_taken(TENANT_A, "emp-00001") is False  # exact match


async def test_next_employee_code_is_monotonic_per_tenant(
    employees: PostgresEmployeeRepository, session: AsyncSession
) -> None:
    refs_a = await OrgRefs(session).seed(TENANT_A, "A")
    await OrgRefs(session).seed(TENANT_B, "B")

    assert await employees.next_employee_code(TENANT_A) == "EMP-00001"
    await _add_employee(employees, TENANT_A, refs_a, employee_code="EMP-00001")
    assert await employees.next_employee_code(TENANT_A) == "EMP-00002"
    # An HR-supplied non-numeric override is ignored by the scan.
    await _add_employee(
        employees,
        TENANT_A,
        refs_a,
        employee_code="EMP-TEMP",
        email="temp@example.test",
    )
    # So is a numeric override too large to cast safely (regression: no 500).
    await _add_employee(
        employees,
        TENANT_A,
        refs_a,
        employee_code="EMP-99999999999",
        email="huge@example.test",
    )
    assert await employees.next_employee_code(TENANT_A) == "EMP-00002"
    # Other tenants have their own sequence.
    assert await employees.next_employee_code(TENANT_B) == "EMP-00001"


async def test_list_filters_and_ordering(
    employees: PostgresEmployeeRepository, session: AsyncSession
) -> None:
    refs = await OrgRefs(session).seed(TENANT_A, "A")
    department_id, position_id, location_id = refs
    manager = await _add_employee(
        employees,
        TENANT_A,
        refs,
        employee_code="EMP-00001",
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.test",
    )
    await _add_employee(
        employees,
        TENANT_A,
        refs,
        employee_code="EMP-00002",
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.test",
        manager_id=manager.id,
    )
    await _add_employee(
        employees,
        TENANT_A,
        refs,
        employee_code="EMP-00003",
        first_name="Alan",
        last_name="Turing",
        email="alan@example.test",
        status=EmployeeStatus.ON_LEAVE,
    )

    # Default order: last_name, first_name ascending — stable across pages.
    items, total = await employees.list(TENANT_A, EmployeeFilters(), PageParams())
    assert total == 3
    assert [item.last_name for item in items] == ["Hopper", "Lovelace", "Turing"]

    # q — case-insensitive substring over first/last name, email, code.
    by_name, _ = await employees.list(TENANT_A, EmployeeFilters(q="LOVE"), PageParams())
    assert [item.email for item in by_name] == ["ada@example.test"]
    by_email, _ = await employees.list(
        TENANT_A, EmployeeFilters(q="alan@"), PageParams()
    )
    assert [item.employee_code for item in by_email] == ["EMP-00003"]
    by_code, _ = await employees.list(
        TENANT_A, EmployeeFilters(q="emp-00001"), PageParams()
    )
    assert [item.first_name for item in by_code] == ["Grace"]

    _, by_department = await employees.list(
        TENANT_A, EmployeeFilters(department_id=department_id), PageParams()
    )
    _, by_position = await employees.list(
        TENANT_A, EmployeeFilters(position_id=position_id), PageParams()
    )
    _, by_location = await employees.list(
        TENANT_A, EmployeeFilters(location_id=location_id), PageParams()
    )
    assert (by_department, by_position, by_location) == (3, 3, 3)

    items_status, total_status = await employees.list(
        TENANT_A, EmployeeFilters(status=EmployeeStatus.ON_LEAVE), PageParams()
    )
    assert total_status == 1 and items_status[0] is not None
    assert items_status[0].employee_code == "EMP-00003"

    items_reports, total_reports = await employees.list(
        TENANT_A, EmployeeFilters(manager_id=manager.id), PageParams()
    )
    assert total_reports == 1 and items_reports[0] is not None
    assert items_reports[0].employee_code == "EMP-00002"

    # Pagination + total are consistent.
    page1, _ = await employees.list(
        TENANT_A, EmployeeFilters(), PageParams(page=1, page_size=2)
    )
    page2, _ = await employees.list(
        TENANT_A, EmployeeFilters(), PageParams(page=2, page_size=2)
    )
    assert [item.last_name for item in page1] == ["Hopper", "Lovelace"]
    assert [item.last_name for item in page2] == ["Turing"]


async def test_duplicate_email_and_code_map_to_taken(
    employees: PostgresEmployeeRepository, session: AsyncSession
) -> None:
    refs = await OrgRefs(session).seed(TENANT_A, "A")
    await _add_employee(employees, TENANT_A, refs, employee_code="EMP-00001")

    with pytest.raises(EmployeeEmailTakenError):
        await _add_employee(employees, TENANT_A, refs, employee_code="EMP-00002")
    await session.rollback()

    # The failed flush rolled the whole test transaction back: re-seed, then
    # race a case-variant email (the functional unique index is the backstop).
    refs = await OrgRefs(session).seed(TENANT_A, "A2")
    await _add_employee(employees, TENANT_A, refs, employee_code="EMP-00001")
    with pytest.raises(EmployeeEmailTakenError):
        await _add_employee(
            employees,
            TENANT_A,
            refs,
            employee_code="EMP-00002",
            email="ADA@EXAMPLE.TEST",
        )
    await session.rollback()

    refs = await OrgRefs(session).seed(TENANT_A, "A3")
    await _add_employee(employees, TENANT_A, refs, employee_code="EMP-00001")
    with pytest.raises(EmployeeCodeTakenError):
        await _add_employee(
            employees,
            TENANT_A,
            refs,
            employee_code="EMP-00001",
            email="other@example.test",
        )
    await session.rollback()


async def test_update_direct_reports_and_clear_manager(
    employees: PostgresEmployeeRepository, session: AsyncSession
) -> None:
    refs = await OrgRefs(session).seed(TENANT_A, "A")
    manager = await _add_employee(employees, TENANT_A, refs, employee_code="EMP-00001")
    report_one = await _add_employee(
        employees,
        TENANT_A,
        refs,
        employee_code="EMP-00002",
        email="one@example.test",
        manager_id=manager.id,
    )
    report_two = await _add_employee(
        employees,
        TENANT_A,
        refs,
        employee_code="EMP-00003",
        email="two@example.test",
        manager_id=manager.id,
    )

    reports = await employees.direct_reports(TENANT_A, manager.id)
    assert {report.id for report in reports} == {report_one.id, report_two.id}

    updated = await employees.update(
        TENANT_A, report_one.id, phone="+84-000", status=EmployeeStatus.ON_LEAVE
    )
    assert updated is not None
    assert updated.phone == "+84-000"
    assert updated.status == EmployeeStatus.ON_LEAVE

    cleared = await employees.clear_manager(TENANT_A, manager.id)
    assert set(cleared) == {report_one.id, report_two.id}
    assert await employees.direct_reports(TENANT_A, manager.id) == []
    refreshed = await employees.get(TENANT_A, report_one.id)
    assert refreshed is not None and refreshed.manager_id is None

    assert await employees.update(TENANT_A, uuid7(), phone="+84-000") is None


async def test_employment_current_history_close_and_append(
    employments: PostgresEmploymentRepository, session: AsyncSession
) -> None:
    refs = await OrgRefs(session).seed(TENANT_A, "A")
    employees = PostgresEmployeeRepository(session)
    employee = await _add_employee(employees, TENANT_A, refs, employee_code="EMP-00001")
    department_id, position_id, location_id = refs

    first = await employments.add(
        TENANT_A,
        uuid7(),
        employee_id=employee.id,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        employment_type=EmploymentType.FULL_TIME,
        effective_from=HIRED_AT,
    )
    current = await employments.current(TENANT_A, employee.id)
    assert current is not None and current.id == first.id
    assert current.effective_to is None

    closed = await employments.close(TENANT_A, first.id, EFFECTIVE_AT)
    assert closed is not None and closed.effective_to == EFFECTIVE_AT
    assert await employments.current(TENANT_A, employee.id) is None

    second = await employments.add(
        TENANT_A,
        uuid7(),
        employee_id=employee.id,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        employment_type=EmploymentType.PART_TIME,
        effective_from=EFFECTIVE_AT,
    )
    history = await employments.history(TENANT_A, employee.id)
    assert [item.id for item in history] == [second.id, first.id]  # newest first
    assert await employments.current(TENANT_A, employee.id) is not None

    # Tenant scoping: B sees nothing of A's history.
    assert await employments.current(TENANT_B, employee.id) is None
    assert await employments.history(TENANT_B, employee.id) == []


async def test_close_is_guarded_and_one_open_period_is_enforced(
    employments: PostgresEmploymentRepository, session: AsyncSession
) -> None:
    """A closed period is never rewritten; two open periods are impossible."""
    refs = await OrgRefs(session).seed(TENANT_A, "A")
    employees = PostgresEmployeeRepository(session)
    employee = await _add_employee(employees, TENANT_A, refs, employee_code="EMP-00001")
    department_id, position_id, location_id = refs

    first = await employments.add(
        TENANT_A,
        uuid7(),
        employee_id=employee.id,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        employment_type=EmploymentType.FULL_TIME,
        effective_from=HIRED_AT,
    )
    closed = await employments.close(TENANT_A, first.id, EFFECTIVE_AT)
    assert closed is not None and closed.effective_to == EFFECTIVE_AT

    # Closing again must not rewrite the already-closed period.
    assert await employments.close(TENANT_A, first.id, date(2026, 8, 1)) is None
    reopened = await employments.history(TENANT_A, employee.id)
    assert reopened[0] is not None and reopened[0].effective_to == EFFECTIVE_AT

    # The partial unique index backstops "at most one open period".
    await employments.add(
        TENANT_A,
        uuid7(),
        employee_id=employee.id,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        employment_type=EmploymentType.FULL_TIME,
        effective_from=EFFECTIVE_AT,
    )
    with pytest.raises(IntegrityError):
        await employments.add(
            TENANT_A,
            uuid7(),
            employee_id=employee.id,
            department_id=department_id,
            position_id=position_id,
            location_id=location_id,
            employment_type=EmploymentType.FULL_TIME,
            effective_from=date(2026, 7, 1),
        )
    await session.rollback()


async def test_list_ties_are_ordered_by_id_for_stable_pages(
    employees: PostgresEmployeeRepository, session: AsyncSession
) -> None:
    """Equal names keep a deterministic order across pages (AC2 stability)."""
    refs = await OrgRefs(session).seed(TENANT_A, "A")
    created_ids = []
    for index in range(3):
        row = await _add_employee(
            employees,
            TENANT_A,
            refs,
            employee_code=f"EMP-{index + 1:05d}",
            first_name="Same",
            last_name="Name",
            email=f"same{index}@example.test",
        )
        created_ids.append(row.id)

    page1, total = await employees.list(
        TENANT_A, EmployeeFilters(), PageParams(page=1, page_size=2)
    )
    page2, _ = await employees.list(
        TENANT_A, EmployeeFilters(), PageParams(page=2, page_size=2)
    )

    paged_ids = [item.id for item in page1] + [item.id for item in page2]
    assert total == 3
    assert paged_ids == sorted(created_ids)  # id ascending, no repeats/drops


async def test_employment_check_constraint_rejects_backwards_period(
    employments: PostgresEmploymentRepository, session: AsyncSession
) -> None:
    """`effective_to >= effective_from` is enforced by the DB, not only code."""
    refs = await OrgRefs(session).seed(TENANT_A, "A")
    employees = PostgresEmployeeRepository(session)
    employee = await _add_employee(employees, TENANT_A, refs, employee_code="EMP-00001")
    department_id, position_id, location_id = refs

    with pytest.raises(IntegrityError):
        await employments.add(
            TENANT_A,
            uuid7(),
            employee_id=employee.id,
            department_id=department_id,
            position_id=position_id,
            location_id=location_id,
            employment_type=EmploymentType.FULL_TIME,
            effective_from=EFFECTIVE_AT,
            effective_to=HIRED_AT,
        )
    await session.rollback()
