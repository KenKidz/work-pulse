"""The ADR 0018 overtime seam: real adapter, guard and payroll integration (T6).

`PostgresOvertimeHoursPort` is payroll's only window into overtime rows. These
tests prove, against real PostgreSQL:

1. `verified_hours` returns exactly the month's `hr_verified` rows, excluding
   included ones and out-of-month rows;
2. `mark_included` flips exactly those rows and stores the run handle;
3. a second flip under a different `run_id` counts 0; a same-run replay is an
   idempotent success (the at-most-one-finalized-run guard);
4. with zero verified rows payroll's overtime term stays `0.00`;
5. verified rows reach the timesheet's `overtime_hours` and finalize flips
   them through the real service and real adapter;
6. a row un-verified after calculation makes finalize roll back with
   `PayrollRunStaleError` (ADR 0018 decision 4).
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.overtime import models as overtime_orm
from app.domains.overtime.schemas import OvertimeRequestStatus
from app.domains.payroll import models as payroll_orm
from app.domains.payroll.exceptions import PayrollRunStaleError
from app.domains.payroll.service import PayrollService
from app.infrastructure.postgres import payroll_facts
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.engine import get_session_factory
from app.infrastructure.postgres.outbox import PostgresEventOutbox
from app.infrastructure.postgres.payroll_facts import PostgresOvertimeHoursPort
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.postgres.repositories.payroll import (
    PostgresPayrollRepository,
)
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    ClientFactory,
    create_org_refs,
    identity,
)
from tests.unit.payroll.fakes import FakeAttendanceHoursPort, FakeLeaveDaysPort

PERIOD = "2026-09"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
WORK_DATE = date(2026, 9, 15)
RUN_ID = UUID("00000000-0000-4000-8000-0000000000e1")
OTHER_RUN_ID = UUID("00000000-0000-4000-8000-0000000000e2")
OVERTIME_A = UUID("00000000-0000-4000-8000-0000000000a1")
OVERTIME_B = UUID("00000000-0000-4000-8000-0000000000a2")


async def _seed_world(
    client_factory: ClientFactory, session: AsyncSession
) -> tuple[str, str, str]:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
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
    return refs


async def _add_overtime(
    session: AsyncSession,
    overtime_id: UUID,
    *,
    status: OvertimeRequestStatus = OvertimeRequestStatus.HR_VERIFIED,
    work_date: date = WORK_DATE,
    hours: str = "5.00",
    employee_id: UUID = ACTOR,
    tenant_id: UUID = TENANT_A,
    included_in_run_id: UUID | None = None,
) -> None:
    session.add(
        overtime_orm.OvertimeRequest(
            id=overtime_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            work_date=work_date,
            hours=Decimal(hours),
            status=status.value,
            included_in_run_id=included_in_run_id,
        )
    )
    await session.flush()


def _real_overtime(session: AsyncSession) -> PostgresOvertimeHoursPort:
    return PostgresOvertimeHoursPort(session)


def _real_payroll_service(
    session: AsyncSession,
    *,
    overtime: PostgresOvertimeHoursPort | None = None,
) -> PayrollService:
    return PayrollService(
        repo=PostgresPayrollRepository(session),
        audit=PostgresAuditWriter(session),
        outbox=PostgresEventOutbox(session, clock=lambda: NOW),
        overtime=overtime or PostgresOvertimeHoursPort(session),
        attendance=FakeAttendanceHoursPort(),
        leave_days=FakeLeaveDaysPort(),
    )


async def _create_salary_and_run(client: AsyncClient) -> str:
    salary = await client.post(
        "/api/v1/payroll/salaries",
        json={
            "employee_id": str(ACTOR),
            "amount": "1000.00",
            "effective_from": "2026-01-01",
        },
    )
    assert salary.status_code == 201, salary.text
    run = await client.post("/api/v1/payroll/runs", json={"period": PERIOD})
    assert run.status_code == 201, run.text
    return run.json()["id"]


async def _timesheet(
    session: AsyncSession, run_id: UUID
) -> payroll_orm.PayrollTimesheet:
    row = (
        await session.execute(
            select(payroll_orm.PayrollTimesheet).where(
                payroll_orm.PayrollTimesheet.tenant_id == TENANT_A,
                payroll_orm.PayrollTimesheet.run_id == run_id,
            )
        )
    ).scalar_one()
    return row


async def test_verified_hours_returns_only_the_months_verified_rows(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    await _seed_world(client_factory, session)
    await _add_overtime(session, OVERTIME_A, work_date=date(2026, 9, 15))
    await _add_overtime(
        session,
        OVERTIME_B,
        work_date=date(2026, 9, 16),
        hours="2.50",
    )
    await _add_overtime(
        session,
        UUID("00000000-0000-4000-8000-0000000000a3"),
        work_date=date(2026, 10, 1),  # outside the period
    )
    await _add_overtime(
        session,
        UUID("00000000-0000-4000-8000-0000000000a4"),
        work_date=date(2026, 9, 1),
        status=OvertimeRequestStatus.INCLUDED_IN_PAYROLL,
        included_in_run_id=RUN_ID,
    )
    await _add_overtime(
        session,
        UUID("00000000-0000-4000-8000-0000000000a5"),
        work_date=date(2026, 9, 2),
        status=OvertimeRequestStatus.REJECTED,
    )

    rows = await _real_overtime(session).verified_hours(TENANT_A, PERIOD)

    assert [row.overtime_id for row in rows] == [OVERTIME_A, OVERTIME_B]
    assert [row.employee_id for row in rows] == [ACTOR, ACTOR]
    assert [row.hours for row in rows] == [Decimal("5.00"), Decimal("2.50")]


async def test_mark_included_flips_exactly_the_rows_and_stores_the_run(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    await _seed_world(client_factory, session)
    await _add_overtime(session, OVERTIME_A)
    await _add_overtime(session, OVERTIME_B, work_date=date(2026, 9, 16), hours="2.50")
    port = _real_overtime(session)

    rows = await port.verified_hours(TENANT_A, PERIOD)
    assert await port.mark_included(TENANT_A, rows, RUN_ID) == 2

    stored = {
        row.id: row
        for row in (
            await session.execute(
                select(overtime_orm.OvertimeRequest).where(
                    overtime_orm.OvertimeRequest.tenant_id == TENANT_A
                )
            )
        ).scalars()
    }
    assert stored[OVERTIME_A].status == "included_in_payroll"
    assert stored[OVERTIME_A].included_in_run_id == RUN_ID
    assert stored[OVERTIME_B].status == "included_in_payroll"
    assert stored[OVERTIME_B].included_in_run_id == RUN_ID
    assert await port.verified_hours(TENANT_A, PERIOD) == []


async def test_a_row_is_counted_by_at_most_one_finalized_run(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    await _seed_world(client_factory, session)
    await _add_overtime(session, OVERTIME_A)
    port = _real_overtime(session)
    row = (await port.verified_hours(TENANT_A, PERIOD))[0]

    assert await port.mark_included(TENANT_A, [row], RUN_ID) == 1
    # A different finalized run cannot count the row again.
    assert await port.mark_included(TENANT_A, [row], OTHER_RUN_ID) == 0
    # A same-run replay is idempotent: no state change, still a success.
    assert await port.mark_included(TENANT_A, [row], RUN_ID) == 1

    stored = (
        await session.execute(
            select(overtime_orm.OvertimeRequest).where(
                overtime_orm.OvertimeRequest.id == OVERTIME_A
            )
        )
    ).scalar_one()
    assert stored.status == "included_in_payroll"
    assert stored.included_in_run_id == RUN_ID


async def test_mark_included_chunks_large_handle_sets(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run larger than one bind-parameter batch flips and replays in chunks."""
    monkeypatch.setattr(payroll_facts, "_OVERTIME_FLIP_CHUNK", 2)
    await _seed_world(client_factory, session)
    overtime_ids = [
        UUID(f"00000000-0000-4000-8000-0000000000b{index}") for index in range(1, 6)
    ]
    for index, overtime_id in enumerate(overtime_ids):
        await _add_overtime(
            session,
            overtime_id,
            work_date=date(2026, 9, index + 1),
        )

    port = _real_overtime(session)
    rows = await port.verified_hours(TENANT_A, PERIOD)
    assert len(rows) == 5
    assert await port.mark_included(TENANT_A, rows, RUN_ID) == 5
    # The same-run replay counts every row again, across the same chunks.
    assert await port.mark_included(TENANT_A, rows, RUN_ID) == 5
    assert await port.verified_hours(TENANT_A, PERIOD) == []


async def test_zero_verified_rows_keep_the_payroll_overtime_term_at_zero(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        run_id = UUID(await _create_salary_and_run(admin))

    calculated = await _real_payroll_service(session).calculate_run(run_id, NOW)
    assert calculated.status.value == "calculated"

    timesheet = await _timesheet(session, run_id)
    assert timesheet.overtime_hours == Decimal("0.00")
    assert timesheet.overtime_rows == []
    assert await _real_overtime(session).verified_hours(TENANT_A, PERIOD) == []


async def test_verified_rows_reach_the_timesheet_and_finalize_flips_them(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    await _seed_world(client_factory, session)
    await _add_overtime(session, OVERTIME_A, hours="5.00")
    async with client_factory() as admin:
        run_id = UUID(await _create_salary_and_run(admin))

    calculated = await _real_payroll_service(session).calculate_run(run_id, NOW)
    assert calculated.status.value == "calculated"

    timesheet = await _timesheet(session, run_id)
    assert timesheet.overtime_hours == Decimal("5.00")
    assert timesheet.overtime_rows == [
        {
            "overtime_id": str(OVERTIME_A),
            "employee_id": str(ACTOR),
            "hours": "5.00",
        }
    ]

    async with client_factory() as admin:
        response = await admin.post(f"/api/v1/payroll/runs/{run_id}/finalize")
    assert response.status_code == 200, response.text

    stored = (
        await session.execute(
            select(overtime_orm.OvertimeRequest).where(
                overtime_orm.OvertimeRequest.id == OVERTIME_A
            )
        )
    ).scalar_one()
    assert stored.status == "included_in_payroll"
    assert stored.included_in_run_id == run_id
    assert await _real_overtime(session).verified_hours(TENANT_A, PERIOD) == []


async def test_unverifying_after_calculation_rolls_the_finalize_back(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    await _seed_world(client_factory, session)
    await _add_overtime(session, OVERTIME_A, hours="5.00")
    async with client_factory() as admin:
        run_id = UUID(await _create_salary_and_run(admin))

    await _real_payroll_service(session).calculate_run(run_id, NOW)
    # HR un-verifies (rejects) the counted row after the calculation.
    await session.execute(
        update(overtime_orm.OvertimeRequest)
        .where(overtime_orm.OvertimeRequest.id == OVERTIME_A)
        .values(status=OvertimeRequestStatus.REJECTED.value)
    )
    await session.commit()

    session_factory = get_session_factory()
    async with session_factory() as fresh:
        service = _real_payroll_service(
            fresh,
            overtime=PostgresOvertimeHoursPort(fresh),
        )
        with pytest.raises(PayrollRunStaleError):
            await service.finalize_run(identity(), run_id, NOW)
        await fresh.rollback()

    async with session_factory() as check:
        run = (
            await check.execute(
                select(payroll_orm.PayrollRun).where(
                    payroll_orm.PayrollRun.id == run_id
                )
            )
        ).scalar_one()
        assert run.status == "calculated"
        overtime = (
            await check.execute(
                select(overtime_orm.OvertimeRequest).where(
                    overtime_orm.OvertimeRequest.id == OVERTIME_A
                )
            )
        ).scalar_one()
        assert overtime.status == "rejected"
        assert overtime.included_in_run_id is None
