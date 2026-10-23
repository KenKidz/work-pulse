"""Pure fold rules for the Analytics & Reports projection (T4, ADR 0019).

Every function is a pure function of `(prior row, envelope)` (plus the
employee's folded dimension state for fact folds): no I/O, no wall-clock reads,
no mutation. Re-applying a transition that is already current returns an equal
row, which is how the projection is idempotent under at-least-once redelivery
without a dedupe table (ADR 0019 decision 4).

Malformed payloads raise `FoldError` (a `ValueError`); the worker catches it,
logs ids-only and skips the message — a consumer never poisons its stream. The
fold set is exactly the consumed events from the spec: employee dimension state,
attendance facts, approved leave facts and the overtime state machine.
Everything else (`attendance.late`, `leave.*` other than `approved`,
`payroll.*`, `notification.*`, a rogue `overtime.included_in_payroll`) is
ignored: payroll's inclusion flip emits no event (ADR 0018 decision 6), so a
paid claim stays `hr_verified` and counted exactly once.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.domains.reports.schemas import (
    OVERTIME_TRANSITIONS,
    UNKNOWN_SENIORITY_BAND,
    AssignmentRow,
    AttendanceFact,
    DimensionFold,
    DimensionStamp,
    DimensionState,
    EmployeeRow,
    LeaveFact,
    OvertimeFact,
)

_HUNDREDTH = Decimal("0.01")

#: Event type → target overtime status. `overtime.requested` is handled
#: separately (it only creates a row, never moves an existing one).
_OVERTIME_EVENT_TARGETS = {
    "overtime.approved": "manager_approved",
    "overtime.verified": "hr_verified",
    "overtime.rejected": "rejected",
    "overtime.withdrawn": "withdrawn",
}


class FoldError(ValueError):
    """A known event whose required payload fields cannot be mapped.

    The worker treats it as a malformed message: ids-only log, skip, advance the
    offset (ADR 0019 decision 6). Messages name the field, never its value.
    """


# --- Envelope parsing -----------------------------------------------------


def _data(envelope: Mapping[str, Any]) -> Mapping[str, Any]:
    data = envelope.get("data")
    if not isinstance(data, Mapping):
        raise FoldError("event data is not an object")
    return data


def _tenant_id(envelope: Mapping[str, Any]) -> UUID:
    return _uuid_value(envelope.get("tenant_id"), "tenant_id")


def _employee_id(data: Mapping[str, Any]) -> UUID:
    return _uuid_value(data.get("employee_id"), "employee_id")


def _uuid_value(value: object, field: str) -> UUID:
    if not isinstance(value, str) or not value:
        raise FoldError(f"{field} is missing")
    try:
        return UUID(value)
    except ValueError as exc:
        raise FoldError(f"{field} is not a UUID") from exc


def _uuid_field(data: Mapping[str, Any], field: str) -> UUID:
    return _uuid_value(data.get(field), field)


def _optional_uuid_field(data: Mapping[str, Any], field: str) -> UUID | None:
    value = data.get(field)
    if value is None or value == "":
        return None
    return _uuid_value(value, field)


def _date_field(data: Mapping[str, Any], field: str) -> date:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise FoldError(f"{field} is missing")
    return _date_value(value, field)


def _optional_date_field(data: Mapping[str, Any], field: str) -> date | None:
    value = data.get(field)
    if value is None or value == "":
        return None
    return _date_value(value, field)


def _date_value(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FoldError(f"{field} is not a date") from exc


def _bool_field(data: Mapping[str, Any], field: str) -> bool:
    value = data.get(field)
    if not isinstance(value, bool):
        raise FoldError(f"{field} is missing")
    return value


def _int_field(data: Mapping[str, Any], field: str) -> int:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise FoldError(f"{field} is missing")
    return value


def _optional_int_field(data: Mapping[str, Any], field: str) -> int | None:
    value = data.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise FoldError(f"{field} is not an integer")
    return value


def _decimal_field(data: Mapping[str, Any], field: str) -> Decimal:
    value = data.get(field)
    if value is None or isinstance(value, bool):
        raise FoldError(f"{field} is missing")
    try:
        # Never through binary float: `str()` keeps the JSON literal's digits
        # and the quantize matches the `Decimal(9, 2)` column (ADR 0019/0017).
        return Decimal(str(value)).quantize(_HUNDREDTH, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError) as exc:
        raise FoldError(f"{field} is not a decimal") from exc


def _str_field(data: Mapping[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise FoldError(f"{field} is missing")
    return value


def _occurred_on(envelope: Mapping[str, Any]) -> date:
    """The UTC calendar date of `occurred_at` (the pre-extension interval start)."""
    value = envelope.get("occurred_at")
    if not isinstance(value, str):
        raise FoldError("occurred_at is missing")
    try:
        occurred_at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FoldError("occurred_at is not a timestamp") from exc
    if occurred_at.tzinfo is not None:
        occurred_at = occurred_at.astimezone(UTC)
    return occurred_at.date()


# --- Seniority and dimension resolution -----------------------------------


def _anniversary(start: date, years: int) -> date:
    """`start + years`, clamping a Feb 29 anniversary to Feb 28."""
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        return start.replace(year=start.year + years, day=28)


def seniority_band(hired_at: date | None, at: date | None) -> str:
    """The spec's tenure band for a tenure anchored at `hired_at`.

    Measured at `at` (the row's bucket end; `to` for `interval=total`). The
    anniversary itself belongs to the older band: exactly one year of tenure is
    `1_3y`. Missing tenure data degrades to `unknown` (spec rule 4).
    """
    if hired_at is None or at is None:
        return UNKNOWN_SENIORITY_BAND
    if at >= _anniversary(hired_at, 5):
        return "5y_plus"
    if at >= _anniversary(hired_at, 3):
        return "3_5y"
    if at >= _anniversary(hired_at, 1):
        return "1_3y"
    return "lt_1y"


def resolve_assignment(
    assignments: Sequence[AssignmentRow], fact_date: date
) -> AssignmentRow | None:
    """The assignment covering `fact_date` (ADR 0019 decision 5).

    The latest interval starting on or before the fact date; facts predating
    the earliest known interval fall back to that interval (a pre-extension
    create only knows its first-seen date).
    """
    return resolve_ordered_assignment(
        sorted(assignments, key=lambda assignment: assignment.effective_from),
        fact_date,
    )


def resolve_ordered_assignment(
    assignments: Sequence[AssignmentRow], fact_date: date
) -> AssignmentRow | None:
    """`resolve_assignment` for a sequence already ordered by start date.

    The headcount read applies this once per employee × bucket over dimension
    rows the repository already returns `ORDER BY effective_from`, so it must
    not re-sort on every visit.
    """
    covering: AssignmentRow | None = None
    for assignment in assignments:
        if assignment.effective_from <= fact_date:
            covering = assignment
        else:
            break
    if covering is None and assignments:
        return assignments[0]
    return covering


def stamp_for(state: DimensionState, fact_date: date) -> DimensionStamp:
    """Resolve the dimension triple + tenure anchor for one fact date."""
    assignment = resolve_assignment(state.assignments, fact_date)
    return DimensionStamp(
        department_id=assignment.department_id if assignment else None,
        position_id=assignment.position_id if assignment else None,
        location_id=assignment.location_id if assignment else None,
        hired_at=state.employee.hired_at if state.employee else None,
    )


# --- Employee dimension folds ---------------------------------------------


def fold_employee_created(
    envelope: Mapping[str, Any], *, prior: EmployeeRow | None = None
) -> DimensionFold:
    """Open `dim_employee` and the first assignment interval.

    `hired_at` is the interval start when the producer filled it (T1); a
    pre-extension envelope starts the interval at `occurred_at`'s date and
    leaves tenure unknown. A stale create never reopens a terminated employee.
    """
    tenant_id = _tenant_id(envelope)
    data = _data(envelope)
    employee_id = _employee_id(data)
    hired_at = _optional_date_field(data, "hired_at")
    effective_from = hired_at if hired_at is not None else _occurred_on(envelope)

    anchor = hired_at if hired_at is not None else (prior.hired_at if prior else None)
    employee = EmployeeRow(
        tenant_id=tenant_id,
        employee_id=employee_id,
        hired_at=anchor,
        terminated_at=prior.terminated_at if prior else None,
    )
    if employee.terminated_at is not None:
        # Replaying a create after the termination must not reopen an interval.
        return DimensionFold(employee=employee)

    assignment = AssignmentRow(
        tenant_id=tenant_id,
        employee_id=employee_id,
        effective_from=effective_from,
        effective_to=None,
        department_id=_uuid_field(data, "department_id"),
        position_id=_uuid_field(data, "position_id"),
        location_id=_uuid_field(data, "location_id"),
    )
    return DimensionFold(employee=employee, assignments=(assignment,))


def fold_employee_transferred(
    envelope: Mapping[str, Any],
    *,
    prior: EmployeeRow | None = None,
    assignments: Sequence[AssignmentRow] = (),
) -> DimensionFold:
    """Close the interval before `effective_from` and open the next one.

    `to_position_id`/`to_location_id` are optional (T1 additive extension); a
    pre-extension envelope carries the previous values forward. The returned
    `restamp_from` bounds the per-employee fact correction the writer runs
    (backdated transfers, ADR 0019 decision 5).
    """
    tenant_id = _tenant_id(envelope)
    data = _data(envelope)
    employee_id = _employee_id(data)
    effective_from = _date_field(data, "effective_from")

    ordered = sorted(assignments, key=lambda assignment: assignment.effective_from)
    previous = _assignment_before(ordered, effective_from)
    existing = next((a for a in ordered if a.effective_from == effective_from), None)
    carry = previous if previous is not None else existing
    if carry is None and ordered:
        # A transfer dated before a pre-extension first-seen interval still has
        # to carry dimension values; the earliest known interval is the same
        # fallback used for facts that predate it (ADR 0019 decision 5).
        carry = ordered[0]
    position_id = _optional_uuid_field(data, "to_position_id")
    location_id = _optional_uuid_field(data, "to_location_id")
    if position_id is None and carry is not None:
        position_id = carry.position_id
    if location_id is None and carry is not None:
        location_id = carry.location_id

    changed: list[AssignmentRow] = []
    if previous is not None and (
        previous.effective_to is None or previous.effective_to > effective_from
    ):
        changed.append(replace(previous, effective_to=effective_from))

    next_start = min(
        (a.effective_from for a in ordered if a.effective_from > effective_from),
        default=None,
    )
    # A duplicate transfer must not reopen an interval a later termination
    # already closed; a genuinely new interval is bounded by the next one.
    effective_to = existing.effective_to if existing is not None else next_start
    if effective_to is None:
        effective_to = next_start
    changed.append(
        AssignmentRow(
            tenant_id=tenant_id,
            employee_id=employee_id,
            effective_from=effective_from,
            effective_to=effective_to,
            department_id=_uuid_field(data, "to_department_id"),
            position_id=position_id,
            location_id=location_id,
        )
    )

    employee = prior or EmployeeRow(
        tenant_id=tenant_id, employee_id=employee_id, hired_at=None
    )
    return DimensionFold(
        employee=employee,
        assignments=tuple(changed),
        restamp_from=effective_from,
    )


def fold_employee_terminated(
    envelope: Mapping[str, Any],
    *,
    prior: EmployeeRow | None = None,
    assignments: Sequence[AssignmentRow] = (),
) -> DimensionFold:
    """Stamp `terminated_at` and close every interval the termination covers."""
    tenant_id = _tenant_id(envelope)
    data = _data(envelope)
    employee_id = _employee_id(data)
    effective_date = _date_field(data, "effective_date")

    existing_termination = prior.terminated_at if prior else None
    terminated_at = (
        min(existing_termination, effective_date)
        if existing_termination is not None
        else effective_date
    )
    employee = EmployeeRow(
        tenant_id=tenant_id,
        employee_id=employee_id,
        hired_at=prior.hired_at if prior else None,
        terminated_at=terminated_at,
    )
    closed = tuple(
        replace(assignment, effective_to=effective_date)
        for assignment in assignments
        if assignment.effective_from <= effective_date
        and (
            assignment.effective_to is None or assignment.effective_to > effective_date
        )
    )
    return DimensionFold(employee=employee, assignments=closed)


def _assignment_before(
    ordered: Sequence[AssignmentRow], moment: date
) -> AssignmentRow | None:
    """The latest interval starting strictly before `moment`."""
    previous: AssignmentRow | None = None
    for assignment in ordered:
        if assignment.effective_from < moment:
            previous = assignment
        else:
            break
    return previous


# --- Fact folds -----------------------------------------------------------


def fold_overtime(
    event_type: str,
    envelope: Mapping[str, Any],
    *,
    prior: OvertimeFact | None = None,
    dimensions: DimensionState,
) -> OvertimeFact | None:
    """Fold one overtime event into the request's current-state row.

    Verified hours are counted by the read side only while `status` is
    `hr_verified`; an HR un-verification (`rejected` from `hr_verified`) flips
    that status and therefore removes the hours from every bucket (spec rule 5).
    """
    data = _data(envelope)

    if event_type == "overtime.requested":
        if prior is not None:
            return prior
        target = "requested"
    else:
        target = _OVERTIME_EVENT_TARGETS.get(event_type)
        if target is None:
            # Unknown/irrelevant event: the flip to `included_in_payroll` never
            # emits one, and anything else must not move the state backwards.
            return prior

    if prior is None:
        if event_type == "overtime.withdrawn":
            # A withdrawal needs a prior row (it has no hours); a partial
            # history with nothing to withdraw contributes no fact.
            return None
        work_date = _date_field(data, "work_date")
        stamp = stamp_for(dimensions, work_date)
        return OvertimeFact(
            tenant_id=_tenant_id(envelope),
            overtime_id=_uuid_field(data, "overtime_id"),
            employee_id=_employee_id(data),
            work_date=work_date,
            hours=_decimal_field(data, "hours"),
            status=target,
            department_id=stamp.department_id,
            position_id=stamp.position_id,
            location_id=stamp.location_id,
            hired_at=stamp.hired_at,
        )

    allowed_from = OVERTIME_TRANSITIONS.get(target)
    if allowed_from is None or prior.status not in allowed_from:
        return prior
    return replace(prior, status=target)


def fold_attendance(
    event_type: str,
    envelope: Mapping[str, Any],
    *,
    prior: AttendanceFact | None = None,
    dimensions: DimensionState,
) -> AttendanceFact | None:
    """Merge `checked_in` + `checked_out` into one attendance row.

    A checked-in record that never checks out keeps `duration_minutes = None`
    (it counts as a check-in and a possible lateness fact, never as a worked
    day — spec rule 6). A late `checked_in` after a closed row is a replay and
    is ignored.
    """
    data = _data(envelope)

    if event_type == "attendance.checked_in":
        if prior is not None:
            return prior
        workday = _date_field(data, "workday")
        stamp = stamp_for(dimensions, workday)
        return AttendanceFact(
            tenant_id=_tenant_id(envelope),
            attendance_id=_uuid_field(data, "attendance_id"),
            employee_id=_employee_id(data),
            workday=workday,
            duration_minutes=None,
            late=_bool_field(data, "late"),
            minutes_late=_optional_int_field(data, "minutes_late"),
            department_id=stamp.department_id,
            position_id=stamp.position_id,
            location_id=stamp.location_id,
            hired_at=stamp.hired_at,
        )

    if event_type != "attendance.checked_out":
        return prior

    duration_minutes = _int_field(data, "duration_minutes")
    if prior is None:
        workday = _date_field(data, "workday")
        stamp = stamp_for(dimensions, workday)
        # Pre-extension check-outs lack the additive `late`/`minutes_late`
        # pair; without a prior row there is nothing to carry forward.
        if "late" in data:
            late = _bool_field(data, "late")
            minutes_late = _optional_int_field(data, "minutes_late")
        else:
            late = False
            minutes_late = None
        return AttendanceFact(
            tenant_id=_tenant_id(envelope),
            attendance_id=_uuid_field(data, "attendance_id"),
            employee_id=_employee_id(data),
            workday=workday,
            duration_minutes=duration_minutes,
            late=late,
            minutes_late=minutes_late,
            department_id=stamp.department_id,
            position_id=stamp.position_id,
            location_id=stamp.location_id,
            hired_at=stamp.hired_at,
        )

    # Lateness is a check-in fact (spec rule 6); a check-out only closes the
    # record. The additive check-out pair exists for consumers that miss the
    # check-in, not to overwrite the projected flag.
    return replace(prior, duration_minutes=duration_minutes)


def fold_leave(
    event_type: str,
    envelope: Mapping[str, Any],
    *,
    prior: LeaveFact | None = None,
    dimensions: DimensionState,
) -> LeaveFact | None:
    """Project one approved leave request as whole days in its start bucket.

    `leave.rejected`/`leave.withdrawn` only ever follow a pending request and
    therefore contribute nothing; a request is never split across buckets, so
    the row is anchored on `start_date` (spec rule 7).
    """
    if event_type != "leave.approved":
        return prior

    data = _data(envelope)
    start_date = _date_field(data, "start_date")
    stamp = stamp_for(dimensions, start_date)
    return LeaveFact(
        tenant_id=_tenant_id(envelope),
        leave_request_id=_uuid_field(data, "leave_request_id"),
        employee_id=_employee_id(data),
        start_date=start_date,
        leave_type_code=_str_field(data, "leave_type_code"),
        approved_day_count=_int_field(data, "days"),
        department_id=stamp.department_id,
        position_id=stamp.position_id,
        location_id=stamp.location_id,
        hired_at=stamp.hired_at,
    )
