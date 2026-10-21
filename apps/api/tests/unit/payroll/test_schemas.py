"""Wire-shape tests for payroll schemas (ADR 0017 money rendering)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.payroll.schemas import (
    BonusCreate,
    PayrollConfig,
    PayrollDeductionCreate,
    PayrollDeductionKind,
    PayrollDeductionUpdate,
    PayrollRunCreate,
    SalaryCreate,
)

TENANT = UUID("00000000-0000-4000-8000-00000000000a")


def test_money_serializes_as_a_json_number_not_a_string() -> None:
    """ADR 0017 decision 5: generated clients must see `number`."""
    config = PayrollConfig(
        tenant_id=TENANT,
        currency="USD",
        standard_monthly_hours=Decimal("160.00"),
        overtime_multiplier=Decimal("1.50"),
        updated_at=datetime(2026, 10, 1, tzinfo=UTC),
    )

    dumped = config.model_dump(mode="json")

    assert dumped["standard_monthly_hours"] == 160.0
    assert isinstance(dumped["standard_monthly_hours"], float)
    assert dumped["overtime_multiplier"] == 1.5
    assert isinstance(dumped["overtime_multiplier"], float)

    # Orval reads OpenAPI, not our Python serializer: pin the schema too.
    schema = PayrollConfig.model_json_schema()["properties"]
    assert schema["standard_monthly_hours"]["type"] == "number"
    assert schema["overtime_multiplier"]["type"] == "number"


def test_period_must_be_a_calendar_month() -> None:
    assert PayrollRunCreate(period="2026-09").period == "2026-09"
    with pytest.raises(ValidationError):
        PayrollRunCreate(period="2026-13")
    with pytest.raises(ValidationError):
        PayrollRunCreate(period="2026-9")


@pytest.mark.parametrize("value", [Decimal("0.00"), Decimal("100.01")])
def test_percent_deduction_outside_range_is_rejected(value: Decimal) -> None:
    with pytest.raises(ValidationError):
        PayrollDeductionCreate(
            code="TAX",
            name="Tax",
            kind=PayrollDeductionKind.PERCENT_OF_BASE,
            value=value,
        )


def test_fixed_deduction_below_one_cent_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PayrollDeductionUpdate(
            kind=PayrollDeductionKind.FIXED_AMOUNT, value=Decimal("0")
        )


def test_salary_and_bonus_amounts_are_at_least_one_cent() -> None:
    with pytest.raises(ValidationError):
        SalaryCreate(
            employee_id=UUID("00000000-0000-4000-8000-000000000002"),
            amount=Decimal("0.00"),
            effective_from=date(2026, 9, 1),
        )
    with pytest.raises(ValidationError):
        BonusCreate(
            employee_id=UUID("00000000-0000-4000-8000-000000000002"),
            amount=Decimal("0.001"),
        )


def test_bonus_note_is_bounded_and_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        BonusCreate(
            employee_id=UUID("00000000-0000-4000-8000-000000000002"),
            amount=Decimal("10.00"),
            note="x" * 201,
        )
    with pytest.raises(ValidationError):
        BonusCreate.model_validate(
            {
                "employee_id": "00000000-0000-4000-8000-000000000002",
                "amount": "10.00",
                "run_id": "00000000-0000-4000-8000-000000000005",
            }
        )
