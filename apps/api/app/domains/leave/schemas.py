"""Wire contracts for the Leave domain (pinned by T1, `.ai/features/leave.md`).

These read shapes are the vocabulary of the repository protocols and the API
response models. `LeaveRequest.status` and `LeaveType.code` serialize as the
pinned lowercase strings; `LeaveBalance.remaining_days` is derived so the
never-negative invariant has one expression.

`reason` and `decision_note` are returned as stored (the service applies the
visibility rule) but must never be logged or emitted in events/audit values
(`.ai/security.md`).
"""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field


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
