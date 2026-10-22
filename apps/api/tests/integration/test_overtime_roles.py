"""Overtime role matrix at the HTTP edge (T5, `.ai/features/overtime.md`).

Requesting/viewing/withdrawing are own-only; approving is the manager step
(manager team scope, hr/admin/super_admin), verifying is the HR step
(hr/admin/super_admin only). An out-of-scope request is a `404` (existence is
not leaked); a visible request with a forbidden transition is a `403`.
Self-decision is always a `403`, whatever the decider's role. Reject uses the
manager-step gate from `requested` and the HR-step gate from
`manager_approved`/`hr_verified`.
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
    ClientFactory,
    create_org_refs,
    identity,
)

CLOCK = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
MANAGER = UUID("00000000-0000-4000-8000-0000000000d1")
REPORT = UUID("00000000-0000-4000-8000-0000000000d2")
HR_EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000d3")
OUTSIDER = UUID("00000000-0000-4000-8000-0000000000d4")


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
        session,
        refs,
        employee_id=MANAGER,
        code="EMP-00010",
        email="manager@example.test",
    )
    await _seed_employee(
        session,
        refs,
        employee_id=ACTOR,
        code="EMP-00009",
        email="self@example.test",
        manager_id=MANAGER,
    )
    await _seed_employee(
        session,
        refs,
        employee_id=REPORT,
        code="EMP-00011",
        email="report@example.test",
        manager_id=MANAGER,
    )
    await _seed_employee(
        session,
        refs,
        employee_id=HR_EMPLOYEE,
        code="EMP-00012",
        email="hr@example.test",
    )
    await _seed_employee(
        session,
        refs,
        employee_id=OUTSIDER,
        code="EMP-00013",
        email="outsider@example.test",
    )


async def _submit(
    client: AsyncClient, *, work_date: date = date(2026, 10, 21)
) -> Response:
    return await client.post(
        "/api/v1/overtime/requests",
        json={"work_date": work_date.isoformat(), "hours": "2.50"},
    )


async def test_employee_and_finance_have_no_decide_capability(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        request_id = (await _submit(employee)).json()["id"]

    for role in (Role.EMPLOYEE, Role.FINANCE):
        async with client_factory(identity(role=role, employee_id=REPORT)) as client:
            approve = await client.post(
                f"/api/v1/overtime/requests/{request_id}/approve", json={}
            )
            verify = await client.post(
                f"/api/v1/overtime/requests/{request_id}/verify", json={}
            )
            reject = await client.post(
                f"/api/v1/overtime/requests/{request_id}/reject", json={}
            )
        _assert_error(approve, 403, "forbidden")
        _assert_error(verify, 403, "forbidden")
        _assert_error(reject, 403, "forbidden")

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        detail = await employee.get(f"/api/v1/overtime/requests/{request_id}")
    assert detail.json()["status"] == "requested"  # denied transitions changed nothing


@pytest.mark.parametrize("role", [Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
async def test_self_decision_is_forbidden_for_every_decider_role(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        request_id = (await _submit(employee)).json()["id"]

    async with client_factory(identity(role=role, employee_id=ACTOR)) as decider:
        approve = await decider.post(
            f"/api/v1/overtime/requests/{request_id}/approve", json={}
        )
        reject = await decider.post(
            f"/api/v1/overtime/requests/{request_id}/reject", json={}
        )
    _assert_error(approve, 403, "forbidden")
    _assert_error(reject, 403, "forbidden")

    # A non-requester HR verifies, then the requester's HR account may not
    # verify (or reject) their own row at the HR step either.
    async with client_factory(identity(role=Role.HR, employee_id=HR_EMPLOYEE)) as hr:
        assert (
            await hr.post(f"/api/v1/overtime/requests/{request_id}/approve", json={})
        ).status_code == 200
    async with client_factory(identity(role=role, employee_id=ACTOR)) as decider:
        verify = await decider.post(
            f"/api/v1/overtime/requests/{request_id}/verify", json={}
        )
        reject = await decider.post(
            f"/api/v1/overtime/requests/{request_id}/reject", json={}
        )
    _assert_error(verify, 403, "forbidden")
    _assert_error(reject, 403, "forbidden")


async def test_manager_without_an_hr_role_cannot_verify(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        request_id = (await _submit(employee)).json()["id"]

    async with client_factory(identity(role=Role.MANAGER, employee_id=MANAGER)) as m:
        approved = await m.post(
            f"/api/v1/overtime/requests/{request_id}/approve", json={}
        )
        verify = await m.post(f"/api/v1/overtime/requests/{request_id}/verify", json={})

    assert approved.status_code == 200
    assert approved.json()["status"] == "manager_approved"
    _assert_error(verify, 403, "forbidden")


async def test_manager_scope_is_self_plus_direct_reports(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE, employee_id=REPORT)) as r:
        report_request = (await _submit(r, work_date=date(2026, 10, 20))).json()["id"]
    async with client_factory(
        identity(role=Role.EMPLOYEE, employee_id=OUTSIDER)
    ) as outsider:
        outsider_request = (
            await _submit(outsider, work_date=date(2026, 10, 21))
        ).json()["id"]

    async with client_factory(identity(role=Role.MANAGER, employee_id=MANAGER)) as m:
        approved = await m.post(
            f"/api/v1/overtime/requests/{report_request}/approve", json={}
        )
        foreign_detail = await m.get(f"/api/v1/overtime/requests/{outsider_request}")
        foreign_approve = await m.post(
            f"/api/v1/overtime/requests/{outsider_request}/approve", json={}
        )
        foreign_withdraw = await m.post(
            f"/api/v1/overtime/requests/{outsider_request}/withdraw"
        )
        foreign_filter = await m.get(
            "/api/v1/overtime/requests", params={"employee_id": str(OUTSIDER)}
        )
        listed = await m.get("/api/v1/overtime/requests")

    assert approved.status_code == 200
    assert approved.json()["status"] == "manager_approved"
    _assert_error(foreign_detail, 404, "overtime_request_not_found")
    _assert_error(foreign_approve, 404, "overtime_request_not_found")
    _assert_error(foreign_withdraw, 404, "overtime_request_not_found")
    _assert_error(foreign_filter, 404, "employee_not_found")
    assert [item["id"] for item in listed.json()["items"]] == [report_request]


async def test_reject_role_gate_depends_on_the_source_state(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        from_requested = (await _submit(employee, work_date=date(2026, 10, 19))).json()[
            "id"
        ]
        from_manager_approved = (
            await _submit(employee, work_date=date(2026, 10, 20))
        ).json()["id"]
        from_hr_verified = (
            await _submit(employee, work_date=date(2026, 10, 21))
        ).json()["id"]

    async with client_factory(identity(role=Role.MANAGER, employee_id=MANAGER)) as m:
        assert (
            await m.post(f"/api/v1/overtime/requests/{from_requested}/reject", json={})
        ).status_code == 200
        assert (
            await m.post(
                f"/api/v1/overtime/requests/{from_manager_approved}/approve", json={}
            )
        ).status_code == 200
        manager_reject = await m.post(
            f"/api/v1/overtime/requests/{from_manager_approved}/reject", json={}
        )

    _assert_error(
        manager_reject, 403, "forbidden"
    )  # manager step cannot reject at HR step

    async with client_factory(identity(role=Role.HR, employee_id=HR_EMPLOYEE)) as hr:
        rejected = await hr.post(
            f"/api/v1/overtime/requests/{from_manager_approved}/reject", json={}
        )
        assert (
            await hr.post(
                f"/api/v1/overtime/requests/{from_hr_verified}/approve", json={}
            )
        ).status_code == 200
        assert (
            await hr.post(
                f"/api/v1/overtime/requests/{from_hr_verified}/verify", json={}
            )
        ).status_code == 200
        unverified = await hr.post(
            f"/api/v1/overtime/requests/{from_hr_verified}/reject", json={}
        )

    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["rejected_from"] == "manager_approved"
    assert unverified.json()["status"] == "rejected"
    assert unverified.json()["rejected_from"] == "hr_verified"


async def test_visible_non_owner_cannot_withdraw(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        request_id = (await _submit(employee)).json()["id"]

    async with client_factory(identity(role=Role.HR, employee_id=HR_EMPLOYEE)) as hr:
        response = await hr.post(f"/api/v1/overtime/requests/{request_id}/withdraw")

    _assert_error(response, 403, "forbidden")
