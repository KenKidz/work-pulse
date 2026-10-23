"""Pure fold-rule tests for the analytics projection (T4, ADR 0019).

Pinned expectations from `.ai/features/analytics-reports.md` (business rules
3–8, edge cases) and ADR 0019 decisions 4–5:

- seniority bands switch on the anniversary (Feb 29 clamps to Feb 28) and
  missing tenure degrades to `unknown`;
- dimension intervals close/open on the transfer date, carry forward missing
  position/location (pre-extension), and every transfer bounds a re-stamp;
- overtime is counted only while `hr_verified`: an HR un-verify flips the
  status, payroll's inclusion flip is invisible, and redelivery is a no-op;
- attendance merges check-in + check-out, and a never-checked-out record keeps
  `duration_minutes = None`;
- approved leave counts whole days anchored on `start_date`;
- duplicate application of any event returns an equal row.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from app.domains.attendance.events import (
    attendance_checked_in,
    attendance_checked_out,
)
from app.domains.employees.events import (
    employee_created,
    employee_terminated,
    employee_transferred,
)
from app.domains.leave.events import leave_approved
from app.domains.overtime.events import (
    overtime_approved,
    overtime_rejected,
    overtime_requested,
    overtime_verified,
    overtime_withdrawn,
)
from app.domains.reports.folds import (
    FoldError,
    fold_attendance,
    fold_employee_created,
    fold_employee_terminated,
    fold_employee_transferred,
    fold_leave,
    fold_overtime,
    resolve_assignment,
    seniority_band,
    stamp_for,
)
from app.domains.reports.schemas import (
    AssignmentRow,
    DimensionState,
    EmployeeRow,
)

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000001")
ACTOR = UUID("00000000-0000-4000-8000-000000000031")
DEPARTMENT_A = UUID("00000000-0000-4000-8000-0000000000d1")
DEPARTMENT_B = UUID("00000000-0000-4000-8000-0000000000d2")
POSITION_A = UUID("00000000-0000-4000-8000-0000000000e1")
POSITION_B = UUID("00000000-0000-4000-8000-0000000000e2")
LOCATION_A = UUID("00000000-0000-4000-8000-0000000000f1")
LOCATION_B = UUID("00000000-0000-4000-8000-0000000000f2")
OVERTIME = UUID("00000000-0000-4000-8000-0000000000c1")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000c2")
LEAVE_REQUEST = UUID("00000000-0000-4000-8000-0000000000c3")

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
HIRED_AT = date(2026, 1, 5)


def _created(hired_at: date | None = HIRED_AT) -> dict[str, Any]:
    return employee_created(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        employee_id=EMPLOYEE,
        department_id=DEPARTMENT_A,
        position_id=POSITION_A,
        location_id=LOCATION_A,
        employment_type="full_time",
        hired_at=hired_at,
    )


def _dimensions(
    *, hired_at: date | None = HIRED_AT, started: date = HIRED_AT
) -> DimensionState:
    employee = EmployeeRow(tenant_id=TENANT, employee_id=EMPLOYEE, hired_at=hired_at)
    assignment = AssignmentRow(
        tenant_id=TENANT,
        employee_id=EMPLOYEE,
        effective_from=started,
        department_id=DEPARTMENT_A,
        position_id=POSITION_A,
        location_id=LOCATION_A,
    )
    return DimensionState(employee=employee, assignments=(assignment,))


def _transferred(
    *,
    effective_from: date,
    to_department_id: UUID = DEPARTMENT_B,
    to_position_id: UUID | None = POSITION_B,
    to_location_id: UUID | None = LOCATION_B,
) -> dict[str, Any]:
    return employee_transferred(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        employee_id=EMPLOYEE,
        from_department_id=DEPARTMENT_A,
        to_department_id=to_department_id,
        effective_from=effective_from,
        to_position_id=to_position_id,
        to_location_id=to_location_id,
    )


# --- Seniority ------------------------------------------------------------


def test_seniority_bands_switch_on_the_anniversary() -> None:
    hired = date(2025, 3, 15)

    assert seniority_band(hired, date(2026, 3, 14)) == "lt_1y"
    assert seniority_band(hired, date(2026, 3, 15)) == "1_3y"
    assert seniority_band(hired, date(2028, 3, 14)) == "1_3y"
    assert seniority_band(hired, date(2028, 3, 15)) == "3_5y"
    assert seniority_band(hired, date(2030, 3, 14)) == "3_5y"
    assert seniority_band(hired, date(2030, 3, 15)) == "5y_plus"


def test_seniority_band_clamps_a_leap_day_anniversary() -> None:
    hired = date(2024, 2, 29)

    assert seniority_band(hired, date(2025, 2, 27)) == "lt_1y"
    assert seniority_band(hired, date(2025, 2, 28)) == "1_3y"
    assert seniority_band(hired, date(2027, 2, 27)) == "1_3y"
    assert seniority_band(hired, date(2027, 2, 28)) == "3_5y"
    assert seniority_band(hired, date(2029, 2, 27)) == "3_5y"
    assert seniority_band(hired, date(2029, 2, 28)) == "5y_plus"


def test_missing_tenure_degrades_to_unknown() -> None:
    assert seniority_band(None, date(2026, 10, 23)) == "unknown"
    assert seniority_band(HIRED_AT, None) == "unknown"


# --- Employee dimension folds --------------------------------------------


def test_created_with_hired_at_opens_the_initial_assignment() -> None:
    fold = fold_employee_created(_created())

    assert fold.employee == EmployeeRow(
        tenant_id=TENANT, employee_id=EMPLOYEE, hired_at=HIRED_AT
    )
    [assignment] = fold.assignments
    assert assignment.effective_from == HIRED_AT
    assert assignment.effective_to is None
    assert assignment.department_id == DEPARTMENT_A
    assert assignment.position_id == POSITION_A
    assert assignment.location_id == LOCATION_A
    assert fold.restamp_from is None


def test_pre_extension_created_starts_at_occurred_at_and_keeps_facts_visible() -> None:
    envelope = _created()
    del envelope["data"]["hired_at"]

    fold = fold_employee_created(envelope)

    assert fold.employee is not None
    assert fold.employee.hired_at is None
    # The interval cannot know the real hire date; it starts first-seen.
    assert fold.assignments[0].effective_from == NOW.date()

    state = DimensionState(employee=fold.employee, assignments=fold.assignments)
    stamp = stamp_for(state, date(2026, 2, 1))
    assert stamp.department_id == DEPARTMENT_A
    assert stamp.position_id == POSITION_A
    assert stamp.location_id == LOCATION_A
    assert stamp.hired_at is None
    assert seniority_band(stamp.hired_at, date(2026, 2, 1)) == "unknown"


def test_duplicate_create_does_not_reopen_a_terminated_employee() -> None:
    terminated = EmployeeRow(
        tenant_id=TENANT,
        employee_id=EMPLOYEE,
        hired_at=HIRED_AT,
        terminated_at=date(2026, 6, 30),
    )

    fold = fold_employee_created(_created(), prior=terminated)

    assert fold.employee == terminated
    assert fold.assignments == ()


def test_transfer_closes_the_previous_interval_and_opens_the_next() -> None:
    state = _dimensions()
    effective_from = date(2026, 3, 1)

    fold = fold_employee_transferred(
        _transferred(effective_from=effective_from),
        prior=state.employee,
        assignments=state.assignments,
    )

    closed, opened = fold.assignments
    assert closed.effective_to == effective_from
    assert opened.effective_from == effective_from
    assert opened.effective_to is None
    assert opened.department_id == DEPARTMENT_B
    assert opened.position_id == POSITION_B
    assert opened.location_id == LOCATION_B
    assert fold.restamp_from == effective_from

    updated = DimensionState(
        employee=fold.employee,
        assignments=(closed, opened),
    )
    assert resolve_assignment(updated.assignments, date(2026, 2, 28)) == closed
    assert resolve_assignment(updated.assignments, date(2026, 3, 1)) == opened


def test_pre_extension_transfer_carries_position_and_location_forward() -> None:
    state = _dimensions()
    envelope = _transferred(
        effective_from=date(2026, 3, 1),
        to_position_id=None,
        to_location_id=None,
    )

    fold = fold_employee_transferred(
        envelope, prior=state.employee, assignments=state.assignments
    )

    _, opened = fold.assignments
    assert opened.department_id == DEPARTMENT_B
    assert opened.position_id == POSITION_A
    assert opened.location_id == LOCATION_A


def test_backdated_transfer_bounds_the_restamp_from_the_effective_date() -> None:
    state = _dimensions()

    fold = fold_employee_transferred(
        _transferred(effective_from=date(2026, 2, 15)),
        prior=state.employee,
        assignments=state.assignments,
    )

    assert fold.restamp_from == date(2026, 2, 15)
    updated = DimensionState(
        employee=fold.employee,
        assignments=tuple(fold.assignments),
    )
    before = stamp_for(updated, date(2026, 2, 14))
    after = stamp_for(updated, date(2026, 2, 15))
    assert before.department_id == DEPARTMENT_A
    assert after.department_id == DEPARTMENT_B


def test_termination_closes_the_open_interval_and_keeps_the_earliest_date() -> None:
    state = _dimensions()
    envelope = employee_terminated(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        employee_id=EMPLOYEE,
        effective_date=date(2026, 6, 30),
    )

    fold = fold_employee_terminated(
        envelope, prior=state.employee, assignments=state.assignments
    )

    assert fold.employee is not None
    assert fold.employee.terminated_at == date(2026, 6, 30)
    [closed] = fold.assignments
    assert closed.effective_to == date(2026, 6, 30)

    duplicate = fold_employee_terminated(
        envelope, prior=fold.employee, assignments=fold.assignments
    )
    assert duplicate.employee == fold.employee
    assert duplicate.assignments == ()


# --- Overtime -------------------------------------------------------------


def _overtime_requested() -> dict[str, Any]:
    return overtime_requested(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        overtime_id=OVERTIME,
        employee_id=EMPLOYEE,
        work_date=date(2026, 2, 10),
        hours=Decimal("2.50"),
    )


def test_overtime_counts_only_while_hr_verified() -> None:
    dimensions = _dimensions()
    requested = fold_overtime(
        "overtime.requested", _overtime_requested(), dimensions=dimensions
    )
    assert requested is not None
    assert requested.status == "requested"
    assert requested.hours == Decimal("2.50")
    assert requested.department_id == DEPARTMENT_A

    approved = fold_overtime(
        "overtime.approved",
        overtime_approved(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            overtime_id=OVERTIME,
            employee_id=EMPLOYEE,
            work_date=date(2026, 2, 10),
            hours=Decimal("2.50"),
            approved_by=ACTOR,
        ),
        prior=requested,
        dimensions=dimensions,
    )
    assert approved is not None and approved.status == "manager_approved"

    verified = fold_overtime(
        "overtime.verified",
        overtime_verified(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            overtime_id=OVERTIME,
            employee_id=EMPLOYEE,
            work_date=date(2026, 2, 10),
            hours=Decimal("2.50"),
            verified_by=ACTOR,
        ),
        prior=approved,
        dimensions=dimensions,
    )
    assert verified is not None and verified.status == "hr_verified"

    unverified = fold_overtime(
        "overtime.rejected",
        overtime_rejected(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            overtime_id=OVERTIME,
            employee_id=EMPLOYEE,
            work_date=date(2026, 2, 10),
            hours=Decimal("2.50"),
            rejected_by=ACTOR,
            rejected_from="hr_verified",
        ),
        prior=verified,
        dimensions=dimensions,
    )
    assert unverified is not None
    # The read side counts `hr_verified` only; the hours have left that state.
    assert unverified.status == "rejected"


def test_overtime_redelivery_is_a_no_op() -> None:
    dimensions = _dimensions()
    requested = fold_overtime(
        "overtime.requested", _overtime_requested(), dimensions=dimensions
    )
    assert requested is not None

    again = fold_overtime(
        "overtime.requested",
        _overtime_requested(),
        prior=requested,
        dimensions=dimensions,
    )
    assert again == requested


def test_verified_can_advance_a_gap_in_retained_history() -> None:
    dimensions = _dimensions()
    requested = fold_overtime(
        "overtime.requested", _overtime_requested(), dimensions=dimensions
    )
    assert requested is not None

    verified = fold_overtime(
        "overtime.verified",
        overtime_verified(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            overtime_id=OVERTIME,
            employee_id=EMPLOYEE,
            work_date=date(2026, 2, 10),
            hours=Decimal("2.50"),
            verified_by=ACTOR,
        ),
        prior=requested,
        dimensions=dimensions,
    )
    assert verified is not None
    assert verified.status == "hr_verified"


def test_terminal_overtime_never_moves_backwards() -> None:
    dimensions = _dimensions()
    requested = fold_overtime(
        "overtime.requested", _overtime_requested(), dimensions=dimensions
    )
    assert requested is not None
    rejected = fold_overtime(
        "overtime.rejected",
        overtime_rejected(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            overtime_id=OVERTIME,
            employee_id=EMPLOYEE,
            work_date=date(2026, 2, 10),
            hours=Decimal("2.50"),
            rejected_by=ACTOR,
            rejected_from="requested",
        ),
        prior=requested,
        dimensions=dimensions,
    )
    assert rejected is not None and rejected.status == "rejected"

    approved = fold_overtime(
        "overtime.approved",
        overtime_approved(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            overtime_id=OVERTIME,
            employee_id=EMPLOYEE,
            work_date=date(2026, 2, 10),
            hours=Decimal("2.50"),
            approved_by=ACTOR,
        ),
        prior=rejected,
        dimensions=dimensions,
    )
    assert approved == rejected


def test_payroll_inclusion_flip_is_invisible() -> None:
    dimensions = _dimensions()
    row = fold_overtime(
        "overtime.verified",
        overtime_verified(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            overtime_id=OVERTIME,
            employee_id=EMPLOYEE,
            work_date=date(2026, 2, 10),
            hours=Decimal("2.50"),
            verified_by=ACTOR,
        ),
        dimensions=dimensions,
    )
    assert row is not None and row.status == "hr_verified"

    # Payroll's flip emits no event (ADR 0018 decision 6); a rogue one must not
    # move the row, so a paid claim stays counted exactly once.
    flipped = fold_overtime(
        "overtime.included_in_payroll",
        {
            "event_type": "overtime.included_in_payroll",
            "tenant_id": str(TENANT),
            "occurred_at": NOW.isoformat(),
            "data": {
                "overtime_id": str(OVERTIME),
                "employee_id": str(EMPLOYEE),
                "work_date": "2026-02-10",
                "hours": 2.5,
            },
        },
        prior=row,
        dimensions=dimensions,
    )
    assert flipped == row


def test_withdraw_after_verification_is_ignored() -> None:
    dimensions = _dimensions()
    verified = fold_overtime(
        "overtime.verified",
        overtime_verified(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            overtime_id=OVERTIME,
            employee_id=EMPLOYEE,
            work_date=date(2026, 2, 10),
            hours=Decimal("2.50"),
            verified_by=ACTOR,
        ),
        dimensions=dimensions,
    )
    assert verified is not None

    withdrawn = fold_overtime(
        "overtime.withdrawn",
        overtime_withdrawn(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            overtime_id=OVERTIME,
            employee_id=EMPLOYEE,
            work_date=date(2026, 2, 10),
        ),
        prior=verified,
        dimensions=dimensions,
    )
    assert withdrawn == verified


def test_withdraw_without_prior_contributes_nothing() -> None:
    assert (
        fold_overtime(
            "overtime.withdrawn",
            overtime_withdrawn(
                tenant_id=TENANT,
                actor_id=ACTOR,
                occurred_at=NOW,
                overtime_id=OVERTIME,
                employee_id=EMPLOYEE,
                work_date=date(2026, 2, 10),
            ),
            dimensions=DimensionState(),
        )
        is None
    )


def test_malformed_overtime_payload_raises_a_fold_error() -> None:
    envelope = _overtime_requested()
    del envelope["data"]["hours"]

    with pytest.raises(FoldError):
        fold_overtime("overtime.requested", envelope, dimensions=DimensionState())


# --- Attendance -----------------------------------------------------------


def test_check_in_without_check_out_has_no_worked_minutes() -> None:
    fact = fold_attendance(
        "attendance.checked_in",
        attendance_checked_in(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            attendance_id=ATTENDANCE,
            employee_id=EMPLOYEE,
            workday=date(2026, 2, 10),
            work_mode="onsite",
            late=True,
            minutes_late=12,
        ),
        dimensions=_dimensions(),
    )

    assert fact is not None
    assert fact.duration_minutes is None
    assert fact.late is True
    assert fact.minutes_late == 12
    assert fact.department_id == DEPARTMENT_A


def test_check_out_merges_into_the_open_record() -> None:
    checkin = fold_attendance(
        "attendance.checked_in",
        attendance_checked_in(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            attendance_id=ATTENDANCE,
            employee_id=EMPLOYEE,
            workday=date(2026, 2, 10),
            work_mode="onsite",
            late=False,
            minutes_late=None,
        ),
        dimensions=_dimensions(),
    )
    assert checkin is not None

    checkout = fold_attendance(
        "attendance.checked_out",
        attendance_checked_out(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            attendance_id=ATTENDANCE,
            employee_id=EMPLOYEE,
            workday=date(2026, 2, 10),
            duration_minutes=480,
            late=False,
            minutes_late=None,
        ),
        prior=checkin,
        dimensions=_dimensions(),
    )
    assert checkout is not None
    assert checkout.duration_minutes == 480
    assert checkout.department_id == DEPARTMENT_A

    # A replayed check-in after the close must not reopen the record.
    replayed = fold_attendance(
        "attendance.checked_in",
        attendance_checked_in(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            attendance_id=ATTENDANCE,
            employee_id=EMPLOYEE,
            workday=date(2026, 2, 10),
            work_mode="onsite",
            late=False,
            minutes_late=None,
        ),
        prior=checkout,
        dimensions=_dimensions(),
    )
    assert replayed == checkout


def test_pre_extension_check_out_keeps_the_check_in_lateness() -> None:
    checkin = fold_attendance(
        "attendance.checked_in",
        attendance_checked_in(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            attendance_id=ATTENDANCE,
            employee_id=EMPLOYEE,
            workday=date(2026, 2, 10),
            work_mode="onsite",
            late=True,
            minutes_late=7,
        ),
        dimensions=_dimensions(),
    )
    assert checkin is not None

    envelope = attendance_checked_out(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        attendance_id=ATTENDANCE,
        employee_id=EMPLOYEE,
        workday=date(2026, 2, 10),
        duration_minutes=480,
        late=True,
        minutes_late=7,
    )
    del envelope["data"]["late"]
    del envelope["data"]["minutes_late"]

    checkout = fold_attendance(
        "attendance.checked_out", envelope, prior=checkin, dimensions=_dimensions()
    )
    assert checkout is not None
    assert checkout.duration_minutes == 480
    assert checkout.late is True
    assert checkout.minutes_late == 7


def test_irrelevant_attendance_event_is_ignored() -> None:
    assert (
        fold_attendance("attendance.late", {"data": {}}, dimensions=DimensionState())
        is None
    )


# --- Leave ----------------------------------------------------------------


def test_approved_leave_counts_whole_days_on_the_start_date() -> None:
    fact = fold_leave(
        "leave.approved",
        leave_approved(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            leave_request_id=LEAVE_REQUEST,
            employee_id=EMPLOYEE,
            leave_type_code="annual",
            start_date=date(2026, 2, 27),
            end_date=date(2026, 3, 3),
            days=5,
            decided_by=ACTOR,
        ),
        dimensions=_dimensions(),
    )

    assert fact is not None
    assert fact.start_date == date(2026, 2, 27)
    assert fact.approved_day_count == 5
    assert fact.leave_type_code == "annual"
    assert fact.department_id == DEPARTMENT_A


def test_rejected_leave_contributes_nothing() -> None:
    assert (
        fold_leave(
            "leave.rejected",
            {"data": {}},
            dimensions=DimensionState(),
        )
        is None
    )
