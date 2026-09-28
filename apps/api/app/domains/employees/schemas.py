"""Wire contracts for the Employees domain.

Read schemas are titled exactly `Employee`, `EmployeeSummary`, `EmployeeDetail`,
`Employment` (pinned — they drive the generated TypeScript types).

Semantics worth naming here (pinned in `.ai/features/employees.md`):

- Dates (`hired_at`, `effective_from`, `effective_to`, `effective_date`,
  `terminated_at`) are calendar dates; `created_at`/`updated_at` are date-times.
- `EmployeeDetail.current_employment` is `null` once every period is closed
  (terminated employees still read `200`); `employments` is newest first.
- `UpdateEmployee`: absent = unchanged; `phone` may be explicitly `null`;
  explicit `null` for name/email is a validation error.
- `TransferRequest`: absent `position_id`/`location_id`/`manager_id` inherit the
  current values; explicit `null` for position/location is rejected (an employee
  always has both), explicit `null` for `manager_id` clears the manager.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.shared.pagination import Page


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERN = "intern"


class EmployeeStatus(StrEnum):
    ACTIVE = "active"
    ON_LEAVE = "on_leave"  # reserved for Attendance — no endpoint sets it here
    TERMINATED = "terminated"


class Employment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    employee_id: UUID
    department_id: UUID
    position_id: UUID
    location_id: UUID
    employment_type: EmploymentType
    effective_from: date
    effective_to: date | None = None  # null = open period
    created_at: datetime


class Employee(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    employee_code: str
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    department_id: UUID
    position_id: UUID
    location_id: UUID
    manager_id: UUID | None = None
    employment_type: EmploymentType
    status: EmployeeStatus
    hired_at: date
    terminated_at: date | None = None
    created_at: datetime
    updated_at: datetime


class EmployeeSummary(BaseModel):
    """List rows — Employee minus tenant_id, phone, created_at, updated_at."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    employee_code: str
    first_name: str
    last_name: str
    email: str
    department_id: UUID
    position_id: UUID
    location_id: UUID
    manager_id: UUID | None = None
    employment_type: EmploymentType
    status: EmployeeStatus
    hired_at: date
    terminated_at: date | None = None


class EmployeeDetail(Employee):
    current_employment: Employment | None = None
    employments: list[Employment]


class CreateEmployee(BaseModel):
    first_name: str = Field(min_length=1, max_length=200)
    last_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    department_id: UUID
    position_id: UUID
    location_id: UUID
    manager_id: UUID | None = None
    employment_type: EmploymentType
    hired_at: date
    employee_code: str | None = Field(default=None, min_length=1, max_length=50)


class UpdateEmployee(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=200)
    last_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    phone: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def _reject_explicit_null_required(self) -> Self:
        for field in ("first_name", "last_name", "email"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class TransferRequest(BaseModel):
    department_id: UUID
    position_id: UUID | None = None
    location_id: UUID | None = None
    manager_id: UUID | None = None
    effective_from: date
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _reject_explicit_null_assignment(self) -> Self:
        for field in ("position_id", "location_id"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class TerminationRequest(BaseModel):
    effective_date: date
    reason: str = Field(min_length=1, max_length=500)


class EmployeeListPage(BaseModel):
    items: list[EmployeeSummary]
    page: Page


EmploymentList = list[Employment]
