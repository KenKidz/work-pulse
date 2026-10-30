"""Projection row and report DTOs for Analytics & Reports (T4/T5, ADR 0019/0024).

These are internal value types, not the HTTP contract (T5 owns the pinned
request/response models). The row types mirror the ADR 0019 ClickHouse tables
one-to-one; `updated_at` is deliberately absent because it is the CDC writer's
bookkeeping version (the source commit timestamp), never a report fact.

Two invariants live here:

- Rows carry identifiers, dates, counts and hours only — no names, emails,
  salaries or leave reasons (`.ai/security.md`). Display labels are resolved at
  query time through a read-only port (ADR 0019 decision 8).
- Seniority bands are a closed vocabulary (`SENIORITY_BANDS`), so the SQL
  expression and the read-side Python twin cannot drift on band codes.
"""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, ClassVar, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PrivateAttr,
    WithJsonSchema,
    model_validator,
)

#: Overtime states the projection can observe. `included_in_payroll` is
#: deliberately absent: payroll's flip emits no domain event (ADR 0018
#: decision 6), and the CDC sink maps the OLTP state back to `hr_verified`
#: (ADR 0024 D6), so a paid claim is counted exactly once.
OVERTIME_STATUSES = (
    "requested",
    "manager_approved",
    "hr_verified",
    "rejected",
    "withdrawn",
)

#: Seniority band codes, ordered youngest tenure first.
SENIORITY_BANDS = ("lt_1y", "1_3y", "3_5y", "5y_plus")

#: No tenure anchor (a pre-extension `employee.created`) degrades honestly.
UNKNOWN_SENIORITY_BAND = "unknown"

#: Human labels for the report envelope and the web surface (T5/T7).
SENIORITY_BAND_LABELS: dict[str, str] = {
    "lt_1y": "< 1 year",
    "1_3y": "1–<3 years",
    "3_5y": "3–<5 years",
    "5y_plus": "5+ years",
    "unknown": "Unknown",
}

#: The group-by dimensions every report accepts (the leave report additionally
#: accepts `leave_type`; the API validates that in T5).
GROUP_BY_DIMENSIONS = ("department", "location", "position", "seniority")
LEAVE_GROUP_BY_DIMENSIONS = (*GROUP_BY_DIMENSIONS, "leave_type")

#: Report time bucketing. `total` produces a single `bucket = None` row.
REPORT_INTERVALS = ("day", "week", "month", "total")
#: The empty dimension-key placeholder (missing/unresolvable dimension).
UNKNOWN_GROUP_KEY = "unknown"


@dataclass(frozen=True, slots=True)
class EmployeeRow:
    """One `dim_employee` row (current identity/employment state)."""

    tenant_id: UUID
    employee_id: UUID
    hired_at: date | None = None
    terminated_at: date | None = None


@dataclass(frozen=True, slots=True)
class AssignmentRow:
    """One `dim_assignment` interval; periods are half-open `[from, to)`."""

    tenant_id: UUID
    employee_id: UUID
    effective_from: date
    effective_to: date | None = None
    department_id: UUID | None = None
    position_id: UUID | None = None
    location_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class OvertimeFact:
    """One `fact_overtime` row: one overtime request, current state."""

    tenant_id: UUID
    overtime_id: UUID
    employee_id: UUID
    work_date: date
    hours: Decimal
    status: str
    department_id: UUID | None = None
    position_id: UUID | None = None
    location_id: UUID | None = None
    hired_at: date | None = None


@dataclass(frozen=True, slots=True)
class AttendanceFact:
    """One `fact_attendance` row: `checked_in` + `checked_out` merged."""

    tenant_id: UUID
    attendance_id: UUID
    employee_id: UUID
    workday: date
    duration_minutes: int | None
    late: bool
    minutes_late: int | None = None
    department_id: UUID | None = None
    position_id: UUID | None = None
    location_id: UUID | None = None
    hired_at: date | None = None


@dataclass(frozen=True, slots=True)
class LeaveFact:
    """One `fact_leave` row: one approved leave request (whole days)."""

    tenant_id: UUID
    leave_request_id: UUID
    employee_id: UUID
    start_date: date
    leave_type_code: str
    approved_day_count: int
    department_id: UUID | None = None
    position_id: UUID | None = None
    location_id: UUID | None = None
    hired_at: date | None = None


# --- Read-side aggregate boundary (implemented by the T5 service) ---------


@dataclass(frozen=True, slots=True)
class ReportQuery:
    """One validated report query, already policy-scoped.

    `employee_scope` is the manager's self + direct-reports set; `None` means
    company-wide (HR/Finance/Admin/Super Admin). An empty tuple is a real
    scope with no employees and must return no rows, never company-wide data.
    """

    tenant_id: UUID
    start: date
    end: date
    interval: str
    group_by: tuple[str, ...]
    department_id: UUID | None = None
    location_id: UUID | None = None
    position_id: UUID | None = None
    employee_scope: tuple[UUID, ...] | None = None


@dataclass(frozen=True, slots=True)
class OvertimeAggregateRow:
    """Verified overtime aggregates for one bucket × group row."""

    bucket: date | None
    keys: tuple[str, ...]
    verified_hours: Decimal
    verified_request_count: int
    verified_day_count: int
    active_employee_count: int


@dataclass(frozen=True, slots=True)
class AttendanceAggregateRow:
    """Attendance aggregates; the service derives `late_rate`/`avg_minutes_late`."""

    bucket: date | None
    keys: tuple[str, ...]
    checkin_count: int
    worked_day_count: int
    worked_minutes: int
    late_count: int
    minutes_late_total: int


@dataclass(frozen=True, slots=True)
class LeaveAggregateRow:
    """Approved leave aggregates for one bucket × group row."""

    bucket: date | None
    keys: tuple[str, ...]
    approved_day_count: int
    approved_request_count: int


@dataclass(frozen=True, slots=True)
class HeadcountAggregateRow:
    """Headcount metrics at the bucket's as-of date."""

    bucket: date | None
    keys: tuple[str, ...]
    headcount: int
    joiners: int
    leavers: int


# --- HTTP contract (pinned in `.ai/features/analytics-reports.md`) ---------


class ReportDimension(StrEnum):
    """Every `group_by` dimension the four reports accept."""

    DEPARTMENT = "department"
    LOCATION = "location"
    POSITION = "position"
    SENIORITY = "seniority"
    LEAVE_TYPE = "leave_type"


class ReportInterval(StrEnum):
    """Time bucketing; `week` starts Monday, `total` has `bucket = null`."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    TOTAL = "total"


def _decimal_string(value: Decimal) -> str:
    """Render a Decimal at its quantized scale (ADR 0019 decision 9)."""
    return str(value)


# ADR 0019 decision 9 pins the report wire format: fixed-scale decimal
# **strings** (hours 2dp, `late_rate` 4dp, `avg_minutes_late` 1dp). ADR 0017's
# JSON-number rule is the money wire rule and is deliberately untouched — these
# are hours and rates, not amounts. `Decimal` stays exact in the service and the
# repository/derived-metric code quantizes before construction, so `str`
# preserves trailing zeros for the typed client and the web's formatting.
ReportDecimal = Annotated[
    Decimal,
    PlainSerializer(_decimal_string, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string"}),
]


def add_months(value: date, months: int) -> date:
    """Calendar-month addition, clamping a short month (Jan 31 + 1 → Feb 28)."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def parse_group_by(value: str) -> tuple[str, ...]:
    """Parse and validate the comma-separated `group_by` list.

    One to three distinct dimensions, in row order. Unknown names and
    duplicates are rejected here so every report kind shares one parser; the
    report-specific vocabulary (`leave_type` is leave-only) is enforced by
    `ReportQueryParams.allowed_dimensions`.
    """
    parts = tuple(part.strip() for part in value.split(","))
    if not parts or any(not part for part in parts):
        raise ValueError("group_by must list 1 to 3 dimensions")
    if len(parts) > 3:
        raise ValueError("group_by accepts at most 3 dimensions")
    if len(set(parts)) != len(parts):
        raise ValueError("group_by dimensions must be distinct")
    if not set(parts).issubset(LEAVE_GROUP_BY_DIMENSIONS):
        raise ValueError("group_by contains an unknown dimension")
    return parts


class ReportQueryParams(BaseModel):
    """The shared query surface of the four report endpoints.

    `from`/`to` are aliases of `start`/`end` (the pinned wire names). The range
    is inclusive and capped at 24 calendar months. The service turns a validated
    instance into a `ReportQuery`; policy scoping happens there, never here.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    #: Dimensions accepted by this report kind (leave adds `leave_type`).
    allowed_dimensions: ClassVar[frozenset[str]] = frozenset(GROUP_BY_DIMENSIONS)

    start: date = Field(alias="from")
    end: date = Field(alias="to")
    group_by: str = "department"
    interval: ReportInterval = ReportInterval.MONTH
    department_id: UUID | None = None
    location_id: UUID | None = None
    position_id: UUID | None = None

    _dimensions: tuple[str, ...] = PrivateAttr(default=())

    @model_validator(mode="after")
    def _validate_range_and_grouping(self) -> Self:
        if self.start > self.end:
            raise ValueError("'from' must be on or before 'to'")
        if self.end > add_months(self.start, 24):
            raise ValueError("the report range cannot exceed 24 months")
        dimensions = parse_group_by(self.group_by)
        if not set(dimensions).issubset(type(self).allowed_dimensions):
            raise ValueError("group_by contains a dimension unsupported by this report")
        self._dimensions = dimensions
        return self

    @property
    def dimensions(self) -> tuple[str, ...]:
        """The validated dimension names, in row order."""
        return self._dimensions


class LeaveReportParams(ReportQueryParams):
    """The leave report additionally groups by `leave_type` (PO Decision 8)."""

    allowed_dimensions: ClassVar[frozenset[str]] = frozenset(LEAVE_GROUP_BY_DIMENSIONS)


class ReportGroup(BaseModel):
    """One group-by dimension value on a report row."""

    dimension: ReportDimension
    key: str
    label: str


class OvertimeReportRow(BaseModel):
    """Verified-overtime metrics for one bucket × group."""

    bucket: date | None
    groups: list[ReportGroup]
    verified_hours: ReportDecimal
    verified_request_count: int
    verified_day_count: int
    active_employee_count: int
    hours_per_employee: ReportDecimal


class AttendanceReportRow(BaseModel):
    """Attendance/lateness metrics for one bucket × group."""

    bucket: date | None
    groups: list[ReportGroup]
    checkin_count: int
    worked_day_count: int
    worked_minutes: int
    late_count: int
    late_rate: ReportDecimal
    avg_minutes_late: ReportDecimal


class LeaveReportRow(BaseModel):
    """Approved-leave metrics for one bucket × group."""

    bucket: date | None
    groups: list[ReportGroup]
    approved_day_count: int
    approved_request_count: int


class HeadcountReportRow(BaseModel):
    """Workforce metrics at the bucket's as-of date."""

    bucket: date | None
    groups: list[ReportGroup]
    headcount: int
    joiners: int
    leavers: int


class _ReportEnvelope(BaseModel):
    """Fields shared by the four report envelopes."""

    model_config = ConfigDict(populate_by_name=True)

    start: date = Field(alias="from")
    end: date = Field(alias="to")
    group_by: list[ReportDimension]
    interval: ReportInterval


class OvertimeReport(_ReportEnvelope):
    """`GET /api/v1/reports/overtime` response."""

    rows: list[OvertimeReportRow]


class AttendanceReport(_ReportEnvelope):
    """`GET /api/v1/reports/attendance` response."""

    rows: list[AttendanceReportRow]


class LeaveReport(_ReportEnvelope):
    """`GET /api/v1/reports/leave` response."""

    rows: list[LeaveReportRow]


class HeadcountReport(_ReportEnvelope):
    """`GET /api/v1/reports/headcount` response."""

    rows: list[HeadcountReportRow]
