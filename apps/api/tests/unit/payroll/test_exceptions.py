"""Pinned payroll error codes and statuses (`.ai/features/payroll.md`)."""

import pytest

from app.domains.payroll.exceptions import (
    EmployeeNotFoundError,
    PayrollDeductionNotFoundError,
    PayrollEmployeeNotInRunError,
    PayrollPeriodNotClosedError,
    PayrollPeriodOpenError,
    PayrollRunFinalizedError,
    PayrollRunNotCalculatedError,
    PayrollRunNotFoundError,
    PayslipNotFoundError,
    SalaryEffectiveDateTakenError,
)
from app.shared.errors import WorkPulseError

PINNED_ERRORS = [
    (PayrollPeriodNotClosedError, 409, "payroll_period_not_closed"),
    (PayrollPeriodOpenError, 409, "payroll_period_open"),
    (PayrollRunNotCalculatedError, 409, "payroll_run_not_calculated"),
    (PayrollRunFinalizedError, 409, "payroll_run_finalized"),
    (PayrollEmployeeNotInRunError, 409, "payroll_employee_not_in_run"),
    (SalaryEffectiveDateTakenError, 409, "salary_effective_date_taken"),
    (PayrollRunNotFoundError, 404, "payroll_run_not_found"),
    (PayslipNotFoundError, 404, "payslip_not_found"),
    (PayrollDeductionNotFoundError, 404, "payroll_deduction_not_found"),
    (EmployeeNotFoundError, 404, "employee_not_found"),
]


@pytest.mark.parametrize(("error_class", "status", "code"), PINNED_ERRORS)
def test_error_carries_the_pinned_code_and_status(
    error_class: type[WorkPulseError], status: int, code: str
) -> None:
    error = error_class()

    assert error.status_code == status
    assert error.code == code
    assert error.message
    assert str(error) == error.message
