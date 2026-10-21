"""Unit tests for the deterministic payroll calculation core (T4).

Pinned expectations (`.ai/features/payroll.md` → AC1/AC3/AC6/AC10, ADR 0017):

- base comes from the greatest `effective_from` on or before period end;
- `overtime_pay = hours × (base / standard_monthly_hours) × multiplier`;
- bonuses sum, one line per active deduction (ordered by `code`), and
  `net = base + overtime_pay + bonuses − Σ deduction lines`;
- every output line is half-up 2dp and `Σ payslip lines == run totals`;
- unchanged inputs (even in a different order) serialize byte-identically;
- an employee without an eligible salary is skipped as `no_salary`;
- mid-period hires/terminations get the full base (no proration).
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.domains.payroll.calculation import (
    EmployeePayrollInput,
    SalaryPoint,
    calculate_run_inputs,
    period_end,
    salary_for,
)
from app.domains.payroll.schemas import (
    PayrollDeduction,
    PayrollDeductionKind,
    PayrollRunSkipReason,
)

PERIOD = "2026-09"
PERIOD_END = date(2026, 9, 30)
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)

EMPLOYEE_A = UUID("00000000-0000-4000-8000-0000000000a1")
EMPLOYEE_B = UUID("00000000-0000-4000-8000-0000000000b1")
RULE_A = UUID("00000000-0000-4000-8000-0000000000c1")
RULE_B = UUID("00000000-0000-4000-8000-0000000000c2")
RULE_INACTIVE = UUID("00000000-0000-4000-8000-0000000000c3")


def rule(
    *,
    rule_id: UUID,
    code: str,
    kind: PayrollDeductionKind,
    value: str,
    active: bool = True,
    name: str | None = None,
) -> PayrollDeduction:
    return PayrollDeduction(
        id=rule_id,
        code=code,
        name=name or code.title(),
        kind=kind,
        value=Decimal(value),
        active=active,
        created_at=NOW,
        updated_at=NOW,
    )


def employee(
    employee_id: UUID = EMPLOYEE_A,
    *,
    salaries: tuple[SalaryPoint, ...] = (),
    overtime_hours: str = "0",
    bonuses: tuple[str, ...] = (),
) -> EmployeePayrollInput:
    return EmployeePayrollInput(
        employee_id=employee_id,
        salaries=salaries,
        overtime_hours=Decimal(overtime_hours),
        bonuses=tuple(Decimal(amount) for amount in bonuses),
    )


def calculate(
    employees: tuple[EmployeePayrollInput, ...],
    *,
    deductions: tuple[PayrollDeduction, ...] = (),
    hours: str = "160.00",
    multiplier: str = "1.50",
):
    return calculate_run_inputs(
        period=PERIOD,
        employees=employees,
        deductions=deductions,
        standard_monthly_hours=Decimal(hours),
        overtime_multiplier=Decimal(multiplier),
    )


def test_salary_pick_uses_greatest_effective_date_on_or_before_period_end() -> None:
    history = (
        SalaryPoint(amount=Decimal("1000.00"), effective_from=date(2026, 1, 1)),
        SalaryPoint(amount=Decimal("1200.00"), effective_from=date(2026, 9, 15)),
        # After the closed month: must never participate.
        SalaryPoint(amount=Decimal("5000.00"), effective_from=date(2026, 10, 1)),
    )

    picked = salary_for(history, PERIOD_END)

    assert picked is not None
    assert picked.amount == Decimal("1200.00")


def test_salary_effective_on_period_end_is_eligible() -> None:
    history = (
        SalaryPoint(amount=Decimal("1000.00"), effective_from=date(2026, 1, 1)),
        SalaryPoint(amount=Decimal("1400.00"), effective_from=PERIOD_END),
    )

    picked = salary_for(history, PERIOD_END)

    assert picked is not None
    assert picked.amount == Decimal("1400.00")


def test_employee_without_eligible_salary_is_skipped_not_failed() -> None:
    result = calculate(
        (
            employee(
                salaries=(
                    SalaryPoint(
                        amount=Decimal("2000.00"),
                        effective_from=date(2026, 10, 1),
                    ),
                )
            ),
        )
    )

    assert result.payslips == []
    assert len(result.skipped) == 1
    assert result.skipped[0].employee_id == EMPLOYEE_A
    assert result.skipped[0].reason is PayrollRunSkipReason.NO_SALARY
    assert result.totals.base == Decimal("0.00")
    assert result.totals.net == Decimal("0.00")


def test_mid_period_hire_gets_the_full_base_no_proration() -> None:
    result = calculate(
        (
            employee(
                salaries=(
                    SalaryPoint(
                        amount=Decimal("3000.00"),
                        effective_from=date(2026, 9, 20),
                    ),
                )
            ),
        )
    )

    assert len(result.payslips) == 1
    assert result.payslips[0].base == Decimal("3000.00")


def test_overtime_pay_uses_the_pinned_formula_and_rounds_half_up() -> None:
    # hourly = 1600 / 160 = 10; 2.345 × 10 × 1.50 = 35.175 → 35.18
    result = calculate(
        (
            employee(
                salaries=(
                    SalaryPoint(
                        amount=Decimal("1600.00"), effective_from=date(2026, 1, 1)
                    ),
                ),
                overtime_hours="2.345",
            ),
        )
    )

    assert result.payslips[0].overtime_pay == Decimal("35.18")


def test_bonus_entries_are_summed_and_quantized_once() -> None:
    result = calculate(
        (
            employee(
                salaries=(
                    SalaryPoint(
                        amount=Decimal("1000.00"), effective_from=date(2026, 1, 1)
                    ),
                ),
                bonuses=("12.34", "0.66"),
            ),
        )
    )

    assert result.payslips[0].bonuses == Decimal("13.00")


def test_each_active_deduction_is_a_code_ordered_line_and_inactive_is_absent() -> None:
    result = calculate(
        (
            employee(
                salaries=(
                    SalaryPoint(
                        amount=Decimal("1000.00"), effective_from=date(2026, 1, 1)
                    ),
                )
            ),
        ),
        deductions=(
            rule(
                rule_id=RULE_B,
                code="ZTAX",
                kind=PayrollDeductionKind.FIXED_AMOUNT,
                value="10.00",
            ),
            rule(
                rule_id=RULE_INACTIVE,
                code="MTAX",
                kind=PayrollDeductionKind.PERCENT_OF_BASE,
                value="50.00",
                active=False,
            ),
            rule(
                rule_id=RULE_A,
                code="ATAX",
                kind=PayrollDeductionKind.PERCENT_OF_BASE,
                value="5.00",
            ),
        ),
    )

    payslip = result.payslips[0]
    assert [line.code for line in payslip.deductions] == ["ATAX", "ZTAX"]
    assert [line.amount for line in payslip.deductions] == [
        Decimal("50.00"),
        Decimal("10.00"),
    ]
    assert payslip.net == Decimal("940.00")


def test_net_is_the_formula_applied_to_rounded_lines() -> None:
    # base 1600 + overtime 35.18 + bonuses 13.00 − 160.00 − 25.50 = 1462.68
    result = calculate(
        (
            employee(
                salaries=(
                    SalaryPoint(
                        amount=Decimal("1600.00"), effective_from=date(2026, 1, 1)
                    ),
                ),
                overtime_hours="2.345",
                bonuses=("12.34", "0.66"),
            ),
        ),
        deductions=(
            rule(
                rule_id=RULE_A,
                code="PENSION",
                kind=PayrollDeductionKind.PERCENT_OF_BASE,
                value="10.00",
            ),
            rule(
                rule_id=RULE_B,
                code="LOAN",
                kind=PayrollDeductionKind.FIXED_AMOUNT,
                value="25.50",
            ),
        ),
    )

    payslip = result.payslips[0]
    assert payslip.net == Decimal("1462.68")
    assert payslip.net == (
        payslip.base
        + payslip.overtime_pay
        + payslip.bonuses
        - sum((line.amount for line in payslip.deductions), Decimal("0.00"))
    )


def test_negative_net_is_a_visible_outcome_not_clamped() -> None:
    result = calculate(
        (
            employee(
                salaries=(
                    SalaryPoint(
                        amount=Decimal("100.00"), effective_from=date(2026, 1, 1)
                    ),
                )
            ),
        ),
        deductions=(
            rule(
                rule_id=RULE_A,
                code="BIGLOAN",
                kind=PayrollDeductionKind.FIXED_AMOUNT,
                value="150.00",
            ),
        ),
    )

    assert result.payslips[0].net == Decimal("-50.00")


def test_totals_equal_the_sum_of_payslip_lines_to_the_cent() -> None:
    result = calculate(
        (
            employee(
                EMPLOYEE_A,
                salaries=(
                    SalaryPoint(
                        amount=Decimal("1234.56"), effective_from=date(2026, 1, 1)
                    ),
                ),
                overtime_hours="1.11",
                bonuses=("10.00",),
            ),
            employee(
                EMPLOYEE_B,
                salaries=(
                    SalaryPoint(
                        amount=Decimal("2345.67"), effective_from=date(2026, 9, 1)
                    ),
                ),
                overtime_hours="0.07",
                bonuses=("5.55", "0.01"),
            ),
        ),
        deductions=(
            rule(
                rule_id=RULE_A,
                code="TAX",
                kind=PayrollDeductionKind.PERCENT_OF_BASE,
                value="12.50",
            ),
            rule(
                rule_id=RULE_B,
                code="FEE",
                kind=PayrollDeductionKind.FIXED_AMOUNT,
                value="3.33",
            ),
        ),
    )

    assert result.totals.base == sum((p.base for p in result.payslips), Decimal("0.00"))
    assert result.totals.overtime_pay == sum(
        (p.overtime_pay for p in result.payslips), Decimal("0.00")
    )
    assert result.totals.bonuses == sum(
        (p.bonuses for p in result.payslips), Decimal("0.00")
    )
    assert result.totals.deductions == sum(
        (line.amount for p in result.payslips for line in p.deductions),
        Decimal("0.00"),
    )
    assert result.totals.net == sum((p.net for p in result.payslips), Decimal("0.00"))
    assert result.totals.net == (
        result.totals.base
        + result.totals.overtime_pay
        + result.totals.bonuses
        - result.totals.deductions
    )


def test_same_inputs_in_any_order_serialize_byte_identically() -> None:
    employees = (
        employee(
            EMPLOYEE_A,
            salaries=(
                SalaryPoint(amount=Decimal("1000.00"), effective_from=date(2026, 1, 1)),
            ),
            bonuses=("10.00", "2.50"),
        ),
        employee(
            EMPLOYEE_B,
            salaries=(
                SalaryPoint(amount=Decimal("2000.00"), effective_from=date(2026, 2, 1)),
            ),
            overtime_hours="3.00",
        ),
    )
    deductions = (
        rule(
            rule_id=RULE_B,
            code="ZTAX",
            kind=PayrollDeductionKind.FIXED_AMOUNT,
            value="7.77",
        ),
        rule(
            rule_id=RULE_A,
            code="ATAX",
            kind=PayrollDeductionKind.PERCENT_OF_BASE,
            value="3.00",
        ),
    )

    first = calculate(employees, deductions=deductions)
    second = calculate(
        tuple(reversed(employees)), deductions=tuple(reversed(deductions))
    )

    assert first.model_dump_json() == second.model_dump_json()


def test_empty_scope_produces_zero_totals() -> None:
    result = calculate(())

    assert result.payslips == []
    assert result.skipped == []
    assert result.totals.model_dump() == {
        "base": Decimal("0.00"),
        "overtime_pay": Decimal("0.00"),
        "bonuses": Decimal("0.00"),
        "deductions": Decimal("0.00"),
        "net": Decimal("0.00"),
    }


def test_period_end_handles_leap_february_and_rejects_malformed_periods() -> None:
    assert period_end("2028-02") == date(2028, 2, 29)
    assert period_end("2026-09") == date(2026, 9, 30)
    with pytest.raises(ValueError):
        period_end("2026-13")
    with pytest.raises(ValueError):
        period_end("not-a-month")


@pytest.mark.parametrize("hours", ["0", "-1"])
def test_standard_monthly_hours_must_be_positive(hours: str) -> None:
    with pytest.raises(ValueError):
        calculate((), hours=hours)


@pytest.mark.parametrize("multiplier", ["0", "-0.5"])
def test_overtime_multiplier_must_be_positive(multiplier: str) -> None:
    with pytest.raises(ValueError):
        calculate((), hours="160", multiplier=multiplier)
