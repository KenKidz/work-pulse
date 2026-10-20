"""Leave role matrix at the HTTP edge (T6, `.ai/features/leave.md`).

Requesting/viewing/withdrawing are own-only; deciding is manager (team),
hr, admin, super_admin. An out-of-scope request is a `404` (existence is not
leaked); a visible request with a forbidden transition is a `403`. Self-
approval is always a `403`, whatever the decider's role.
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import leave as leave_api
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.leave.schemas import LeaveTypeCode
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    ClientFactory,
    create_org_refs,
    identity,
)

CLOCK = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
MANAGER = UUID("00000000-0000-4000-8000-0000000000d1")
REPORT = UUID("00000000-0000-4000-8000-0000000000d2")


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def _seed_employee(
    session: AsyncSession,
    refs: tuple[str, str, str],
    *,
    employee_id: UUID,
    code: str,
    email: str,
    manager_id: UUID | None = None,
) -> None:
    await PostgresEmployeeRepository(session).add(
        TENANT_A,
        employee_id,
        employee_code=code,
        first_name="Actor",
        last_name="Self",
        email=email,
        phone=None,
        department_id=UUID(refs[0]),
        position_id=UUID(refs[1]),
        location_id=UUID(refs[2]),
        manager_id=manager_id,
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        hired_at=date(2026, 1, 5),
    )


async def _seed_world(client_factory: ClientFactory, session: AsyncSession) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(
        session, refs, employee_id=ACTOR, code="EMP-00009", email="self@example.test"
    )
    await _seed_employee(
        session,
        refs,
        employee_id=MANAGER,
        code="EMP-00010",
        email="manager@example.test",
    )
    await _seed_employee(
        session,
        refs,
        employee_id=REPORT,
        code="EMP-00011",
        email="report@example.test",
        manager_id=MANAGER,
    )


async def _annual_id(client: AsyncClient) -> str:
    response = await client.get("/api/v1/leave/types")
    assert response.status_code == 200
    types: dict[str, Any] = {row["code"]: row for row in response.json()}
    return types[LeaveTypeCode.ANNUAL.value]["id"]


async def _submit(
    client: AsyncClient,
    leave_type_id: str,
    *,
    start_date: date = date(2026, 10, 19),
) -> Response:
    return await client.post(
        "/api/v1/leave/requests",
        json={
            "leave_type_id": leave_type_id,
            "start_date": start_date.isoformat(),
            "end_date": start_date.isoformat(),
        },
    )


async def test_employee_and_finance_have_no_decide_capability(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        request_id = (await _submit(employee, await _annual_id(employee))).json()["id"]

    for role in (Role.EMPLOYEE, Role.FINANCE):
        async with client_factory(identity(role=role)) as client:
            approve = await client.post(
                f"/api/v1/leave/requests/{request_id}/approve", json={}
            )
            reject = await client.post(
                f"/api/v1/leave/requests/{request_id}/reject", json={}
            )
        _assert_error(approve, 403, "forbidden")
        _assert_error(reject, 403, "forbidden")

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        detail = await employee.get(f"/api/v1/leave/requests/{request_id}")
    assert detail.json()["status"] == "pending"  # denied transitions changed nothing


@pytest.mark.parametrize("role", [Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
async def test_self_approval_is_forbidden_for_every_decider_role(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        request_id = (await _submit(employee, await _annual_id(employee))).json()["id"]

    async with client_factory(identity(role=role, employee_id=ACTOR)) as decider:
        approve = await decider.post(
            f"/api/v1/leave/requests/{request_id}/approve", json={}
        )
        reject = await decider.post(
            f"/api/v1/leave/requests/{request_id}/reject", json={}
        )

    _assert_error(approve, 403, "forbidden")
    _assert_error(reject, 403, "forbidden")


async def test_manager_decides_direct_reports_only(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE, employee_id=REPORT)) as r:
        report_request = (await _submit(r, await _annual_id(r))).json()["id"]
    async with client_factory(identity(role=Role.EMPLOYEE, employee_id=ACTOR)) as a:
        outsider_request = (await _submit(a, await _annual_id(a))).json()["id"]

    async with client_factory(identity(role=Role.MANAGER, employee_id=MANAGER)) as m:
        approved = await m.post(
            f"/api/v1/leave/requests/{report_request}/approve", json={}
        )
        foreign_detail = await m.get(f"/api/v1/leave/requests/{outsider_request}")
        foreign_decide = await m.post(
            f"/api/v1/leave/requests/{outsider_request}/approve", json={}
        )
        foreign_withdraw = await m.post(
            f"/api/v1/leave/requests/{outsider_request}/withdraw"
        )
        listed = await m.get("/api/v1/leave/requests")

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    _assert_error(foreign_detail, 404, "leave_request_not_found")
    _assert_error(foreign_decide, 404, "leave_request_not_found")
    _assert_error(foreign_withdraw, 404, "leave_request_not_found")
    assert [item["id"] for item in listed.json()["items"]] == [report_request]


async def test_hr_sees_all_requests_and_balances(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as a:
        first = (await _submit(a, await _annual_id(a))).json()["id"]
    async with client_factory(identity(role=Role.EMPLOYEE, employee_id=REPORT)) as r:
        second = (await _submit(r, await _annual_id(r))).json()["id"]

    async with client_factory(identity(role=Role.HR, employee_id=MANAGER)) as hr:
        listed = await hr.get("/api/v1/leave/requests")
        first_detail = await hr.get(f"/api/v1/leave/requests/{first}")
        second_detail = await hr.get(f"/api/v1/leave/requests/{second}")
        balances = await hr.get("/api/v1/leave/balances", params={"year": 2026})

    assert {item["id"] for item in listed.json()["items"]} == {first, second}
    assert first_detail.status_code == 200
    assert second_detail.status_code == 200
    assert {row["employee_id"] for row in balances.json()} == {
        str(ACTOR),
        str(REPORT),
    }


async def test_employee_scope_is_own_only(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE, employee_id=REPORT)) as r:
        request_id = (await _submit(r, await _annual_id(r))).json()["id"]

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        listed = await employee.get("/api/v1/leave/requests")
        foreign_detail = await employee.get(f"/api/v1/leave/requests/{request_id}")
        foreign_filter = await employee.get(
            "/api/v1/leave/requests", params={"employee_id": str(REPORT)}
        )
        foreign_balances = await employee.get(
            "/api/v1/leave/balances", params={"employee_id": str(REPORT)}
        )

    assert listed.json()["items"] == []
    _assert_error(foreign_detail, 404, "leave_request_not_found")
    _assert_error(foreign_filter, 404, "employee_not_found")
    _assert_error(foreign_balances, 404, "employee_not_found")


async def test_withdraw_by_a_visible_non_owner_is_403(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        request_id = (await _submit(employee, await _annual_id(employee))).json()["id"]

    async with client_factory(identity(role=Role.HR, employee_id=MANAGER)) as hr:
        response = await hr.post(f"/api/v1/leave/requests/{request_id}/withdraw")

    _assert_error(response, 403, "forbidden")
