"""Payroll routes (T5): configuration, deduction lines and salary records.

The remaining payroll routes (run lifecycle, bonuses, queries, own payslips)
land with T6/T9. Routers stay thin: identity → policy (inside the service) →
service, with `now` read at this edge only. operationIds are pinned in
`.ai/features/payroll.md` and drive the orval-generated web hooks.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.dependencies import IdentityDep, PayrollServiceDep
from app.domains.payroll.schemas import (
    PayrollConfig,
    PayrollConfigUpdate,
    PayrollDeduction,
    PayrollDeductionCreate,
    PayrollDeductionUpdate,
    Salary,
    SalaryCreate,
    SalaryListPage,
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
