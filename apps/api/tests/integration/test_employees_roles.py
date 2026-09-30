"""AC8 + the roles matrix — role enforcement at the HTTP edge for employees."""

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    ClientFactory,
    create_org_refs,
    employee_payload,
    identity,
)


async def _seed_actor(session: AsyncSession, refs: tuple[str, str, str]) -> None:
    await PostgresEmployeeRepository(session).add(
        TENANT_A,
        ACTOR,
        employee_code="EMP-00009",
        first_name="Actor",
        last_name="Self",
        email="self@example.test",
        phone=None,
        department_id=UUID(refs[0]),
        position_id=UUID(refs[1]),
        location_id=UUID(refs[2]),
        manager_id=None,
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        hired_at=date(2026, 1, 5),
    )


@pytest.mark.parametrize("role", ["employee", "manager", "finance"])
async def test_non_manage_roles_cannot_create(
    client_factory: ClientFactory, role: str
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    async with client_factory(identity(role=Role(role))) as client:
        response = await client.post("/api/v1/employees", json=employee_payload(refs))

    assert response.status_code == 403  # AC8
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("role", ["employee", "manager", "finance"])
async def test_non_manage_roles_cannot_mutate(
    client_factory: ClientFactory, role: str
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        created = (
            await admin.post("/api/v1/employees", json=employee_payload(refs))
        ).json()

    async with client_factory(identity(role=Role(role))) as client:
        responses = [
            await client.patch(
                f"/api/v1/employees/{created['id']}", json={"first_name": "Nope"}
            ),
            await client.post(
                f"/api/v1/employees/{created['id']}/transfers",
                json={"department_id": refs[0], "effective_from": "2026-06-01"},
            ),
            await client.post(
                f"/api/v1/employees/{created['id']}/termination",
                json={"effective_date": "2026-06-01", "reason": "nope"},
            ),
        ]

    for response in responses:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("role", ["hr", "admin", "super_admin"])
async def test_manage_roles_can_create(
    client_factory: ClientFactory, role: str
) -> None:
    async with client_factory(identity(role=Role(role))) as client:
        refs = await create_org_refs(client)
        response = await client.post("/api/v1/employees", json=employee_payload(refs))

    assert response.status_code == 201


@pytest.mark.parametrize("role", ["employee", "finance"])
async def test_roles_without_read_capability_get_403_on_list_and_detail(
    client_factory: ClientFactory, role: str
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        created = (
            await admin.post("/api/v1/employees", json=employee_payload(refs))
        ).json()

    async with client_factory(identity(role=Role(role))) as client:
        listed = await client.get("/api/v1/employees")
        detail = await client.get(f"/api/v1/employees/{created['id']}")

    for response in (listed, detail):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"


async def test_employee_role_can_only_read_me(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_actor(session, refs)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        mine = await employee.get("/api/v1/employees/me")
        listed = await employee.get("/api/v1/employees")

    assert mine.status_code == 200
    assert mine.json()["id"] == str(ACTOR)
    assert listed.status_code == 403


async def test_manager_sees_only_direct_reports(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_actor(session, refs)

    async with client_factory() as admin:
        report = (
            await admin.post(
                "/api/v1/employees",
                json=employee_payload(refs, manager_id=str(ACTOR)),
            )
        ).json()
        outsider = (
            await admin.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs,
                    first_name="Grace",
                    last_name="Hopper",
                    email="grace@example.test",
                ),
            )
        ).json()

    async with client_factory(identity(role=Role.MANAGER)) as manager:
        listed = await manager.get("/api/v1/employees")
        own_report = await manager.get(f"/api/v1/employees/{report['id']}")
        non_report = await manager.get(f"/api/v1/employees/{outsider['id']}")
        me = await manager.get("/api/v1/employees/me")

    items = listed.json()["items"]
    assert [item["id"] for item in items] == [report["id"]]
    assert own_report.status_code == 200
    assert non_report.status_code == 404  # never 403 — no existence leak
    assert non_report.json()["error"]["code"] == "employee_not_found"
    assert me.status_code == 200


async def test_manager_cannot_read_non_report_employments(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        outsider = (
            await admin.post("/api/v1/employees", json=employee_payload(refs))
        ).json()
    await _seed_actor(session, refs)

    async with client_factory(identity(role=Role.MANAGER)) as manager:
        response = await manager.get(f"/api/v1/employees/{outsider['id']}/employments")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "employee_not_found"
