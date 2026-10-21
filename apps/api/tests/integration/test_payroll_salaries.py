"""HTTP integration tests for effective-dated salary records (T5).

Salary history is append-only and one currency per tenant (PO Decision 4):
the tenant config's currency is stamped at creation. Covered here:

- create → `201` with the config currency, list newest-first and filterable by
  employee, partial pagination envelope;
- `409 salary_effective_date_taken`, `422` for sub-cent amounts, `404` for an
  employee outside the tenant;
- role matrix: HR reads but cannot create; employee/manager cannot read;
- tenant isolation: tenant B sees none of tenant A's records and cannot append
  to a tenant-A employee;
- audit: salary amounts never appear in `changes` (PII), only "changed".
"""

from datetime import date
from uuid import UUID, uuid4

from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    TENANT_B,
    USER,
    ClientFactory,
    create_org_refs,
    identity,
)

OTHER = UUID("00000000-0000-4000-8000-0000000000e2")
TENANT_B_EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000b2")


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def _seed_employee(
    session: AsyncSession,
    refs: tuple[str, str, str],
    *,
    tenant: UUID = TENANT_A,
    employee_id: UUID = ACTOR,
    code: str = "EMP-00009",
    email: str = "self@example.test",
) -> None:
    await PostgresEmployeeRepository(session).add(
        tenant,
        employee_id,
        employee_code=code,
        first_name="Actor",
        last_name="Self",
        email=email,
        phone=None,
        department_id=UUID(refs[0]),
        position_id=UUID(refs[1]),
        location_id=UUID(refs[2]),
        manager_id=None,
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        hired_at=date(2026, 1, 5),
    )


async def _seed_world(client_factory: ClientFactory, session: AsyncSession) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)
    await _seed_employee(
        session,
        (refs[0], refs[1], refs[2]),
        employee_id=OTHER,
        code="EMP-00010",
        email="other@example.test",
    )

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b_admin:
        refs_b = await create_org_refs(tenant_b_admin, suffix="-b")
    await _seed_employee(
        session,
        refs_b,
        tenant=TENANT_B,
        employee_id=TENANT_B_EMPLOYEE,
        code="EMP-00009",
        email="b@example.test",
    )


async def _create_salary(
    client: AsyncClient,
    *,
    employee_id: UUID = ACTOR,
    amount: float = 2500.0,
    effective_from: str = "2026-01-01",
) -> Response:
    return await client.post(
        "/api/v1/payroll/salaries",
        json={
            "employee_id": str(employee_id),
            "amount": amount,
            "effective_from": effective_from,
        },
    )


async def test_salary_create_list_and_currency_from_config(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_world(client_factory, session)

    async with client_factory() as client:
        await client.put("/api/v1/payroll/config", json={"currency": "EUR"})
        created = await _create_salary(client)
        by_employee = await client.get(f"/api/v1/payroll/salaries?employee_id={ACTOR}")
        all_rows = await client.get("/api/v1/payroll/salaries?page=0&page_size=500")

    assert created.status_code == 201
    salary = created.json()
    assert salary["employee_id"] == str(ACTOR)
    assert salary["amount"] == 2500.0
    assert salary["currency"] == "EUR"
    assert salary["effective_from"] == "2026-01-01"
    assert salary["created_by"] == str(USER)

    assert by_employee.status_code == 200
    assert [row["id"] for row in by_employee.json()["items"]] == [salary["id"]]
    assert by_employee.json()["page"]["total_items"] == 1

    # Pagination clamps out-of-range values rather than erroring.
    assert all_rows.status_code == 200
    assert all_rows.json()["page"]["page"] == 1
    assert all_rows.json()["page"]["page_size"] == 100


async def test_salary_history_is_append_only_newest_first(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_world(client_factory, session)

    async with client_factory() as client:
        first = await _create_salary(client, effective_from="2026-01-01")
        second = await _create_salary(
            client, amount=3100.0, effective_from="2026-08-01"
        )
        listed = await client.get("/api/v1/payroll/salaries")

    assert first.status_code == 201
    assert second.status_code == 201
    assert [row["effective_from"] for row in listed.json()["items"]] == [
        "2026-08-01",
        "2026-01-01",
    ]


async def test_salary_duplicate_effective_date_is_a_conflict(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_world(client_factory, session)

    async with client_factory() as client:
        assert (
            await _create_salary(client, effective_from="2026-01-01")
        ).status_code == 201
        duplicate = await _create_salary(
            client, amount=3000.0, effective_from="2026-01-01"
        )

    _assert_error(duplicate, 409, "salary_effective_date_taken")


async def test_salary_amount_must_be_at_least_one_cent(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_world(client_factory, session)

    async with client_factory() as client:
        response = await _create_salary(client, amount=0)

    _assert_error(response, 422, "validation_error")


async def test_salary_for_unknown_employee_is_not_found(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_world(client_factory, session)

    async with client_factory() as client:
        response = await _create_salary(client, employee_id=uuid4())

    _assert_error(response, 404, "employee_not_found")


async def test_salary_role_matrix(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.HR)) as hr:
        assert (await hr.get("/api/v1/payroll/salaries")).status_code == 200
        _assert_error(await _create_salary(hr), 403, "forbidden")

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        _assert_error(
            await employee.get("/api/v1/payroll/salaries"),
            404,
            "payroll_not_found",
        )
        _assert_error(await _create_salary(employee), 403, "forbidden")

    async with client_factory(identity(role=Role.MANAGER)) as manager:
        _assert_error(
            await manager.get("/api/v1/payroll/salaries"), 404, "payroll_not_found"
        )


async def test_salary_records_are_tenant_scoped(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_world(client_factory, session)

    async with client_factory() as tenant_a:
        assert (await _create_salary(tenant_a)).status_code == 201

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        listed = await tenant_b.get("/api/v1/payroll/salaries")
        foreign_target = await _create_salary(tenant_b, employee_id=ACTOR)
        own_target = await _create_salary(tenant_b, employee_id=TENANT_B_EMPLOYEE)

    assert listed.status_code == 200
    assert listed.json()["items"] == []
    _assert_error(foreign_target, 404, "employee_not_found")
    assert own_target.status_code == 201
    assert own_target.json()["employee_id"] == str(TENANT_B_EMPLOYEE)


async def test_salary_audit_never_records_the_amount(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_world(client_factory, session)

    async with client_factory() as client:
        created = await _create_salary(client, amount=2500.0)

    assert created.status_code == 201
    entries = list(
        (
            await session.execute(
                select(AuditEntry).where(AuditEntry.action == "payroll.salary_created")
            )
        )
        .scalars()
        .all()
    )
    assert len(entries) == 1
    changes = entries[0].changes
    assert changes is not None
    assert changes["amount"] == "changed"
    assert changes["employee_id"] == {"old": None, "new": str(ACTOR)}
    assert changes["currency"] == {"old": None, "new": "USD"}
    assert "2500" not in str(changes)
