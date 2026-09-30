"""AC8 + the T1 role matrix — attendance role enforcement at the HTTP edge."""

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

SHIFT_PAYLOAD = {
    "name": "Day",
    "start_time": "09:00",
    "end_time": "18:00",
    "grace_minutes": 10,
}


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


@pytest.mark.parametrize("role", ["employee", "finance"])
async def test_board_denied_for_employee_and_finance(
    client_factory: ClientFactory, role: str
) -> None:
    async with client_factory(identity(role=Role(role))) as client:
        response = await client.get("/api/v1/attendance/board")

    assert response.status_code == 403  # T1: employee/finance → 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_employee_records_are_self_scoped_and_foreign_filter_is_404(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
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
    await _seed_actor(session, refs)

    async with client_factory() as admin:
        mine = (
            await admin.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
    async with client_factory(identity(employee_id=UUID(outsider["id"]))) as them:
        await them.post("/api/v1/attendance/check-ins", json={"work_mode": "onsite"})

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        listed = await employee.get("/api/v1/attendance/records")
        foreign = await employee.get(
            "/api/v1/attendance/records", params={"employee_id": outsider["id"]}
        )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [mine["id"]]
    assert foreign.status_code == 404  # AC8: never 403, never a leak
    assert foreign.json()["error"]["code"] == "employee_not_found"


async def test_manager_records_scope_is_self_and_direct_reports(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_actor(session, refs)  # the actor must exist to be a manager

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

    async with client_factory(identity(employee_id=UUID(report["id"]))) as them:
        report_record = (
            await them.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        ).json()
    async with client_factory(identity(employee_id=UUID(outsider["id"]))) as them:
        await them.post("/api/v1/attendance/check-ins", json={"work_mode": "onsite"})

    async with client_factory(identity(role=Role.MANAGER)) as manager:
        listed = await manager.get("/api/v1/attendance/records")
        foreign = await manager.get(
            "/api/v1/attendance/records", params={"employee_id": outsider["id"]}
        )
        board = await manager.get("/api/v1/attendance/board")

    assert [item["id"] for item in listed.json()["items"]] == [report_record["id"]]
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "employee_not_found"
    assert board.status_code == 200  # managers may read the board


@pytest.mark.parametrize("role", ["employee", "finance", "manager"])
async def test_non_shift_roles_cannot_manage_shifts(
    client_factory: ClientFactory, role: str
) -> None:
    async with client_factory(identity(role=Role(role))) as client:
        created = await client.post("/api/v1/shifts", json=SHIFT_PAYLOAD)
        listed = await client.get("/api/v1/shifts")
        patched = await client.patch(
            f"/api/v1/shifts/{UUID(int=1)}", json={"name": "Nope"}
        )

    for response in (created, listed, patched):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("role", ["hr", "admin", "super_admin"])
async def test_shift_roles_can_manage_shifts(
    client_factory: ClientFactory, role: str
) -> None:
    async with client_factory(identity(role=Role(role))) as client:
        created = await client.post("/api/v1/shifts", json=SHIFT_PAYLOAD)
        assert created.status_code == 201
        listed = await client.get("/api/v1/shifts")
        assert listed.status_code == 200
        patched = await client.patch(
            f"/api/v1/shifts/{created.json()['id']}", json={"grace_minutes": 5}
        )
        assert patched.status_code == 200
        assert patched.json()["grace_minutes"] == 5
