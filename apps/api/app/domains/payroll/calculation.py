"""The deterministic payroll calculation core (T4, ADR 0003/0017).

Pure functions only: no I/O, no clock, no ORM. The caller assembles the inputs
(the employee scope, salary histories, HR-verified overtime hours, bonus
entries and the tenant's active deduction rules) and receives payslips, totals
and the `no_salary` skip list. That split is what makes the simulation
reproducible: identical inputs and configuration produce byte-identical
outputs, which is the feature's core promise.

Money rules (ADR 0017, `.ai/features/payroll.md` → Business rules):

- Money is `Decimal`; there is no `float` anywhere.
- Every **output line** — base, overtime pay, bonuses, each deduction line and
  net — is quantized once to 2dp with `ROUND_HALF_UP`.
- Intermediates (`hourly_rate = base / standard_monthly_hours`, the overtime
  product, percentages) stay unrounded under an explicit `prec = 28` context.
- `net = base + overtime_pay + bonuses − Σ deduction lines`; it may be
  negative (a visible simulation outcome, never clamped).
- Deduction lines are ordered by `code`; payslips are ordered by employee id,
  so the serialized result is stable regardless of input ordering.
- Base is the salary record with the greatest `effective_from` on or before the
  period end. There is **no proration** (PO Decision 5): a mid-period hire or
  termination receives the full monthly amount.

`no_salary` skips are not errors: an in-scope employee without a salary record
effective by period end is omitted from payslips and listed in `skipped`
(plan T7).
"""

from calendar import monthrange
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from uuid import UUID

from pydantic import BaseModel

from app.domains.payroll.schemas import (
    PayrollDeduction,
    PayrollDeductionKind,
    PayrollRunSkip,
    PayrollRunSkipReason,
    PayrollRunTotals,
)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
_PRECISION = 28

# The fixed arithmetic context (ADR 0017 decision 2): explicit precision and
# rounding so a platform default can never change a cent.
_CONTEXT = Context(prec=_PRECISION, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class SalaryPoint:
    """One effective-dated salary input (already tenant/employee scoped)."""

    amount: Decimal
    effective_from: date


@dataclass(frozen=True, slots=True)
class EmployeePayrollInput:
    """Everything the calculator needs for one in-scope employee."""

    employee_id: UUID
    salaries: Sequence[SalaryPoint] = ()
    overtime_hours: Decimal = ZERO
    # Each entry is a stored 2dp bonus amount; they are summed per employee.
    bonuses: Sequence[Decimal] = ()


class CalculatedDeductionLine(BaseModel):
    """One output deduction line; `value` is the rule, `amount` the result."""

    code: str
    name: str
    kind: PayrollDeductionKind
    value: Decimal
    amount: Decimal


class CalculatedPayslip(BaseModel):
    """The line-level result for one employee (no id, run or timestamps).

    Persistence owns ids and statuses (T7); keeping them out of the core is
    what allows the byte-identical determinism test.
    """

    employee_id: UUID
    period: str
    base: Decimal
    overtime_pay: Decimal
    bonuses: Decimal
    deductions: list[CalculatedDeductionLine]
    net: Decimal


class CalculationResult(BaseModel):
    """The complete output of one calculation pass."""

    payslips: list[CalculatedPayslip]
    totals: PayrollRunTotals
    skipped: list[PayrollRunSkip]


def period_start(period: str) -> date:
    """The first calendar day of a `YYYY-MM` period (PO Decision 2)."""
    try:
        year_text, month_text = period.split("-", 1)
        year, month = int(year_text), int(month_text)
        return date(year, month, 1)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid payroll period: {period!r}") from None


def period_end(period: str) -> date:
    """The last calendar day of a `YYYY-MM` period (PO Decision 2)."""
    try:
        year_text, month_text = period.split("-", 1)
        year, month = int(year_text), int(month_text)
        return date(year, month, monthrange(year, month)[1])
    except (ValueError, TypeError):
        raise ValueError(f"Invalid payroll period: {period!r}") from None


def salary_for(
    salaries: Sequence[SalaryPoint], period_last_day: date
) -> SalaryPoint | None:
    """Pick the salary with the greatest `effective_from` ≤ the period end.

    Records effective after the period end do not participate (they cannot
    change a closed month), and an empty eligible history is the `no_salary`
    skip. The tie-break is deterministic even though the database forbids two
    records on the same date.
    """
    eligible = [point for point in salaries if point.effective_from <= period_last_day]
    if not eligible:
        return None
    return max(eligible, key=lambda point: (point.effective_from, point.amount))


def calculate_run_inputs(
    *,
    period: str,
    employees: Sequence[EmployeePayrollInput],
    deductions: Sequence[PayrollDeduction],
    standard_monthly_hours: Decimal,
    overtime_multiplier: Decimal,
) -> CalculationResult:
    """Calculate payslips and run totals from already-assembled inputs.

    `deductions` is the tenant's full rule set; inactive rules are ignored.
    Active rules land on every payslip as their own line (ADR 0003) and are
    ordered by `code`. `standard_monthly_hours` and `overtime_multiplier` come
    from tenant payroll config (defaults 160 / 1.50 in the API layer).
    """
    if standard_monthly_hours <= ZERO:
        raise ValueError("standard_monthly_hours must be greater than zero")
    if overtime_multiplier <= ZERO:
        raise ValueError("overtime_multiplier must be greater than zero")

    end = period_end(period)
    active_rules = sorted(
        (rule for rule in deductions if rule.active),
        key=lambda rule: (rule.code, str(rule.id)),
    )

    payslips: list[CalculatedPayslip] = []
    skipped: list[PayrollRunSkip] = []

    with localcontext(_CONTEXT):
        for employee in sorted(employees, key=lambda item: str(item.employee_id)):
            salary = salary_for(employee.salaries, end)
            if salary is None:
                skipped.append(
                    PayrollRunSkip(
                        employee_id=employee.employee_id,
                        reason=PayrollRunSkipReason.NO_SALARY,
                    )
                )
                continue

            base = _money(salary.amount)
            hourly_rate = base / standard_monthly_hours
            overtime_pay = _money(
                employee.overtime_hours * hourly_rate * overtime_multiplier
            )
            bonuses = _money(sum(employee.bonuses, ZERO))

            lines = [_deduction_line(rule, base) for rule in active_rules]
            net = _money(
                base
                + overtime_pay
                + bonuses
                - sum((line.amount for line in lines), ZERO)
            )

            payslips.append(
                CalculatedPayslip(
                    employee_id=employee.employee_id,
                    period=period,
                    base=base,
                    overtime_pay=overtime_pay,
                    bonuses=bonuses,
                    deductions=lines,
                    net=net,
                )
            )

    totals = PayrollRunTotals(
        base=_money(sum((payslip.base for payslip in payslips), ZERO)),
        overtime_pay=_money(sum((payslip.overtime_pay for payslip in payslips), ZERO)),
        bonuses=_money(sum((payslip.bonuses for payslip in payslips), ZERO)),
        deductions=_money(
            sum(
                (line.amount for payslip in payslips for line in payslip.deductions),
                ZERO,
            )
        ),
        net=_money(sum((payslip.net for payslip in payslips), ZERO)),
    )
    return CalculationResult(payslips=payslips, totals=totals, skipped=skipped)


def _deduction_line(rule: PayrollDeduction, base: Decimal) -> CalculatedDeductionLine:
    """Apply one active tenant rule to one employee's base pay."""
    if rule.kind is PayrollDeductionKind.PERCENT_OF_BASE:
        amount = base * rule.value / Decimal(100)
    else:  # fixed_amount
        amount = rule.value
    return CalculatedDeductionLine(
        code=rule.code,
        name=rule.name,
        kind=rule.kind,
        value=_money(rule.value),
        amount=_money(amount),
    )


def _money(value: Decimal) -> Decimal:
    """The single output-line quantization point (ADR 0017 decision 2)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
