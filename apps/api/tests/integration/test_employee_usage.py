"""T6 — the real `DepartmentUsagePort`: archiving counts open employments.

Replaces the `NullDepartmentUsage` no-op (organizations Risk 1, ADR 0007):
a department with an active employee can no longer be archived; once the
employee leaves or transfers away, archiving succeeds again.
"""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.employee_usage import PostgresEmployeeUsage
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
    PostgresEmploymentRepository,
)
from app.infrastructure.postgres.repositories.organizations import (
    PostgresDepartmentRepository,
    PostgresLocationRepository,
    PostgresPositionRepository,
)
from tests.integration.conftest import (
    TENANT_A,
    TENANT_B,
    ClientFactory,
    create_org_refs,
    employee_payload,
)


async def test_archive_blocked_while_employee_is_active_then_allowed(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs = await create_org_refs(client)
        created = (
            await client.post("/api/v1/employees", json=employee_payload(refs))
        ).json()

        blocked = await client.post(f"/api/v1/departments/{refs[0]}/archive")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "department_archived_in_use"

        await client.post(
            f"/api/v1/employees/{created['id']}/termination",
            json={"effective_date": "2026-06-01", "reason": "left"},
        )
        allowed = await client.post(f"/api/v1/departments/{refs[0]}/archive")

    assert allowed.status_code == 200
    assert allowed.json()["archived_at"] is not None


async def test_archive_allowed_after_transfer_away(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        refs_a = await create_org_refs(client)
        refs_b = await create_org_refs(client, suffix="-B")
        created = (
            await client.post("/api/v1/employees", json=employee_payload(refs_a))
        ).json()

        blocked_a = await client.post(f"/api/v1/departments/{refs_a[0]}/archive")
        assert blocked_a.status_code == 409

        await client.post(
            f"/api/v1/employees/{created['id']}/transfers",
            json={"department_id": refs_b[0], "effective_from": "2026-06-01"},
        )
        now_free = await client.post(f"/api/v1/departments/{refs_a[0]}/archive")
        now_in_use = await client.post(f"/api/v1/departments/{refs_b[0]}/archive")

    assert now_free.status_code == 200
    assert now_in_use.status_code == 409
    assert now_in_use.json()["error"]["code"] == "department_archived_in_use"


async def test_usage_counts_only_open_periods_of_active_statuses(
    session: AsyncSession,
) -> None:
    """`on_leave` counts; tenant scoping is honored; closed periods do not."""
    departments = PostgresDepartmentRepository(session)
    positions = PostgresPositionRepository(session)
    locations = PostgresLocationRepository(session)
    employees = PostgresEmployeeRepository(session)
    employments = PostgresEmploymentRepository(session)
    usage = PostgresEmployeeUsage(session)

    department_id, position_id, location_id = uuid7(), uuid7(), uuid7()
    await departments.add(TENANT_A, department_id, "Ops", None)
    await positions.add(TENANT_A, position_id, "Operator", None)
    await locations.add(TENANT_A, location_id, "HQ", None, "UTC")

    employee_id = uuid7()
    await employees.add(
        TENANT_A,
        employee_id,
        employee_code="EMP-00001",
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.test",
        phone=None,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        manager_id=None,
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ON_LEAVE,
        hired_at=date(2026, 1, 5),
    )
    await employments.add(
        TENANT_A,
        uuid7(),
        employee_id=employee_id,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        employment_type=EmploymentType.FULL_TIME,
        effective_from=date(2026, 1, 5),
    )

    assert await usage.count_active_employees(TENANT_A, department_id) == 1
    assert await usage.count_active_employees(TENANT_B, department_id) == 0

    current = await employments.current(TENANT_A, employee_id)
    assert current is not None
    await employments.close(TENANT_A, current.id, date(2026, 6, 1))

    assert await usage.count_active_employees(TENANT_A, department_id) == 0
