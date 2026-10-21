"""Integration tests for the payroll calculation job (T7).

The API creates the run; the application service (the worker's eventual call
site) drives `QUEUED → CALCULATING → CALCULATED | FAILED` over the real
Postgres repository. The attendance/overtime/leave inputs come from the
deterministic seam fakes (ADR 0018 decision 7).

Pinned behavior (`.ai/features/payroll.md` → AC1/AC3/AC6/AC10):

- the formula and line-level rounding produce cent-exact payslips;
- run totals equal the sum of payslip lines;
- timesheets carry the assembled worked/overtime/leave facts;
- recalculation replaces the provisional set and unchanged inputs reproduce
  the same payslip lines (determinism);
- `no_salary` employees are skipped without blocking finalize;
- a failed job persists a stable code and recovers through recalculate;
- a duplicate job pass never creates a second payslip set.
"""

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from httpx import AsyncClient
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.payroll import models as payroll_orm
from app.domains.payroll.schemas import (
    PayrollRunSkipReason,
    PayrollRunStatus,
)
from app.domains.payroll.service import PayrollService
from app.infrastructure.postgres.models_outbox import EventOutboxRow
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

PERIOD = "2026-09"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
OTHER = uuid.UUID("00000000-0000-4000-8000-0000000000e2")
OVERTIME_ID = uuid.UUID("00000000-0000-4000-8000-0000000000f1")

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMAS_DIR = REPO_ROOT / "contracts" / "events" / "payroll"


async def _seed_actor(session: AsyncSession, refs: tuple[str, str, str]) -> None:
    await PostgresEmployeeRepository(session).add(
        TENANT_A,
        ACTOR,
        employee_code="EMP-00009",
        first_name="Actor",
        last_name="Self",
        email="self@example.test",
        phone=None,
        department_id=uuid.UUID(refs[0]),
        position_id=uuid.UUID(refs[1]),
        location_id=uuid.UUID(refs[2]),
        manager_id=None,
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        hired_at=date(2026, 1, 5),
    )


async def _seed_other(session: AsyncSession, refs: tuple[str, str, str]) -> None:
    await PostgresEmployeeRepository(session).add(
        TENANT_A,
        OTHER,
        employee_code="EMP-00010",
        first_name="Other",
        last_name="Person",
        email="other@example.test",
        phone=None,
        department_id=uuid.UUID(refs[0]),
        position_id=uuid.UUID(refs[1]),
        location_id=uuid.UUID(refs[2]),
        manager_id=None,
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        hired_at=date(2026, 1, 5),
    )


async def _seed_world(client_factory: ClientFactory, session: AsyncSession) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_actor(session, refs)


async def _create_salary(
    client: AsyncClient,
    *,
    amount: str = "1000.00",
    effective_from: str = "2026-01-01",
) -> None:
    response = await client.post(
        "/api/v1/payroll/salaries",
        json={
            "employee_id": str(ACTOR),
            "amount": amount,
            "effective_from": effective_from,
        },
    )
    assert response.status_code == 201, response.text


async def _create_deduction(
    client: AsyncClient,
    *,
    code: str,
    value: str,
    kind: str = "fixed_amount",
    active: bool = True,
) -> None:
    response = await client.post(
        "/api/v1/payroll/deductions",
        json={
            "code": code,
            "name": code.title(),
            "kind": kind,
            "value": value,
            "active": active,
        },
    )
    assert response.status_code == 201, response.text


async def _create_run(client: AsyncClient) -> dict:
    response = await client.post("/api/v1/payroll/runs", json={"period": PERIOD})
    assert response.status_code == 201, response.text
    return response.json()


async def _payslips(session: AsyncSession, run_id: uuid.UUID):
    return list(
        (
            await session.execute(
                select(payroll_orm.Payslip)
                .where(
                    payroll_orm.Payslip.tenant_id == TENANT_A,
                    payroll_orm.Payslip.run_id == run_id,
                )
                .order_by(payroll_orm.Payslip.employee_id)
            )
        ).scalars()
    )


async def _deduction_lines(
    session: AsyncSession, payslip_ids: list[uuid.UUID]
) -> list[payroll_orm.PayslipDeductionLine]:
    return list(
        (
            await session.execute(
                select(payroll_orm.PayslipDeductionLine)
                .where(
                    payroll_orm.PayslipDeductionLine.tenant_id == TENANT_A,
                    payroll_orm.PayslipDeductionLine.payslip_id.in_(payslip_ids),
                )
                .order_by(
                    payroll_orm.PayslipDeductionLine.payslip_id,
                    payroll_orm.PayslipDeductionLine.code,
                )
            )
        ).scalars()
    )


async def _snapshot(session: AsyncSession, run_id: uuid.UUID) -> list[dict]:
    """The deterministic part of a payslip set: amounts and ordered lines."""
    payslips = await _payslips(session, run_id)
    lines = await _deduction_lines(session, [row.id for row in payslips])
    by_payslip: dict[uuid.UUID, list[dict]] = {row.id: [] for row in payslips}
    for line in lines:
        by_payslip[line.payslip_id].append(
            {
                "code": line.code,
                "name": line.name,
                "kind": line.kind,
                "value": str(line.value),
                "amount": str(line.amount),
            }
        )
    return [
        {
            "employee_id": str(row.employee_id),
            "period": row.period,
            "status": row.status,
            "base": str(row.base),
            "overtime_pay": str(row.overtime_pay),
            "bonuses": str(row.bonuses),
            "net": str(row.net),
            "deductions": by_payslip[row.id],
        }
        for row in payslips
    ]


# --- AC1 / AC3 / AC10 -----------------------------------------------------


async def test_calculation_is_deterministic_and_totals_match_the_lines(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
    payroll_overtime,
    payroll_attendance,
    payroll_leave_days,
) -> None:
    await _seed_world(client_factory, session)
    payroll_overtime.add(
        TENANT_A,
        PERIOD,
        overtime_id=OVERTIME_ID,
        employee_id=ACTOR,
        hours=Decimal("5.00"),
    )
    payroll_attendance.worked[(TENANT_A, PERIOD, ACTOR)] = Decimal("80.00")
    payroll_attendance.days[(TENANT_A, PERIOD, ACTOR)] = 10
    payroll_leave_days.days[(TENANT_A, PERIOD, ACTOR)] = 2

    async with client_factory() as admin:
        await _create_salary(admin)
        await _create_deduction(admin, code="tax", kind="percent_of_base", value="10")
        await _create_deduction(admin, code="benefit", value="25.50")
        run = await _create_run(admin)
        calculated = await payroll_service.calculate_run(run["id"], NOW)

    assert calculated.status is PayrollRunStatus.CALCULATED
    assert calculated.employee_count == 1
    assert calculated.skipped_count == 0
    assert calculated.totals is not None

    payslips = await _payslips(session, uuid.UUID(run["id"]))
    assert len(payslips) == 1
    payslip = payslips[0]
    assert payslip.base == Decimal("1000.00")
    assert payslip.overtime_pay == Decimal("46.88")  # 5 × (1000/160) × 1.50
    assert payslip.bonuses == Decimal("0.00")
    assert payslip.net == Decimal("921.38")  # 1000 + 46.88 − 100 − 25.50

    lines = await _deduction_lines(session, [payslip.id])
    assert [(line.code, line.amount) for line in lines] == [
        ("benefit", Decimal("25.50")),
        ("tax", Decimal("100.00")),
    ]

    totals = calculated.totals
    assert totals.base == payslip.base
    assert totals.overtime_pay == payslip.overtime_pay
    assert totals.bonuses == payslip.bonuses
    assert totals.deductions == sum((line.amount for line in lines), Decimal())
    assert totals.net == payslip.net

    timesheet = (
        await session.execute(
            select(payroll_orm.PayrollTimesheet).where(
                payroll_orm.PayrollTimesheet.run_id == uuid.UUID(run["id"])
            )
        )
    ).scalar_one()
    assert timesheet.worked_hours == Decimal("80.00")
    assert timesheet.overtime_hours == Decimal("5.00")
    assert timesheet.leave_days == 2
    assert timesheet.attendance_days == 10

    # AC3: replacing the provisional set with unchanged inputs is byte-
    # identical on the deterministic fields; only persistence ids differ.
    before = await _snapshot(session, uuid.UUID(run["id"]))
    queued = await payroll_service.recalculate_run(
        identity(), uuid.UUID(run["id"]), NOW
    )
    assert queued.status is PayrollRunStatus.QUEUED
    again = await payroll_service.calculate_run(run["id"], NOW)
    assert again.status is PayrollRunStatus.CALCULATED
    assert await _snapshot(session, uuid.UUID(run["id"])) == before

    event_rows = list(
        (
            await session.execute(
                select(EventOutboxRow).where(
                    EventOutboxRow.event_type == "payroll.calculated"
                )
            )
        ).scalars()
    )
    # Recalculation replaces the provisional set but emits no second
    # run-level event (`.ai/features/payroll.md` → Events).
    assert len(event_rows) == 1
    schema = json.loads((SCHEMAS_DIR / "payroll-calculated.v1.json").read_text())
    for row in event_rows:
        Draft202012Validator(schema).validate(row.payload["data"])
        assert row.payload["data"]["employee_count"] == 1


# --- AC6 ------------------------------------------------------------------


async def test_no_salary_employee_is_skipped_and_does_not_block_finalize(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_actor(session, refs)
    await _seed_other(session, refs)

    async with client_factory() as admin:
        await _create_salary(admin)
        run = await _create_run(admin)
        calculated = await payroll_service.calculate_run(run["id"], NOW)

    assert calculated.status is PayrollRunStatus.CALCULATED
    assert calculated.employee_count == 1
    assert calculated.skipped_count == 1
    assert calculated.skipped[0].employee_id == OTHER
    assert calculated.skipped[0].reason is PayrollRunSkipReason.NO_SALARY

    payslips = await _payslips(session, uuid.UUID(run["id"]))
    assert [row.employee_id for row in payslips] == [ACTOR]

    # Informational timesheets cover the whole scope, including the skip; the
    # skip's overtime handles stay empty so finalize cannot include its hours.
    timesheets = list(
        (
            await session.execute(
                select(payroll_orm.PayrollTimesheet)
                .where(payroll_orm.PayrollTimesheet.run_id == uuid.UUID(run["id"]))
                .order_by(payroll_orm.PayrollTimesheet.employee_id)
            )
        ).scalars()
    )
    assert [row.employee_id for row in timesheets] == [ACTOR, OTHER]
    assert all(row.overtime_rows == [] for row in timesheets)

    finalized = await payroll_service.finalize_run(
        identity(), uuid.UUID(run["id"]), NOW
    )
    assert finalized.status is PayrollRunStatus.FINALIZED


# --- failure and idempotency ---------------------------------------------


async def test_failed_job_persists_a_stable_code_and_recovers(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
    payroll_overtime,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        run = await _create_run(admin)

    payroll_overtime.error = RuntimeError("seam unavailable")
    failed = await payroll_service.calculate_run(run["id"], NOW)
    assert failed.status is PayrollRunStatus.FAILED
    assert failed.failed_code == "payroll_calculation_failed"

    payroll_overtime.error = None
    queued = await payroll_service.recalculate_run(
        identity(), uuid.UUID(run["id"]), NOW
    )
    assert queued.status is PayrollRunStatus.QUEUED

    calculated = await payroll_service.calculate_run(run["id"], NOW)
    assert calculated.status is PayrollRunStatus.CALCULATED
    assert len(await _payslips(session, uuid.UUID(run["id"]))) == 1


async def test_duplicate_job_pass_never_creates_a_second_payslip_set(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        run = await _create_run(admin)
        first = await payroll_service.calculate_run(run["id"], NOW)
        second = await payroll_service.calculate_run(run["id"], NOW)

    assert first.status is PayrollRunStatus.CALCULATED
    assert second.status is PayrollRunStatus.CALCULATED
    assert len(await _payslips(session, uuid.UUID(run["id"]))) == 1
    events = list(
        (
            await session.execute(
                select(EventOutboxRow).where(
                    EventOutboxRow.event_type == "payroll.calculated"
                )
            )
        ).scalars()
    )
    assert len(events) == 1


async def test_result_replacement_is_rejected_after_the_run_moves_on(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        run = await _create_run(admin)
        await payroll_service.calculate_run(run["id"], NOW)

    repo = PostgresPayrollRepository(session)
    replaced = await repo.replace_run_results(
        TENANT_A,
        uuid.UUID(run["id"]),
        timesheets=[],
        payslips=[],
        now=NOW,
    )

    assert replaced is False
    assert len(await _payslips(session, uuid.UUID(run["id"]))) == 1


# --- bonuses and rule inputs ---------------------------------------------


async def test_bonuses_are_summed_on_recalculation(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        run = await _create_run(admin)
        await payroll_service.calculate_run(run["id"], NOW)

        for amount in ("50.00", "25.50"):
            response = await admin.post(
                f"/api/v1/payroll/runs/{run['id']}/bonuses",
                json={"employee_id": str(ACTOR), "amount": amount},
            )
            assert response.status_code == 201, response.text

        await payroll_service.recalculate_run(identity(), uuid.UUID(run["id"]), NOW)
        calculated = await payroll_service.calculate_run(run["id"], NOW)

    payslip = (await _payslips(session, uuid.UUID(run["id"])))[0]
    assert payslip.bonuses == Decimal("75.50")
    assert calculated.totals is not None
    assert calculated.totals.bonuses == Decimal("75.50")


async def test_latest_effective_salary_wins_and_inactive_rules_are_absent(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin, amount="1000.00", effective_from="2026-01-01")
        await _create_salary(admin, amount="1200.00", effective_from="2026-09-15")
        # A record effective after the closed month never participates.
        await _create_salary(admin, amount="5000.00", effective_from="2026-10-01")
        await _create_deduction(admin, code="union", value="10.00")
        await _create_deduction(admin, code="parking", value="20.00", active=False)
        run = await _create_run(admin)
        await payroll_service.calculate_run(run["id"], NOW)

    payslip = (await _payslips(session, uuid.UUID(run["id"])))[0]
    assert payslip.base == Decimal("1200.00")
    lines = await _deduction_lines(session, [payslip.id])
    assert [line.code for line in lines] == ["union"]
    assert payslip.net == Decimal("1190.00")
