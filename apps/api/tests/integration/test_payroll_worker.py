"""Integration tests for the payroll calculation worker (T8).

The worker loop is driven once per pass over the real Postgres repository in
fresh sessions — exactly how `workers/payroll/main.py` wires it. The
overtime/attendance/leave seam ports are deterministic fakes (ADR 0018), so a
job's inputs are controlled and no wall-clock time is read.

Pinned behavior (`.ai/features/payroll.md` → AC11):

- a queued run is picked up and ends `calculated` with its payslips, outbox
  envelope and audit entry committed in the unit-of-work transaction;
- a job failure persists `failed` with a stable, PII-free code and recovers
  through `recalculate`;
- a duplicate pass produces no second payslip set.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.payroll import models as payroll_orm
from app.domains.payroll.schemas import PayrollRun, PayrollRunStatus
from app.domains.payroll.service import PayrollService
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.engine import get_session_factory
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.outbox import PostgresEventOutbox
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.postgres.repositories.payroll import PostgresPayrollRepository
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    ClientFactory,
    create_org_refs,
    identity,
)
from tests.unit.payroll.fakes import (
    FakeAttendanceHoursPort,
    FakeLeaveDaysPort,
    FakeOvertimeHoursPort,
)
from workers.payroll.runner import JobUnit, PayrollJobUnit, PayrollRunWorker

PERIOD = "2026-09"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _utc_now() -> datetime:
    return NOW


class _BarrierUnit:
    """A unit that holds the poll open until both workers have listed the run.

    This forces the two workers to race on the same queued id, proving the
    state-guarded claim: both see it, exactly one calculates it.
    """

    def __init__(self, unit: PayrollJobUnit, barrier: asyncio.Barrier) -> None:
        self._unit = unit
        self._barrier = barrier

    async def queued_run_ids(self, limit: int) -> Sequence[UUID]:
        run_ids = await self._unit.queued_run_ids(limit)
        await self._barrier.wait()
        return run_ids

    async def calculate(self, run_id: UUID, now: datetime) -> PayrollRun:
        return await self._unit.calculate(run_id, now)


def _worker(
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
    *,
    barrier: asyncio.Barrier | None = None,
) -> PayrollRunWorker:
    """The real worker wiring, one fresh session per unit of work."""

    @asynccontextmanager
    async def unit() -> AsyncIterator[JobUnit]:
        async with get_session_factory()() as session:
            repo = PostgresPayrollRepository(session)
            job = PayrollJobUnit(
                repo=repo,
                service=PayrollService(
                    repo=repo,
                    audit=PostgresAuditWriter(session),
                    outbox=PostgresEventOutbox(session, clock=_utc_now),
                    overtime=overtime,
                    attendance=attendance,
                    leave_days=leave_days,
                ),
                commit=session.commit,
            )
            if barrier is None:
                yield job
            else:
                yield _BarrierUnit(job, barrier)

    return PayrollRunWorker(
        unit,
        batch_size=10,
        poll_seconds=0.01,
        backoff_base=0.01,
        backoff_max=0.02,
        clock=_utc_now,
    )


async def _seed_world(client_factory: ClientFactory, session: AsyncSession) -> None:
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


async def _queued_run(client: AsyncClient) -> dict:
    salary = await client.post(
        "/api/v1/payroll/salaries",
        json={
            "employee_id": str(ACTOR),
            "amount": "1000.00",
            "effective_from": "2026-01-01",
        },
    )
    assert salary.status_code == 201, salary.text
    created = await client.post("/api/v1/payroll/runs", json={"period": PERIOD})
    assert created.status_code == 201, created.text
    return created.json()


async def _count(session: AsyncSession, model: Any) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# --- AC11 -----------------------------------------------------------------


async def test_worker_calculates_a_queued_run_and_a_duplicate_pass_is_a_noop(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_overtime: FakeOvertimeHoursPort,
    payroll_attendance: FakeAttendanceHoursPort,
    payroll_leave_days: FakeLeaveDaysPort,
) -> None:
    await _seed_world(client_factory, session)
    payroll_attendance.worked[(TENANT_A, PERIOD, ACTOR)] = Decimal("80.00")
    payroll_attendance.days[(TENANT_A, PERIOD, ACTOR)] = 10
    async with client_factory() as admin:
        run = await _queued_run(admin)

    # The worker opens its own sessions: setup must be committed first.
    await session.commit()

    worker = _worker(payroll_overtime, payroll_attendance, payroll_leave_days)
    assert await worker.run_once() == 1

    run_id = UUID(run["id"])
    async with get_session_factory()() as fresh:
        run_row = (
            await fresh.execute(
                select(payroll_orm.PayrollRun).where(
                    payroll_orm.PayrollRun.id == run_id
                )
            )
        ).scalar_one()
        assert run_row.status == PayrollRunStatus.CALCULATED.value
        assert run_row.employee_count == 1
        assert await _count(fresh, payroll_orm.Payslip) == 1
        timesheet = (
            await fresh.execute(
                select(payroll_orm.PayrollTimesheet).where(
                    payroll_orm.PayrollTimesheet.run_id == run_id
                )
            )
        ).scalar_one()
        assert timesheet.worked_hours == Decimal("80.00")
        events = list(
            (
                await fresh.execute(
                    select(EventOutboxRow).where(
                        EventOutboxRow.event_type == "payroll.calculated"
                    )
                )
            ).scalars()
        )
        assert len(events) == 1

    # A duplicate/re-delivered pass sees no queued run and writes nothing.
    assert await worker.run_once() == 0
    async with get_session_factory()() as fresh:
        assert await _count(fresh, payroll_orm.Payslip) == 1
        assert (
            await fresh.execute(select(func.count()).select_from(EventOutboxRow))
        ).scalar_one() == 1


async def test_concurrent_workers_claim_a_queued_run_exactly_once(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_overtime: FakeOvertimeHoursPort,
    payroll_attendance: FakeAttendanceHoursPort,
    payroll_leave_days: FakeLeaveDaysPort,
) -> None:
    """Two workers see the same queued id; only the claim winner calculates."""
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        run = await _queued_run(admin)
    await session.commit()

    barrier = asyncio.Barrier(2)
    first = _worker(
        payroll_overtime, payroll_attendance, payroll_leave_days, barrier=barrier
    )
    second = _worker(
        payroll_overtime, payroll_attendance, payroll_leave_days, barrier=barrier
    )
    results = await asyncio.gather(first.run_once(), second.run_once())

    assert results == [1, 1]
    run_id = UUID(run["id"])
    async with get_session_factory()() as fresh:
        assert await _count(fresh, payroll_orm.Payslip) == 1
        assert (
            await fresh.execute(select(func.count()).select_from(EventOutboxRow))
        ).scalar_one() == 1
        run_row = (
            await fresh.execute(
                select(payroll_orm.PayrollRun).where(
                    payroll_orm.PayrollRun.id == run_id
                )
            )
        ).scalar_one()
        assert run_row.status == PayrollRunStatus.CALCULATED.value


# --- failure and recovery -------------------------------------------------


async def test_worker_persists_a_stable_failure_and_recalculate_recovers(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_overtime: FakeOvertimeHoursPort,
    payroll_attendance: FakeAttendanceHoursPort,
    payroll_leave_days: FakeLeaveDaysPort,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        run = await _queued_run(admin)
    await session.commit()

    payroll_overtime.error = RuntimeError("seam unavailable")
    worker = _worker(payroll_overtime, payroll_attendance, payroll_leave_days)
    assert await worker.run_once() == 1

    run_id = UUID(run["id"])
    async with get_session_factory()() as fresh:
        run_row = (
            await fresh.execute(
                select(payroll_orm.PayrollRun).where(
                    payroll_orm.PayrollRun.id == run_id
                )
            )
        ).scalar_one()
        assert run_row.status == PayrollRunStatus.FAILED.value
        assert run_row.failed_code == "payroll_calculation_failed"
        assert await _count(fresh, payroll_orm.Payslip) == 0

    # The failed run is recoverable through the same endpoint an operator uses.
    payroll_overtime.error = None
    async with get_session_factory()() as fresh:
        service = PayrollService(
            repo=PostgresPayrollRepository(fresh),
            audit=PostgresAuditWriter(fresh),
            outbox=PostgresEventOutbox(fresh, clock=_utc_now),
            overtime=payroll_overtime,
            attendance=payroll_attendance,
            leave_days=payroll_leave_days,
        )
        queued = await service.recalculate_run(identity(), run_id, NOW)
        assert queued.status is PayrollRunStatus.QUEUED
        await fresh.commit()

    assert await worker.run_once() == 1
    async with get_session_factory()() as fresh:
        run_row = (
            await fresh.execute(
                select(payroll_orm.PayrollRun).where(
                    payroll_orm.PayrollRun.id == run_id
                )
            )
        ).scalar_one()
        assert run_row.status == PayrollRunStatus.CALCULATED.value
        assert run_row.failed_code is None
        assert await _count(fresh, payroll_orm.Payslip) == 1
