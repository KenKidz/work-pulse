"""Wire contracts for the Leave domain (pinned by T1, `.ai/features/leave.md`).

These read shapes are the vocabulary of the repository protocols and the API
response models. `LeaveRequest.status` and `LeaveType.code` serialize as the
pinned lowercase strings; `LeaveBalance.remaining_days` is derived so the
never-negative invariant has one expression.

`reason` and `decision_note` are returned as stored (the service applies the
visibility rule) but must never be logged or emitted in events/audit values
(`.ai/security.md`).

Writes (`LeaveRequestCreate`, `LeaveDecision`) reject unknown fields
(`extra="forbid"`) — the same trust posture as the attendance inputs: there is
no client-supplied employee id or timestamp. `end_date < start_date` is a
schema `422` (the pinned edge case allows schema or service rejection), while
`days` and every balance/date rule live in the service.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from app.shared.pagination import Page


class LeaveRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class LeaveTypeCode(StrEnum):
    ANNUAL = "annual"
    SICK = "sick"
    UNPAID = "unpaid"


class LeaveType(BaseModel):
    """Seeded leave type — the pinned wire shape (four fields, no defaults)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: LeaveTypeCode
    name: str
    counts_against_balance: bool


class LeaveRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    employee_id: UUID
    leave_type_id: UUID
    leave_type_code: LeaveTypeCode
    start_date: date
    end_date: date
    days: int
    status: LeaveRequestStatus
    reason: str | None = None
    decision_note: str | None = None
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LeaveBalance(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    employee_id: UUID
    leave_type_id: UUID
    year: int
    entitlement_days: int
    used_days: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def remaining_days(self) -> int:
        """Entitlement minus usage — never negative (DB-checked)."""
        return self.entitlement_days - self.used_days


class LeaveRequestCreate(BaseModel):
    """Submit a request — always for the caller's linked employee."""

    model_config = ConfigDict(extra="forbid")

    leave_type_id: UUID
    start_date: date
    end_date: date
    reason: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def _dates_ordered(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self


class LeaveDecision(BaseModel):
    """Optional decision note — 1..500 characters when provided (PII)."""

    model_config = ConfigDict(extra="forbid")

    decision_note: str | None = Field(default=None, min_length=1, max_length=500)


class LeaveRequestListPage(BaseModel):
    items: list[LeaveRequest]
    page: Page


LeaveTypeList = list[LeaveType]
LeaveBalanceList = list[LeaveBalance]
