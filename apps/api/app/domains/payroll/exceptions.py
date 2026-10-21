"""Domain errors — one subclass per pinned code/status.

Codes and statuses are pinned in `.ai/features/payroll.md`
(## API contract → "Pinned error codes"). Messages are static strings and never
echo amounts, bonus notes, salaries or any other PII (`.ai/security.md`).

`EmployeeNotFoundError` is a cross-domain shared stable code with one class per
domain (the leave/attendance precedent): creating a salary or bonus for an
employee outside the tenant must not import another domain's errors.

The `404` classes are deliberately indistinguishable from one another at the
HTTP boundary: an unknown, cross-tenant or out-of-scope id all return
`*_not_found`, so existence is never leaked.
"""

from app.shared.errors import WorkPulseError


class PayrollPeriodNotClosedError(WorkPulseError):
    """A run may only be created for a month that has ended."""

    status_code = 409
    code = "payroll_period_not_closed"

    def __init__(self) -> None:
        super().__init__("Payroll period has not ended")


class PayrollPeriodOpenError(WorkPulseError):
    """One non-finalized run per period; finalize or wait before retrying."""

    status_code = 409
    code = "payroll_period_open"

    def __init__(self) -> None:
        super().__init__("A payroll run for this period is still open")


class PayrollRunNotCalculatedError(WorkPulseError):
    """Finalize is only allowed from `calculated`."""

    status_code = 409
    code = "payroll_run_not_calculated"

    def __init__(self) -> None:
        super().__init__("Payroll run is not calculated")


class PayrollRunFinalizedError(WorkPulseError):
    """`finalized` is terminal and immutable."""

    status_code = 409
    code = "payroll_run_finalized"

    def __init__(self) -> None:
        super().__init__("Payroll run is finalized")


class PayrollEmployeeNotInRunError(WorkPulseError):
    """A bonus target has no payslip in the run."""

    status_code = 409
    code = "payroll_employee_not_in_run"

    def __init__(self) -> None:
        super().__init__("Employee is not part of this payroll run")


class SalaryEffectiveDateTakenError(WorkPulseError):
    """One salary record per employee per effective date (append-only)."""

    status_code = 409
    code = "salary_effective_date_taken"

    def __init__(self) -> None:
        super().__init__("Salary effective date already taken")


class PayrollDeductionCodeTakenError(WorkPulseError):
    """Deduction codes are the stable line keys and unique per tenant."""

    status_code = 409
    code = "payroll_deduction_code_taken"

    def __init__(self) -> None:
        super().__init__("Payroll deduction code already taken")


class InvalidPayrollDeductionValueError(WorkPulseError):
    """A partial update merged into a kind/value pair that violates the rule."""

    status_code = 422
    code = "validation_error"

    def __init__(self) -> None:
        super().__init__("Deduction value is invalid for its kind")


class PayrollNotFoundError(WorkPulseError):
    """No payroll standing for this capability — existence is not leaked."""

    status_code = 404
    code = "payroll_not_found"

    def __init__(self) -> None:
        super().__init__("Payroll data not found")


class PayrollRunNotFoundError(WorkPulseError):
    """Unknown, cross-tenant or out-of-scope run — existence is not leaked."""

    status_code = 404
    code = "payroll_run_not_found"

    def __init__(self) -> None:
        super().__init__("Payroll run not found")


class PayslipNotFoundError(WorkPulseError):
    """Unknown, foreign or cross-tenant payslip — existence is not leaked."""

    status_code = 404
    code = "payslip_not_found"

    def __init__(self) -> None:
        super().__init__("Payslip not found")


class PayrollDeductionNotFoundError(WorkPulseError):
    """Unknown or cross-tenant deduction line."""

    status_code = 404
    code = "payroll_deduction_not_found"

    def __init__(self) -> None:
        super().__init__("Payroll deduction not found")


class EmployeeNotFoundError(WorkPulseError):
    """Salary/bonus target outside the tenant (domain-local shared code)."""

    status_code = 404
    code = "employee_not_found"

    def __init__(self) -> None:
        super().__init__("Employee not found")
