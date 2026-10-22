"""Wire contracts for the Overtime domain (T3 persistence + T4 domain).

The API contract is pinned by T1 (`.ai/features/overtime.md` → `## API
contract`). This module carries the persistence wire shape, the create/decision
inputs and the list page envelope.

`hours` is `Decimal` internally (ADR 0017) but serializes as a JSON **number**,
never a string — the same JSON-only serializer payroll uses, so the generated
client types hours as `number`. JSON `status`/`rejected_from` values are
lowercase end to end.

Writes reject unknown fields (`extra="forbid"`): there is no client-supplied
employee id, status or timestamp. `hours` is `0 < hours ≤ 12` with at most two
decimal places and `reason`/`note` are 1..500 characters when present; those
are schema `422`s. Date and state rules live in the service.

`reason`, `manager_note` and `hr_note` are PII (`.ai/security.md`): they are
returned to viewers with row visibility but must never be logged, emitted in
events or written into audit `changes` values.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

from app.shared.pagination import Page


def _json_number(value: Decimal) -> float:
    """Render a Decimal as a JSON number (ADR 0017 decision 5)."""
    return float(value)


# `Decimal` stays exact in Python; only the JSON representation becomes a
# number. Never use this for arithmetic.
DecimalHours = Annotated[
    Decimal,
    PlainSerializer(_json_number, return_type=float, when_used="json"),
    # OpenAPI is generated from the validation schema; Decimal's default is
    # `anyOf: [number, string]`. Pin it to a JSON number so the generated
    # client types hours as `number`, never `number | string` (ADR 0017).
    WithJsonSchema({"type": "number"}),
]

# Request-side hours stay a JSON number in the generated contract too; the
# bounds themselves are enforced by the `Field` constraints below.
OvertimeHoursInput = Annotated[
    Decimal,
    WithJsonSchema(
        {"type": "number", "exclusiveMinimum": 0, "maximum": 12},
    ),
]


class OvertimeRequestStatus(StrEnum):
    """Pinned request lifecycle (`REQUESTED` → `MANAGER_APPROVED` → …)."""

    REQUESTED = "requested"
    MANAGER_APPROVED = "manager_approved"
    HR_VERIFIED = "hr_verified"
    INCLUDED_IN_PAYROLL = "included_in_payroll"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class OvertimeRejectedFrom(StrEnum):
    """The observed pre-rejection state, recorded on rejected rows."""

    REQUESTED = "requested"
    MANAGER_APPROVED = "manager_approved"
    HR_VERIFIED = "hr_verified"


class OvertimeRequest(BaseModel):
    """One overtime claim and its decision trail (pinned response shape)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    employee_id: UUID
    work_date: date
    hours: DecimalHours
    status: OvertimeRequestStatus
    # PII — visibility is enforced by the service; never logged or emitted.
    reason: str | None = None
    manager_note: str | None = None
    hr_note: str | None = None
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    verified_by: UUID | None = None
    verified_at: datetime | None = None
    rejected_by: UUID | None = None
    rejected_at: datetime | None = None
    rejected_from: OvertimeRejectedFrom | None = None
    withdrawn_at: datetime | None = None
    included_in_run_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class OvertimeRequestCreate(BaseModel):
    """Submit a claim — always for the caller's linked employee (PO Decision 1).

    Overtime is a claim for hours already worked: the service refuses a future
    `work_date`; the bounds and precision here are schema `422`s.
    """

    model_config = ConfigDict(extra="forbid")

    work_date: date
    hours: OvertimeHoursInput = Field(
        gt=Decimal("0"),
        le=Decimal("12"),
        decimal_places=2,
    )
    reason: str | None = Field(default=None, min_length=1, max_length=500)


class OvertimeDecision(BaseModel):
    """Optional decision note — 1..500 characters when provided (PII).

    The service routes it to `manager_note` on the manager step (`requested`)
    and to `hr_note` on the HR step (`manager_approved`/`hr_verified`) (T1).
    """

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, min_length=1, max_length=500)


class OvertimeRequestListPage(BaseModel):
    items: list[OvertimeRequest]
    page: Page
