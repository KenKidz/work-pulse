"""Tenant isolation for leave (T6) — B never sees or touches A's data.

Tenant scoping is enforced in the Postgres repository (`WHERE tenant_id = ...`
first). This suite proves the HTTP surface: lists are empty, detail/decide/
withdraw are `404`, balances are empty, and A's referenced leave type cannot
be used to create a B request.
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
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
    TENANT_B,
    ClientFactory,
    create_org_refs,
    identity,
)

CLOCK = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
B_EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000b1")


def _assert_error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def _seed_employee(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    refs: tuple[str, str, str],
    employee_id: UUID,
    code: str,
    email: str,
) -> None:
    await PostgresEmployeeRepository(session).add(
        tenant_id,
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


async def _types(client: AsyncClient) -> dict[LeaveTypeCode, dict[str, Any]]:
    response = await client.get("/api/v1/leave/types")
    assert response.status_code == 200
    return {LeaveTypeCode(row["code"]): row for row in response.json()}


async def test_tenant_b_stays_isolated_from_tenant_a_leave(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    async with client_factory() as tenant_a:
        refs_a = await create_org_refs(tenant_a)
    await _seed_employee(
        session,
        tenant_id=TENANT_A,
        refs=refs_a,
        employee_id=ACTOR,
        code="EMP-00009",
        email="self@example.test",
    )

    async with client_factory(identity(role=Role.EMPLOYEE)) as tenant_a_employee:
        types_a = await _types(tenant_a_employee)
        created = await tenant_a_employee.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": types_a[LeaveTypeCode.ANNUAL]["id"],
                "start_date": "2026-10-19",
                "end_date": "2026-10-20",
            },
        )
    assert created.status_code == 201
    request_id = created.json()["id"]

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b_admin:
        refs_b = await create_org_refs(tenant_b_admin, suffix=" B")
    await _seed_employee(
        session,
        tenant_id=TENANT_B,
        refs=refs_b,
        employee_id=B_EMPLOYEE,
        code="EMP-00009",
        email="b@example.test",
    )

    async with client_factory(
        identity(tenant=TENANT_B, employee_id=B_EMPLOYEE)
    ) as tenant_b:
        listed = await tenant_b.get("/api/v1/leave/requests")
        detail = await tenant_b.get(f"/api/v1/leave/requests/{request_id}")
        balances = await tenant_b.get("/api/v1/leave/balances")
        types_b = await _types(tenant_b)
        foreign_type_create = await tenant_b.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": types_a[LeaveTypeCode.ANNUAL]["id"],
                "start_date": "2026-10-19",
                "end_date": "2026-10-19",
            },
        )

    async with client_factory(
        identity(tenant=TENANT_B, role=Role.HR, employee_id=B_EMPLOYEE)
    ) as tenant_b_hr:
        approve = await tenant_b_hr.post(
            f"/api/v1/leave/requests/{request_id}/approve", json={}
        )
        withdraw = await tenant_b_hr.post(
            f"/api/v1/leave/requests/{request_id}/withdraw"
        )

    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert listed.json()["page"]["total_items"] == 0
    _assert_error(detail, 404, "leave_request_not_found")
    _assert_error(approve, 404, "leave_request_not_found")
    _assert_error(withdraw, 404, "leave_request_not_found")
    assert balances.status_code == 200
    assert balances.json() == []
    assert types_b.keys() == types_a.keys()  # same seeded codes per tenant
    assert {row["id"] for row in types_b.values()}.isdisjoint(
        {row["id"] for row in types_a.values()}
    )
    _assert_error(foreign_type_create, 404, "leave_type_not_found")
