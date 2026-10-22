"""Tenant isolation for overtime (T5) — B never sees or touches A's data.

Tenant scoping is enforced in the Postgres repository (`WHERE tenant_id = ...`
first). This suite proves the HTTP surface: lists are empty, detail/decide/
withdraw are `404`, and a foreign `employee_id` filter is the pinned
`employee_not_found` `404`.
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import overtime as overtime_api
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
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

CLOCK = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
B_EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000b1")


def _assert_error(response: Response, status: int, code: str) -> None:
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


async def _submit(client: AsyncClient) -> Response:
    return await client.post(
        "/api/v1/overtime/requests",
        json={"work_date": "2026-10-21", "hours": "2.50"},
    )


async def test_tenant_b_stays_isolated_from_tenant_a_overtime(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    async with client_factory() as tenant_a_admin:
        refs_a = await create_org_refs(tenant_a_admin)
    await _seed_employee(
        session,
        tenant_id=TENANT_A,
        refs=refs_a,
        employee_id=ACTOR,
        code="EMP-00009",
        email="self@example.test",
    )

    async with client_factory(identity(role=Role.EMPLOYEE)) as tenant_a_employee:
        created = await _submit(tenant_a_employee)
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
        listed = await tenant_b.get("/api/v1/overtime/requests")
        detail = await tenant_b.get(f"/api/v1/overtime/requests/{request_id}")
        withdraw = await tenant_b.post(
            f"/api/v1/overtime/requests/{request_id}/withdraw"
        )

    async with client_factory(
        identity(tenant=TENANT_B, role=Role.HR, employee_id=B_EMPLOYEE)
    ) as tenant_b_hr:
        approve = await tenant_b_hr.post(
            f"/api/v1/overtime/requests/{request_id}/approve", json={}
        )
        verify = await tenant_b_hr.post(
            f"/api/v1/overtime/requests/{request_id}/verify", json={}
        )
        reject = await tenant_b_hr.post(
            f"/api/v1/overtime/requests/{request_id}/reject", json={}
        )
        foreign_filter = await tenant_b_hr.get(
            "/api/v1/overtime/requests", params={"employee_id": str(ACTOR)}
        )

    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert listed.json()["page"]["total_items"] == 0
    _assert_error(detail, 404, "overtime_request_not_found")
    _assert_error(approve, 404, "overtime_request_not_found")
    _assert_error(verify, 404, "overtime_request_not_found")
    _assert_error(reject, 404, "overtime_request_not_found")
    _assert_error(withdraw, 404, "overtime_request_not_found")
    _assert_error(foreign_filter, 404, "employee_not_found")
