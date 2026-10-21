"""Unit tests for the payroll run lifecycle and calculation job (T6/T7).

Runs entirely on the in-memory fakes: no database, broker or clock. Covers the
pinned state machine, role gates, the overtime finalize flip, remaining/queued
idempotency and the `no_salary` skip. The deterministic formula itself is
pinned by `test_calculation.py`; these tests assert the application wiring.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.domains.payroll.exceptions import (
    PayrollEmployeeNotInRunError,
    PayrollPeriodNotClosedError,
    PayrollPeriodOpenError,
    PayrollRunFinalizedError,
    PayrollRunNotCalculatedError,
    PayrollRunNotFoundError,
)
from app.domains.payroll.schemas import (
    BonusCreate,
    PayrollRunCreate,
    PayrollRunSkipReason,
    PayrollRunStatus,
    PayslipStatus,
)
from app.domains.payroll.service import PayrollService
from app.shared.errors import ForbiddenError
from app.shared.identity import Role
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox
from tests.unit.payroll.conftest import (
    EMPLOYEE,
    NOW,
    OTHER,
    PERIOD,
    TENANT_A,
    TENANT_B,
    USER,
    identity,
)
from tests.unit.payroll.fakes import (
    FakeAttendanceHoursPort,
    FakeLeaveDaysPort,
    FakeOvertimeHoursPort,
    FakePayrollRepository,
)

OVERTIME_ID = UUID("00000000-0000-4000-8000-0000000000f1")


def service(
    repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> PayrollService:
    return PayrollService(
        repo=repo,
        audit=audit,
        outbox=outbox,
        overtime=overtime,
        attendance=attendance,
        leave_days=leave_days,
    )


async def _seed_inputs(
    svc: PayrollService,
    repo: FakePayrollRepository,
    *,
    employee_id: UUID = EMPLOYEE,
    amount: Decimal = Decimal("1000.00"),
) -> None:
    repo.add_employee(TENANT_A, employee_id)
    await svc.get_config(identity(), NOW)
    await repo.add_salary(
        TENANT_A,
        uuid4(),
        employee_id=employee_id,
        amount=amount,
        currency="USD",
        effective_from=date(2026, 1, 1),
        created_by=USER,
        now=NOW,
    )


async def _create_calculated_run(
    svc: PayrollService,
    repo: FakePayrollRepository,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> UUID:
    await _seed_inputs(svc, repo)
    attendance.worked[(TENANT_A, PERIOD, EMPLOYEE)] = Decimal("80.00")
    attendance.days[(TENANT_A, PERIOD, EMPLOYEE)] = 10
    leave_days.days[(TENANT_A, PERIOD, EMPLOYEE)] = 2
    overtime.add(
        TENANT_A,
        PERIOD,
        overtime_id=OVERTIME_ID,
        employee_id=EMPLOYEE,
        hours=Decimal("5.00"),
    )
    run = await svc.create_run(identity(), PayrollRunCreate(period=PERIOD), NOW)
    calculated = await svc.calculate_run(run.id, NOW)
    assert calculated.status is PayrollRunStatus.CALCULATED
    return run.id


def _service(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> PayrollService:
    return service(payroll_repo, audit, outbox, overtime, attendance, leave_days)


# --- create ---------------------------------------------------------------


async def test_create_run_requires_a_closed_month(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    with pytest.raises(PayrollPeriodNotClosedError):
        await svc.create_run(identity(), PayrollRunCreate(period="2026-10"), NOW)


async def test_create_run_queues_and_audits(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)

    run = await svc.create_run(identity(), PayrollRunCreate(period=PERIOD), NOW)

    assert run.status is PayrollRunStatus.QUEUED
    assert run.supersedes_run_id is None
    assert [entry.action for entry in audit.entries] == ["payroll.run_created"]


async def test_second_open_run_for_a_period_conflicts(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    await svc.create_run(identity(), PayrollRunCreate(period=PERIOD), NOW)

    with pytest.raises(PayrollPeriodOpenError):
        await svc.create_run(identity(), PayrollRunCreate(period=PERIOD), NOW)


# --- finalize -------------------------------------------------------------


async def test_finalize_requires_a_calculated_run(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    run = await svc.create_run(identity(), PayrollRunCreate(period=PERIOD), NOW)

    with pytest.raises(PayrollRunNotCalculatedError):
        await svc.finalize_run(identity(), run.id, NOW)


async def test_finalize_issues_payslips_and_flips_counted_overtime(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    run_id = await _create_calculated_run(
        svc, payroll_repo, overtime, attendance, leave_days
    )

    finalized = await svc.finalize_run(identity(), run_id, NOW)

    assert finalized.status is PayrollRunStatus.FINALIZED
    payslips = [
        row
        for (tenant_id, _), row in payroll_repo.payslips.items()
        if tenant_id == TENANT_A
    ]
    assert [row.status for row in payslips] == [PayslipStatus.ISSUED]
    assert overtime.included == {OVERTIME_ID}
    assert overtime.mark_calls == [(TENANT_A, (OVERTIME_ID,), run_id)]

    # One run-level calculate event plus one issued event per payslip; no
    # amounts anywhere in the payloads (`.ai/security.md`).
    assert [event["event_type"] for event in outbox.envelopes] == [
        "payroll.calculated",
        "payslip.issued",
    ]
    for envelope in outbox.envelopes:
        encoded = str(envelope)
        assert "amount" not in encoded
        assert "1000" not in encoded
        assert "1046.88" not in encoded

    with pytest.raises(PayrollRunFinalizedError):
        await svc.finalize_run(identity(), run_id, NOW)


async def test_finalize_is_denied_for_hr(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    run_id = await _create_calculated_run(
        svc, payroll_repo, overtime, attendance, leave_days
    )

    with pytest.raises(ForbiddenError):
        await svc.finalize_run(identity(role=Role.HR), run_id, NOW)


async def test_create_denied_for_employee_and_manager(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)

    for role in (Role.EMPLOYEE, Role.MANAGER):
        with pytest.raises(ForbiddenError):
            await svc.create_run(
                identity(role=role), PayrollRunCreate(period=PERIOD), NOW
            )


async def test_unknown_or_foreign_run_is_not_found(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    run = await svc.create_run(identity(), PayrollRunCreate(period=PERIOD), NOW)

    with pytest.raises(PayrollRunNotFoundError):
        await svc.finalize_run(identity(tenant_id=TENANT_B), run.id, NOW)
    with pytest.raises(PayrollRunNotFoundError):
        await svc.recalculate_run(identity(tenant_id=TENANT_B), run.id, NOW)


# --- recalculation and bonuses --------------------------------------------


async def test_recalculate_requeues_and_replaces_provisional_payslips(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    run_id = await _create_calculated_run(
        svc, payroll_repo, overtime, attendance, leave_days
    )
    await svc.create_bonus(
        identity(),
        run_id,
        BonusCreate(employee_id=EMPLOYEE, amount=Decimal("50.00")),
        NOW,
    )
    assert payroll_repo.locked_runs == [(TENANT_A, run_id)]

    queued = await svc.recalculate_run(identity(), run_id, NOW)
    assert queued.status is PayrollRunStatus.QUEUED
    assert queued.totals is None

    recalculated = await svc.calculate_run(run_id, NOW)
    payslips = [
        row
        for (tenant_id, _), row in payroll_repo.payslips.items()
        if tenant_id == TENANT_A
    ]
    assert recalculated.status is PayrollRunStatus.CALCULATED
    assert len(payslips) == 1
    assert payslips[0].bonuses == Decimal("50.00")
    assert recalculated.employee_count == 1
    # Recalculation must not emit a second run-level calculate event.
    assert (
        len(
            [
                event
                for event in outbox.envelopes
                if event["event_type"] == "payroll.calculated"
            ]
        )
        == 1
    )


async def test_recalculate_a_finalized_run_is_rejected(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    run_id = await _create_calculated_run(
        svc, payroll_repo, overtime, attendance, leave_days
    )
    await svc.finalize_run(identity(), run_id, NOW)

    with pytest.raises(PayrollRunFinalizedError):
        await svc.recalculate_run(identity(), run_id, NOW)
    with pytest.raises(PayrollRunFinalizedError):
        await svc.create_bonus(
            identity(),
            run_id,
            BonusCreate(employee_id=EMPLOYEE, amount=Decimal("10.00")),
            NOW,
        )


async def test_bonus_requires_the_employee_to_be_in_the_run(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    run = await svc.create_run(identity(), PayrollRunCreate(period=PERIOD), NOW)

    with pytest.raises(PayrollEmployeeNotInRunError):
        await svc.create_bonus(
            identity(),
            run.id,
            BonusCreate(employee_id=OTHER, amount=Decimal("10.00")),
            NOW,
        )


# --- calculation ----------------------------------------------------------


async def test_no_salary_skip_is_listed_and_does_not_block_finalize(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    await _seed_inputs(svc, payroll_repo, employee_id=EMPLOYEE)
    payroll_repo.add_employee(TENANT_A, OTHER)  # no salary record

    run = await svc.create_run(identity(), PayrollRunCreate(period=PERIOD), NOW)
    calculated = await svc.calculate_run(run.id, NOW)

    assert calculated.status is PayrollRunStatus.CALCULATED
    assert calculated.employee_count == 1
    assert calculated.skipped_count == 1
    assert calculated.skipped[0].employee_id == OTHER
    assert calculated.skipped[0].reason is PayrollRunSkipReason.NO_SALARY

    finalized = await svc.finalize_run(identity(), run.id, NOW)
    assert finalized.status is PayrollRunStatus.FINALIZED


async def test_a_fully_skipped_run_still_calculates_and_finalizes(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    payroll_repo.add_employee(TENANT_A, OTHER)  # no salary record

    run = await svc.create_run(identity(), PayrollRunCreate(period=PERIOD), NOW)
    calculated = await svc.calculate_run(run.id, NOW)

    assert calculated.status is PayrollRunStatus.CALCULATED
    assert calculated.employee_count == 0
    assert calculated.skipped_count == 1
    assert calculated.totals is not None
    assert calculated.totals.net == Decimal("0.00")

    finalized = await svc.finalize_run(identity(), run.id, NOW)
    assert finalized.status is PayrollRunStatus.FINALIZED
    issued_events = [
        event for event in outbox.envelopes if event["event_type"] == "payslip.issued"
    ]
    assert issued_events == []


async def test_duplicate_calculation_pass_is_idempotent(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    run_id = await _create_calculated_run(
        svc, payroll_repo, overtime, attendance, leave_days
    )

    again = await svc.calculate_run(run_id, NOW)

    assert again.status is PayrollRunStatus.CALCULATED
    assert len(payroll_repo.payslips) == 1
    calculated_events = [
        e for e in outbox.envelopes if e["event_type"] == "payroll.calculated"
    ]
    assert len(calculated_events) == 1


async def test_failed_calculation_is_recoverable_via_recalculate(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    overtime = FakeOvertimeHoursPort(error=RuntimeError("seam unavailable"))
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    await _seed_inputs(svc, payroll_repo)
    run = await svc.create_run(identity(), PayrollRunCreate(period=PERIOD), NOW)

    failed = await svc.calculate_run(run.id, NOW)
    assert failed.status is PayrollRunStatus.FAILED
    assert failed.failed_code == "payroll_calculation_failed"

    overtime.error = None
    queued = await svc.recalculate_run(identity(), run.id, NOW)
    assert queued.status is PayrollRunStatus.QUEUED

    recalculated = await svc.calculate_run(run.id, NOW)
    assert recalculated.status is PayrollRunStatus.CALCULATED
    assert recalculated.employee_count == 1


async def test_correction_run_supersedes_the_prior_finalized_run(
    payroll_repo: FakePayrollRepository,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    overtime: FakeOvertimeHoursPort,
    attendance: FakeAttendanceHoursPort,
    leave_days: FakeLeaveDaysPort,
) -> None:
    svc = _service(payroll_repo, audit, outbox, overtime, attendance, leave_days)
    first_id = await _create_calculated_run(
        svc, payroll_repo, overtime, attendance, leave_days
    )
    await svc.finalize_run(identity(), first_id, NOW)

    correction = await svc.create_run(identity(), PayrollRunCreate(period=PERIOD), NOW)
    assert correction.supersedes_run_id == first_id
    calculated = await svc.calculate_run(correction.id, NOW)
    assert calculated.status is PayrollRunStatus.CALCULATED
    await svc.finalize_run(identity(), correction.id, NOW)

    first_payslips = [
        row
        for (tenant_id, _), row in payroll_repo.payslips.items()
        if tenant_id == TENANT_A and row.run_id == first_id
    ]
    second_payslips = [
        row
        for (tenant_id, _), row in payroll_repo.payslips.items()
        if tenant_id == TENANT_A and row.run_id == correction.id
    ]
    assert [row.status for row in first_payslips] == [PayslipStatus.SUPERSEDED]
    assert [row.status for row in second_payslips] == [PayslipStatus.ISSUED]

    first_run = await payroll_repo.get_run(TENANT_A, first_id)
    assert first_run is not None
    assert first_run.superseded_by_run_id == correction.id
    assert "payroll.run_superseded" in [entry.action for entry in audit.entries]
