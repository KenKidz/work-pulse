"""HTTP integration tests for the payroll query endpoints (T9).

Drives the real router → service → Postgres adapter. Run lifecycle setup uses
the service for calculation (the worker's call site, T7), then the query
endpoints are exercised over HTTP. Covered:

- run list/detail with period/status filters and the house clamp pagination;
- timesheets and payslips of one run for the company-wide view roles;
- own-payslip scoping for every role, including superseded payslips;
- foreign, unknown and cross-tenant payslips/runs are the same `404`;
- managers have no team payroll visibility; HR reads but never mutates (T6).
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.payroll.schemas import PayrollRunStatus
from app.domains.payroll.service import PayrollService
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

PERIOD = "2026-09"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
OTHER = UUID("00000000-0000-4000-8000-0000000000e2")
OTHER_USER = UUID("00000000-0000-4000-8000-000000000032")


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


async def _seed_employee(
    session: AsyncSession,
    refs: tuple[str, str, str],
    *,
    employee_id: UUID,
    code: str,
    email: str,
) -> None:
    await PostgresEmployeeRepository(session).add(
        TENANT_A,
        employee_id,
        employee_code=code,
        first_name="Person",
        last_name=code,
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
    """The org refs plus two employees (ACTOR links the default user)."""
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(
        session, refs, employee_id=ACTOR, code="EMP-00001", email="actor@example.test"
    )
    await _seed_employee(
        session, refs, employee_id=OTHER, code="EMP-00002", email="other@example.test"
    )


async def _create_salary(
    client: AsyncClient, *, employee_id: UUID = ACTOR, amount: str = "1000.00"
) -> None:
    response = await client.post(
        "/api/v1/payroll/salaries",
        json={
            "employee_id": str(employee_id),
            "amount": amount,
            "effective_from": "2026-01-01",
        },
    )
    assert response.status_code == 201, response.text


async def _calculate_and_finalize(client: AsyncClient, service: PayrollService) -> dict:
    """Create a run for the closed period, calculate it and finalize it."""
    created = await client.post("/api/v1/payroll/runs", json={"period": PERIOD})
    assert created.status_code == 201, created.text
    run = created.json()
    calculated = await service.calculate_run(run["id"], NOW)
    assert calculated.status is PayrollRunStatus.CALCULATED
    finalized = await client.post(f"/api/v1/payroll/runs/{run['id']}/finalize")
    assert finalized.status_code == 200, finalized.text
    return finalized.json()


async def _run_payslips(client: AsyncClient, run_id: str) -> list[dict]:
    response = await client.get(f"/api/v1/payroll/runs/{run_id}/payslips")
    assert response.status_code == 200, response.text
    return response.json()["items"]


# --- company-wide reads (HR+) ---------------------------------------------


async def test_run_queries_return_summary_timesheets_and_payslip_lines(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
    payroll_seams,
) -> None:
    _, attendance, leave_days = payroll_seams
    await _seed_world(client_factory, session)
    attendance.worked[(TENANT_A, PERIOD, ACTOR)] = Decimal("80.00")
    attendance.days[(TENANT_A, PERIOD, ACTOR)] = 10
    leave_days.days[(TENANT_A, PERIOD, ACTOR)] = 2

    async with client_factory() as admin:
        await _create_salary(admin)
        await _create_salary(admin, employee_id=OTHER, amount="900.00")
        await admin.post(
            "/api/v1/payroll/deductions",
            json={
                "code": "tax",
                "name": "Tax",
                "kind": "fixed_amount",
                "value": "25.50",
            },
        )
        run = await _calculate_and_finalize(admin, payroll_service)

        listed = await admin.get(
            f"/api/v1/payroll/runs?period={PERIOD}&status=finalized"
        )
        detail = await admin.get(f"/api/v1/payroll/runs/{run['id']}")
        timesheets = await admin.get(f"/api/v1/payroll/runs/{run['id']}/timesheets")
        payslips = await admin.get(f"/api/v1/payroll/runs/{run['id']}/payslips")

    assert listed.status_code == 200
    listed_body = listed.json()
    assert [item["id"] for item in listed_body["items"]] == [run["id"]]
    assert set(listed_body["page"]) == {
        "page",
        "page_size",
        "total_items",
        "total_pages",
    }

    assert detail.status_code == 200
    assert detail.json()["status"] == "finalized"
    assert detail.json()["totals"]["net"] == 1849.0  # 1974.50 + 874.50

    assert timesheets.status_code == 200
    by_employee = {row["employee_id"]: row for row in timesheets.json()["items"]}
    assert by_employee[str(ACTOR)]["worked_hours"] == 80.0
    assert by_employee[str(ACTOR)]["attendance_days"] == 10
    assert by_employee[str(ACTOR)]["leave_days"] == 2
    assert by_employee[str(OTHER)]["worked_hours"] == 0.0

    assert payslips.status_code == 200
    lines = payslips.json()["items"]
    assert {row["employee_id"] for row in lines} == {str(ACTOR), str(OTHER)}
    by_payslip = {row["employee_id"]: row for row in lines}
    assert by_payslip[str(ACTOR)]["net"] == 974.5  # 1000.00 − 25.50
    assert by_payslip[str(OTHER)]["net"] == 874.5  # 900.00 − 25.50
    for row in lines:
        assert row["status"] == "issued"
        assert row["deductions"] == [
            {
                "code": "tax",
                "name": "Tax",
                "kind": "fixed_amount",
                "value": 25.5,
                "amount": 25.5,
            }
        ]

    # HR has the same read standing (view-only, proven by T6's denied finalize).
    async with client_factory(identity(role=Role.HR)) as hr:
        hr_run = await hr.get(f"/api/v1/payroll/runs/{run['id']}")
        hr_timesheets = await hr.get(f"/api/v1/payroll/runs/{run['id']}/timesheets")
        hr_payslips = await hr.get(f"/api/v1/payroll/runs/{run['id']}/payslips")

    assert hr_run.status_code == 200
    assert hr_timesheets.status_code == 200
    assert hr_payslips.status_code == 200


# --- own payslips (every role) --------------------------------------------


async def test_my_payslips_are_own_only_and_superseded_remains_readable(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        first_run = await _calculate_and_finalize(admin, payroll_service)
        # A correction run for the same period supersedes the first on finalize.
        correction_run = await _calculate_and_finalize(admin, payroll_service)
        first_payslip = (await _run_payslips(admin, first_run["id"]))[0]
        correction_payslip = (await _run_payslips(admin, correction_run["id"]))[0]

    assert first_payslip["status"] == "superseded"
    assert correction_payslip["status"] == "issued"

    actor_identity = identity(role=Role.EMPLOYEE, employee_id=ACTOR)
    async with client_factory(actor_identity) as employee:
        mine = await employee.get("/api/v1/payroll/payslips")
        superseded = await employee.get(
            f"/api/v1/payroll/payslips/{first_payslip['id']}"
        )
        issued = await employee.get(
            f"/api/v1/payroll/payslips/{correction_payslip['id']}"
        )
        # The company-wide payroll workspace is not an employee surface.
        runs = await employee.get("/api/v1/payroll/runs")
        run_payslips = await employee.get(
            f"/api/v1/payroll/runs/{correction_run['id']}/payslips"
        )

    assert mine.status_code == 200
    items = mine.json()["items"]
    assert {row["status"] for row in items} == {"issued", "superseded"}
    assert {row["employee_id"] for row in items} == {str(ACTOR)}
    assert superseded.status_code == 200
    assert superseded.json()["status"] == "superseded"
    assert issued.status_code == 200
    assert issued.json()["net"] == 1000.0
    _assert_error(runs, 404, "payroll_run_not_found")
    _assert_error(run_payslips, 404, "payroll_run_not_found")

    # Another employee sees none of ACTOR's payslips and cannot read one by id.
    other_identity = identity(role=Role.EMPLOYEE, employee_id=OTHER, user_id=OTHER_USER)
    async with client_factory(other_identity) as other:
        other_list = await other.get("/api/v1/payroll/payslips")
        foreign = await other.get(
            f"/api/v1/payroll/payslips/{correction_payslip['id']}"
        )

    assert other_list.status_code == 200
    assert other_list.json()["items"] == []
    assert other_list.json()["page"]["total_items"] == 0
    _assert_error(foreign, 404, "payslip_not_found")

    # A manager has no team payroll visibility (not even a subordinate's slip).
    async with client_factory(
        identity(role=Role.MANAGER, employee_id=OTHER, user_id=OTHER_USER)
    ) as manager:
        manager_view = await manager.get(
            f"/api/v1/payroll/payslips/{correction_payslip['id']}"
        )
        manager_runs = await manager.get("/api/v1/payroll/runs")

    _assert_error(manager_view, 404, "payslip_not_found")
    _assert_error(manager_runs, 404, "payroll_run_not_found")

    # An account without an employee link owns nothing (empty, never someone
    # else's payslips).
    async with client_factory(
        identity(role=Role.ADMIN, employee_id=None, user_id=USER)
    ) as unlinked:
        empty = await unlinked.get("/api/v1/payroll/payslips")

    assert empty.status_code == 200
    assert empty.json()["items"] == []


async def test_provisional_payslips_are_review_material_not_employee_visible(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
) -> None:
    """A calculated run's draft payslips stay with HR/Finance until finalize."""
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        created = await admin.post("/api/v1/payroll/runs", json={"period": PERIOD})
        assert created.status_code == 201, created.text
        run = created.json()
        calculated = await payroll_service.calculate_run(run["id"], NOW)
        assert calculated.status is PayrollRunStatus.CALCULATED
        provisional = (await _run_payslips(admin, run["id"]))[0]
        assert provisional["status"] == "provisional"

    async with client_factory(
        identity(role=Role.EMPLOYEE, employee_id=ACTOR)
    ) as employee:
        mine = await employee.get("/api/v1/payroll/payslips")
        direct = await employee.get(f"/api/v1/payroll/payslips/{provisional['id']}")

    assert mine.status_code == 200
    assert mine.json()["items"] == []
    assert mine.json()["page"]["total_items"] == 0
    _assert_error(direct, 404, "payslip_not_found")

    # HR reviews the draft in the run workspace, by list and by id.
    async with client_factory(identity(role=Role.HR)) as hr:
        review = await hr.get(f"/api/v1/payroll/payslips/{provisional['id']}")

    assert review.status_code == 200
    assert review.json()["status"] == "provisional"


# --- isolation and pagination ---------------------------------------------


async def test_tenant_isolation_and_unknown_ids_are_not_leaked(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        run = await _calculate_and_finalize(admin, payroll_service)
        payslip = (await _run_payslips(admin, run["id"]))[0]

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        runs = await tenant_b.get("/api/v1/payroll/runs")
        detail = await tenant_b.get(f"/api/v1/payroll/runs/{run['id']}")
        timesheets = await tenant_b.get(f"/api/v1/payroll/runs/{run['id']}/timesheets")
        run_payslips = await tenant_b.get(f"/api/v1/payroll/runs/{run['id']}/payslips")
        payslip_response = await tenant_b.get(
            f"/api/v1/payroll/payslips/{payslip['id']}"
        )
        my_payslips = await tenant_b.get("/api/v1/payroll/payslips")

    assert runs.status_code == 200
    assert runs.json()["items"] == []
    _assert_error(detail, 404, "payroll_run_not_found")
    _assert_error(timesheets, 404, "payroll_run_not_found")
    _assert_error(run_payslips, 404, "payroll_run_not_found")
    _assert_error(payslip_response, 404, "payslip_not_found")
    assert my_payslips.status_code == 200
    assert my_payslips.json()["items"] == []


async def test_pagination_clamps_and_invalid_filters_are_422(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        await _calculate_and_finalize(admin, payroll_service)

        clamped_low = await admin.get("/api/v1/payroll/runs?page=0&page_size=0")
        clamped_high = await admin.get("/api/v1/payroll/runs?page_size=501")
        non_integer = await admin.get("/api/v1/payroll/runs?page=x")
        bad_period = await admin.get("/api/v1/payroll/runs?period=2026-9")
        bad_status = await admin.get("/api/v1/payroll/runs?status=nope")
        no_match = await admin.get("/api/v1/payroll/runs?status=queued")

    assert clamped_low.status_code == 200
    assert clamped_low.json()["page"]["page"] == 1
    assert clamped_low.json()["page"]["page_size"] == 1
    assert clamped_high.json()["page"]["page_size"] == 100
    _assert_error(non_integer, 422, "validation_error")
    _assert_error(bad_period, 422, "validation_error")
    _assert_error(bad_status, 422, "validation_error")
    assert no_match.status_code == 200
    assert no_match.json()["items"] == []
