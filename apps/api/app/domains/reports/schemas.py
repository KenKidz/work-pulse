"""Projection and report DTOs for Analytics & Reports (T4, ADR 0019).

These are internal value types, not the HTTP contract (T5 owns the pinned
request/response models). The fold rows mirror the ADR 0019 ClickHouse tables
one-to-one; `updated_at` is deliberately absent because it is the writer's
bookkeeping version, never a fold input or a report fact.

Two invariants live here:

- Rows carry identifiers, dates, counts and hours only — no names, emails,
  salaries or leave reasons (`.ai/security.md`). Display labels are resolved at
  query time through a read-only port (ADR 0019 decision 8).
- Seniority bands are a closed vocabulary (`SENIORITY_BANDS`), so the fold and
  the read service cannot drift on band codes.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

#: Overtime states the projection can observe from the event stream.
#: `included_in_payroll` is deliberately absent: payroll's flip emits no event
#: (ADR 0018 decision 6), so a paid claim stays `hr_verified` and is counted
#: exactly once.
OVERTIME_STATUSES = (
    "requested",
    "manager_approved",
    "hr_verified",
    "rejected",
    "withdrawn",
)

#: Which overtime states may transition where. Terminal side exits are
#: reachable only from the open pre-verification states; a rejection may also
#: un-verify an `hr_verified` claim (ADR 0018 / spec rule 5).
OVERTIME_TRANSITIONS: dict[str, frozenset[str]] = {
    "manager_approved": frozenset({"requested"}),
    # Forward-jumps are tolerated (a gap in retained history must not hide
    # verified hours); nothing moves a terminal row back to an open state.
    "hr_verified": frozenset({"requested", "manager_approved"}),
    "rejected": frozenset({"requested", "manager_approved", "hr_verified"}),
    "withdrawn": frozenset({"requested", "manager_approved"}),
}

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
class DimensionState:
    """The employee's folded dimension state, as loaded for one fold.

    `employee` is `None` when the stream did not (or no longer) includes the
    employee's `employee.created`; facts still project with `unknown`
    dimensions rather than disappearing (honest degradation, spec rule 3).
    """

    employee: EmployeeRow | None = None
    assignments: tuple[AssignmentRow, ...] = ()


@dataclass(frozen=True, slots=True)
class DimensionFold:
    """The rows one employee event contributes, plus an optional re-stamp.

    `assignments` holds only the intervals the event changed (a closed interval
    and/or the opened one); the writer upserts them as complete rows.
    `restamp_from` is set by a transfer: every fact dated on or after
    `effective_from` re-resolves its dimension stamps (backdated-transfer
    correction, ADR 0019 decision 5). The writer executes that bounded pass.
    """

    employee: EmployeeRow | None = None
    assignments: tuple[AssignmentRow, ...] = ()
    restamp_from: date | None = None


@dataclass(frozen=True, slots=True)
class DimensionStamp:
    """The dimension triple and tenure anchor resolved for one fact date."""

    department_id: UUID | None
    position_id: UUID | None
    location_id: UUID | None
    hired_at: date | None


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


# --- Read-side aggregate boundary (T5 implements) -------------------------


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
