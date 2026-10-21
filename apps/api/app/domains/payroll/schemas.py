"""Wire contracts for the Payroll domain (pinned in `.ai/features/payroll.md`).

These shapes are the vocabulary of the repository protocols, the calculation
core and the API response models. Money is `Decimal` internally (ADR 0017) but
serializes as a JSON **number**, never a string: `Money` carries a JSON-only
serializer, so generated clients see `number`. Value scale remains pinned at
2dp by the calculation/DB; JSON itself has no scale.

`PayrollRun.status`, `Payslip.status`, `PayrollDeduction.kind` and the skip
reason serialize as the pinned lowercase strings. `period` is always
`YYYY-MM` (a calendar month, PO Decision 2).

Writes reject unknown fields (`extra="forbid"`): amounts, employee targets and
timestamps are server-owned. Cross-field rules that need the *current* stored
row (for example changing only a deduction value whose kind is stored) are
enforced in the service; validators here cover every rule that can be decided
from the request alone. Amounts, bonus notes, payslip lines and run totals are
PII — never logged, never in events and never in audit `changes` values
(`.ai/security.md`).
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    model_validator,
)

from app.shared.pagination import Page

PERIOD_PATTERN = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
CURRENCY_PATTERN = r"^[A-Z]{3}$"

Period = Annotated[str, Field(pattern=PERIOD_PATTERN)]
Currency = Annotated[str, Field(pattern=CURRENCY_PATTERN)]


def _json_number(value: Decimal) -> float:
    """Render a Decimal as a JSON number (ADR 0017 decision 5)."""
    return float(value)


# `Decimal` stays exact in Python; only the JSON representation becomes a
# number. Never use this for arithmetic.
Money = Annotated[
    Decimal,
    PlainSerializer(_json_number, return_type=float, when_used="json"),
    # OpenAPI is generated from the validation schema; Decimal's default is
    # `anyOf: [number, string]`. Pin it to a JSON number so the generated
    # client types money as `number`, never `number | string` (ADR 0017).
    WithJsonSchema({"type": "number"}),
]
DecimalNumber = Money

# Request-side numerics are validated as `Decimal`, but the generated
# contract must stay a JSON number (ADR 0017 decision 5): without the schema
# override Pydantic's lax input mode emits `number | string`, which lets the
# typed web client drift from the pinned wire shape.
PositiveNumberInput = Annotated[
    Decimal, WithJsonSchema({"type": "number", "exclusiveMinimum": 0})
]
CentAmountInput = Annotated[
    Decimal, WithJsonSchema({"type": "number", "minimum": 0.01})
]


class PayrollRunStatus(StrEnum):
    """Pinned run lifecycle (`queued → calculating → calculated → finalized`)."""

    QUEUED = "queued"
    CALCULATING = "calculating"
    CALCULATED = "calculated"
    FINALIZED = "finalized"
    FAILED = "failed"


class PayrollRunSkipReason(StrEnum):
    """Stable run-summary skip reasons (v1 has exactly one)."""

    NO_SALARY = "no_salary"


class PayrollDeductionKind(StrEnum):
    PERCENT_OF_BASE = "percent_of_base"
    FIXED_AMOUNT = "fixed_amount"


class PayslipStatus(StrEnum):
    PROVISIONAL = "provisional"
    ISSUED = "issued"
    SUPERSEDED = "superseded"


class PayrollConfig(BaseModel):
    """One tenant payroll configuration (single currency)."""

    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    currency: Currency
    standard_monthly_hours: DecimalNumber
    overtime_multiplier: DecimalNumber
    updated_at: datetime


class PayrollConfigUpdate(BaseModel):
    """Partial update; every field is optional and validated when present."""

    model_config = ConfigDict(extra="forbid")

    currency: Currency | None = None
    standard_monthly_hours: PositiveNumberInput | None = Field(default=None, gt=0)
    overtime_multiplier: PositiveNumberInput | None = Field(default=None, gt=0)


class PayrollDeduction(BaseModel):
    """One transparent tenant deduction rule."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    kind: PayrollDeductionKind
    value: Money
    active: bool
    created_at: datetime
    updated_at: datetime


def validate_deduction_value(kind: PayrollDeductionKind, value: Decimal) -> None:
    """Kind-specific value rule shared by create/update and the service.

    The service also calls this with the *merged* rule when an update patches
    only one side of the kind/value pair.
    """
    if kind is PayrollDeductionKind.PERCENT_OF_BASE and not (
        Decimal("0.01") <= value <= Decimal("100")
    ):
        raise ValueError("percent_of_base value must be between 0.01 and 100")
    if kind is PayrollDeductionKind.FIXED_AMOUNT and value < Decimal("0.01"):
        raise ValueError("fixed_amount value must be at least 0.01")


class PayrollDeductionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    kind: PayrollDeductionKind
    value: CentAmountInput = Field(ge=Decimal("0.01"))
    active: bool = True

    @model_validator(mode="after")
    def _value_matches_kind(self) -> Self:
        validate_deduction_value(self.kind, self.value)
        return self


class PayrollDeductionUpdate(BaseModel):
    """Partial update; `code` is immutable once created."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: PayrollDeductionKind | None = None
    value: CentAmountInput | None = Field(default=None, ge=Decimal("0.01"))
    active: bool | None = None

    @model_validator(mode="after")
    def _value_matches_kind_when_both_present(self) -> Self:
        # When only one of the pair is present the stored counterpart decides;
        # the service validates the merged rule.
        if self.kind is not None and self.value is not None:
            validate_deduction_value(self.kind, self.value)
        return self


class Salary(BaseModel):
    """One append-only salary record (amount and currency are PII)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    employee_id: UUID
    amount: Money
    currency: Currency
    effective_from: date
    created_at: datetime
    created_by: UUID


class SalaryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: UUID
    amount: CentAmountInput = Field(ge=Decimal("0.01"))
    effective_from: date


class PayrollRunTotals(BaseModel):
    """Run totals, the sum of payslip lines rounded per line (ADR 0017)."""

    base: Money
    overtime_pay: Money
    bonuses: Money
    deductions: Money
    net: Money


class PayrollRunSkip(BaseModel):
    """One employee omitted from a run, with the stable reason."""

    employee_id: UUID
    reason: PayrollRunSkipReason


class PayrollRun(BaseModel):
    """A monthly run attempt and its PII-free summary."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    period: Period
    status: PayrollRunStatus
    employee_count: int
    skipped_count: int
    skipped: list[PayrollRunSkip] = Field(default_factory=list)
    totals: PayrollRunTotals | None = None
    failed_code: str | None = None
    calculated_at: datetime | None = None
    finalized_at: datetime | None = None
    supersedes_run_id: UUID | None = None
    superseded_by_run_id: UUID | None = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class PayrollRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: Period


class Timesheet(BaseModel):
    """Informational time facts for one employee in one run (no proration)."""

    model_config = ConfigDict(from_attributes=True)

    employee_id: UUID
    period: Period
    worked_hours: DecimalNumber
    overtime_hours: DecimalNumber
    leave_days: int
    attendance_days: int


class PayslipDeductionLine(BaseModel):
    """One inspectable deduction line; `value` is the rule, `amount` the result."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    kind: PayrollDeductionKind
    value: Money
    amount: Money


class Payslip(BaseModel):
    """One employee's calculated pay for a run; `net` may be negative."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    employee_id: UUID
    period: Period
    status: PayslipStatus
    base: Money
    overtime_pay: Money
    bonuses: Money
    deductions: list[PayslipDeductionLine] = Field(default_factory=list)
    net: Money
    issued_at: datetime | None = None


class PayrollBonus(BaseModel):
    """A per-run, per-employee bonus entry (amount and note are PII)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    employee_id: UUID
    amount: Money
    note: str | None = None
    created_by: UUID
    created_at: datetime


class BonusCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: UUID
    amount: CentAmountInput = Field(ge=Decimal("0.01"))
    note: str | None = Field(default=None, min_length=1, max_length=200)


class SalaryListPage(BaseModel):
    items: list[Salary]
    page: Page


class PayrollRunListPage(BaseModel):
    items: list[PayrollRun]
    page: Page


class TimesheetListPage(BaseModel):
    items: list[Timesheet]
    page: Page


class PayslipListPage(BaseModel):
    items: list[Payslip]
    page: Page
