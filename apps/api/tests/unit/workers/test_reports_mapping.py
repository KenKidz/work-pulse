"""T6 unit tests — the pure envelope → fold-command mapper (no I/O).

The mapper is the worker's whole trigger surface: exactly the consumed event
families from the Analytics & Reports spec. Every other known type is filtered
silently; a truly unknown type is a logged skip in the consumer. A consumed
envelope whose identifiers cannot be parsed raises the domain's `FoldError`,
which the consumer turns into a committed, ids-only skip.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from app.domains.attendance.events import attendance_checked_in, attendance_late
from app.domains.employees.events import (
    employee_created,
    employee_terminated,
    employee_transferred,
)
from app.domains.leave.events import leave_approved, leave_rejected
from app.domains.overtime.events import overtime_requested
from app.domains.reports.folds import FoldError
from workers.reports.mapping import (
    FoldKind,
    is_consumed_envelope,
    is_consumed_event_type,
    is_known_event_type,
    map_envelope,
)

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
ACTOR = UUID("00000000-0000-4000-8000-000000000031")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000001")
DEPARTMENT_A = UUID("00000000-0000-4000-8000-0000000000d1")
DEPARTMENT_B = UUID("00000000-0000-4000-8000-0000000000d2")
POSITION = UUID("00000000-0000-4000-8000-0000000000e1")
LOCATION = UUID("00000000-0000-4000-8000-0000000000f1")
OVERTIME = UUID("00000000-0000-4000-8000-0000000000c1")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000c2")
LEAVE_REQUEST = UUID("00000000-0000-4000-8000-0000000000c3")
NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
WORKDAY = date(2026, 10, 23)
HIRED_AT = date(2026, 1, 5)


def _created(**overrides: Any) -> dict[str, Any]:
    return employee_created(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        employee_id=EMPLOYEE,
        department_id=DEPARTMENT_A,
        position_id=POSITION,
        location_id=LOCATION,
        employment_type="full_time",
        hired_at=HIRED_AT,
        **overrides,
    )


def _requested(**overrides: Any) -> dict[str, Any]:
    return overtime_requested(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        overtime_id=OVERTIME,
        employee_id=EMPLOYEE,
        work_date=WORKDAY,
        hours=Decimal("2.50"),
        **overrides,
    )


def test_maps_employee_created_to_a_dimension_command() -> None:
    command = map_envelope(_created())

    assert command is not None
    assert command.kind is FoldKind.DIMENSION
    assert command.event_type == "employee.created"
    assert command.tenant_id == TENANT
    assert command.employee_id == EMPLOYEE
    assert command.entity_id is None
    assert command.event_id is not None


def test_maps_each_fact_family_to_its_entity_id() -> None:
    overtime = map_envelope(_requested())
    attendance = map_envelope(
        attendance_checked_in(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            attendance_id=ATTENDANCE,
            employee_id=EMPLOYEE,
            workday=WORKDAY,
            work_mode="onsite",
            late=False,
            minutes_late=None,
        )
    )
    leave = map_envelope(
        leave_approved(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            leave_request_id=LEAVE_REQUEST,
            employee_id=EMPLOYEE,
            leave_type_code="annual",
            start_date=WORKDAY,
            end_date=WORKDAY,
            days=1,
            decided_by=ACTOR,
        )
    )

    assert overtime is not None and overtime.kind is FoldKind.OVERTIME
    assert overtime.entity_id == OVERTIME
    assert attendance is not None and attendance.kind is FoldKind.ATTENDANCE
    assert attendance.entity_id == ATTENDANCE
    assert leave is not None and leave.kind is FoldKind.LEAVE
    assert leave.entity_id == LEAVE_REQUEST


def test_maps_transfer_and_termination_as_dimension_commands() -> None:
    transferred = employee_transferred(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        employee_id=EMPLOYEE,
        from_department_id=DEPARTMENT_A,
        to_department_id=DEPARTMENT_B,
        effective_from=date(2026, 3, 1),
    )
    terminated = employee_terminated(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        employee_id=EMPLOYEE,
        effective_date=date(2026, 6, 30),
    )

    for envelope in (transferred, terminated):
        command = map_envelope(envelope)
        assert command is not None
        assert command.kind is FoldKind.DIMENSION
        assert command.entity_id is None


@pytest.mark.parametrize(
    "envelope",
    [
        attendance_late(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            attendance_id=ATTENDANCE,
            employee_id=EMPLOYEE,
            workday=WORKDAY,
            shift_id=UUID("00000000-0000-4000-8000-0000000000c4"),
            minutes_late=10,
        ),
        leave_rejected(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            leave_request_id=LEAVE_REQUEST,
            employee_id=EMPLOYEE,
            leave_type_code="annual",
            start_date=WORKDAY,
            end_date=WORKDAY,
            decided_by=ACTOR,
        ),
        {
            "event_id": "evt_known_irrelevant",
            "event_type": "payroll.calculated",
            "version": 1,
            "tenant_id": str(TENANT),
            "occurred_at": NOW.isoformat(),
            "actor_id": str(ACTOR),
            "data": {},
        },
    ],
)
def test_known_but_irrelevant_events_map_to_none(
    envelope: dict[str, Any],
) -> None:
    assert map_envelope(envelope) is None
    assert is_known_event_type(envelope["event_type"]) is True


def test_unknown_event_is_not_known() -> None:
    envelope = {
        "event_id": "evt_unknown",
        "event_type": "mystery.event",
        "version": 1,
        "tenant_id": str(TENANT),
        "occurred_at": NOW.isoformat(),
        "actor_id": str(ACTOR),
        "data": {},
    }

    assert map_envelope(envelope) is None
    assert is_consumed_envelope(envelope) is False
    assert is_known_event_type(envelope["event_type"]) is False


def test_a_consumed_type_at_another_version_is_not_consumed() -> None:
    envelope = _requested()
    envelope["version"] = 2

    assert map_envelope(envelope) is None
    assert is_consumed_envelope(envelope) is False
    assert is_consumed_event_type(envelope["event_type"]) is True


def test_missing_employee_id_is_a_fold_error() -> None:
    envelope = _requested()
    del envelope["data"]["employee_id"]

    with pytest.raises(FoldError):
        map_envelope(envelope)


def test_invalid_entity_id_is_a_fold_error() -> None:
    envelope = _requested()
    envelope["data"]["overtime_id"] = "not-a-uuid"

    with pytest.raises(FoldError):
        map_envelope(envelope)


def test_missing_data_object_is_a_fold_error() -> None:
    envelope = _requested()
    envelope["data"] = []

    with pytest.raises(FoldError):
        map_envelope(envelope)


def test_pre_extension_create_still_maps() -> None:
    """`hired_at` is optional (ADR 0013); the fold degrades to unknown tenure."""
    envelope = _created()
    del envelope["data"]["hired_at"]

    command = map_envelope(envelope)

    assert command is not None
    assert command.kind is FoldKind.DIMENSION


def test_type_guards_never_raise_on_hostile_values() -> None:
    assert is_consumed_event_type(None) is False
    assert is_consumed_event_type(["employee.created"]) is False
    assert is_known_event_type(None) is False
    assert is_known_event_type({"event_type": "employee.created"}) is False
