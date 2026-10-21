"""Payroll routes (T5/T6/T9): config, inputs, run lifecycle, bonuses and reads.

Routers stay thin: identity → policy (inside the service) → service, with `now`
read at this edge only. operationIds are pinned in `.ai/features/payroll.md` and
drive the orval-generated web hooks.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.dependencies import IdentityDep, PayrollServiceDep
from app.domains.payroll.schemas import (
    PERIOD_PATTERN,
    BonusCreate,
    PayrollBonus,
    PayrollConfig,
    PayrollConfigUpdate,
    PayrollDeduction,
    PayrollDeductionCreate,
    PayrollDeductionUpdate,
    PayrollRun,
    PayrollRunCreate,
    PayrollRunListPage,
    PayrollRunStatus,
    Payslip,
    PayslipListPage,
    Salary,
    SalaryCreate,
    SalaryListPage,
    TimesheetListPage,
)
from app.shared.pagination import PageParamsDep

router = APIRouter()


def _now() -> datetime:
    """The single place wall-clock time is read (time is a parameter below)."""
    return datetime.now(UTC)


# --- configuration -----------------------------------------------------------


@router.get(
    "/payroll/config",
    response_model=PayrollConfig,
    operation_id="getPayrollConfig",
)
async def get_payroll_config(
    identity: IdentityDep,
    service: PayrollServiceDep,
) -> PayrollConfig:
    """The tenant's payroll config (defaults provisioned on first read)."""
    return await service.get_config(identity, _now())


@router.put(
    "/payroll/config",
    response_model=PayrollConfig,
    operation_id="updatePayrollConfig",
)
async def update_payroll_config(
    payload: PayrollConfigUpdate,
    identity: IdentityDep,
    service: PayrollServiceDep,
) -> PayrollConfig:
    """Partial config update — Finance/Admin/Super Admin only."""
    return await service.update_config(identity, payload, _now())


# --- deduction lines ---------------------------------------------------------


@router.get(
    "/payroll/deductions",
    response_model=list[PayrollDeduction],
    operation_id="listPayrollDeductions",
)
async def list_payroll_deductions(
    identity: IdentityDep,
    service: PayrollServiceDep,
) -> list[PayrollDeduction]:
    """All transparent tenant deduction rules, ordered by `code`."""
    return list(await service.list_deductions(identity))


@router.post(
    "/payroll/deductions",
    response_model=PayrollDeduction,
    operation_id="createPayrollDeduction",
    status_code=201,
)
async def create_payroll_deduction(
    payload: PayrollDeductionCreate,
    identity: IdentityDep,
    service: PayrollServiceDep,
) -> PayrollDeduction:
    """Add one deduction rule (code unique per tenant)."""
    return await service.create_deduction(identity, payload, _now())


@router.patch(
    "/payroll/deductions/{id}",
    response_model=PayrollDeduction,
    operation_id="updatePayrollDeduction",
)
async def update_payroll_deduction(
    id: UUID,
    payload: PayrollDeductionUpdate,
    identity: IdentityDep,
    service: PayrollServiceDep,
) -> PayrollDeduction:
    """Patch a rule; `code` is immutable."""
    return await service.update_deduction(identity, id, payload, _now())


# --- salary records ----------------------------------------------------------


@router.get(
    "/payroll/salaries",
    response_model=SalaryListPage,
    operation_id="listSalaries",
)
async def list_salaries(
    identity: IdentityDep,
    service: PayrollServiceDep,
    page: PageParamsDep,
    employee_id: UUID | None = Query(default=None),
) -> SalaryListPage:
    """Paged append-only salary history, newest `effective_from` first."""
    return await service.list_salaries(identity, employee_id=employee_id, page=page)


@router.post(
    "/payroll/salaries",
    response_model=Salary,
    operation_id="createSalary",
    status_code=201,
)
async def create_salary(
    payload: SalaryCreate,
    identity: IdentityDep,
    service: PayrollServiceDep,
) -> Salary:
    """Append a salary record; currency comes from tenant config."""
    return await service.create_salary(identity, payload, _now())


# --- run lifecycle -----------------------------------------------------------


@router.post(
    "/payroll/runs",
    response_model=PayrollRun,
    operation_id="createPayrollRun",
    status_code=201,
)
async def create_payroll_run(
    payload: PayrollRunCreate,
    identity: IdentityDep,
    service: PayrollServiceDep,
) -> PayrollRun:
    """Queue a run for a closed month (Finance/Admin/Super Admin)."""
    return await service.create_run(identity, payload, _now())


@router.post(
    "/payroll/runs/{id}/recalculate",
    response_model=PayrollRun,
    operation_id="recalculatePayrollRun",
)
async def recalculate_payroll_run(
    id: UUID,
    identity: IdentityDep,
    service: PayrollServiceDep,
) -> PayrollRun:
    """Re-queue a calculated/failed run; finalized runs are immutable."""
    return await service.recalculate_run(identity, id, _now())


@router.post(
    "/payroll/runs/{id}/finalize",
    response_model=PayrollRun,
    operation_id="finalizePayrollRun",
)
async def finalize_payroll_run(
    id: UUID,
    identity: IdentityDep,
    service: PayrollServiceDep,
) -> PayrollRun:
    """Issue payslips and flip counted overtime exactly once."""
    return await service.finalize_run(identity, id, _now())


@router.post(
    "/payroll/runs/{id}/bonuses",
    response_model=PayrollBonus,
    operation_id="createRunBonus",
    status_code=201,
)
async def create_run_bonus(
    id: UUID,
    payload: BonusCreate,
    identity: IdentityDep,
    service: PayrollServiceDep,
) -> PayrollBonus:
    """Add a bonus for an employee in the calculated run."""
    return await service.create_bonus(identity, id, payload, _now())


# --- queries (T9) ------------------------------------------------------------


@router.get(
    "/payroll/runs",
    response_model=PayrollRunListPage,
    operation_id="listPayrollRuns",
)
async def list_payroll_runs(
    identity: IdentityDep,
    service: PayrollServiceDep,
    page: PageParamsDep,
    period: str | None = Query(default=None, pattern=PERIOD_PATTERN),
    status: PayrollRunStatus | None = Query(default=None),
) -> PayrollRunListPage:
    """Paged run history, newest period first (HR+)."""
    return await service.list_runs(identity, period=period, status=status, page=page)


@router.get(
    "/payroll/runs/{id}",
    response_model=PayrollRun,
    operation_id="getPayrollRun",
)
async def get_payroll_run(
    id: UUID,
    identity: IdentityDep,
    service: PayrollServiceDep,
) -> PayrollRun:
    """One run and its PII-free summary."""
    return await service.get_run(identity, id)


@router.get(
    "/payroll/runs/{id}/timesheets",
    response_model=TimesheetListPage,
    operation_id="listRunTimesheets",
)
async def list_run_timesheets(
    id: UUID,
    identity: IdentityDep,
    service: PayrollServiceDep,
    page: PageParamsDep,
) -> TimesheetListPage:
    """Informational worked/overtime/leave facts for the run's scope."""
    return await service.list_run_timesheets(identity, id, page)


@router.get(
    "/payroll/runs/{id}/payslips",
    response_model=PayslipListPage,
    operation_id="listRunPayslips",
)
async def list_run_payslips(
    id: UUID,
    identity: IdentityDep,
    service: PayrollServiceDep,
    page: PageParamsDep,
) -> PayslipListPage:
    """Every payslip of one run (provisional, issued and superseded)."""
    return await service.list_run_payslips(identity, id, page)


@router.get(
    "/payroll/payslips",
    response_model=PayslipListPage,
    operation_id="listMyPayslips",
)
async def list_my_payslips(
    identity: IdentityDep,
    service: PayrollServiceDep,
    page: PageParamsDep,
) -> PayslipListPage:
    """The caller's own payslips, every role (issued + superseded)."""
    return await service.list_my_payslips(identity, page)


@router.get(
    "/payroll/payslips/{id}",
    response_model=Payslip,
    operation_id="getPayslip",
)
async def get_payslip(
    id: UUID,
    identity: IdentityDep,
    service: PayrollServiceDep,
) -> Payslip:
    """Own payslip, or any payslip for HR/Finance/Admin/Super Admin."""
    return await service.get_payslip(identity, id)
