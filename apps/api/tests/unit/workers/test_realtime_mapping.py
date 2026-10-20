"""T3 unit tests — the pure envelope → `PresenceFact` mapping (no I/O).

Envelopes come from the real builders (`app.domains.attendance.events`), so
these tests pin the producer/consumer seam itself: every field the fact needs
round-trips the wire format, and the fact-shape assertions that used to live
with the write path's presence publish re-home here (the ADR 0013 swap).

The failure taxonomy: a missing or invalid fact-required field maps to `None`
with `is_realtime_envelope` still `True` (the consumer's skip signal), a
filtered event (`attendance.late`, `employee.*`, other versions, leave outside
the consume date) maps to `None` with `is_realtime_envelope` `False` (silent),
pre-extension payloads default `null`/`false`, and nothing ever raises.

Leave (T7, ADR 0014): a v1 `leave.approved` maps to an `on_leave` fact only
when its inclusive range covers the injected consume date. The fact has no
attendance identity and no `since`; other `leave.*` events never reach the
wall.
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.domains.attendance.events import (
    attendance_checked_in,
    attendance_checked_out,
    attendance_late,
)
from app.domains.leave.events import (
    leave_approved,
    leave_rejected,
    leave_requested,
    leave_withdrawn,
)
from workers.realtime.mapping import is_realtime_envelope, map_envelope

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
ACTOR = UUID("00000000-0000-4000-8000-000000000031")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000007")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000a1")
LEAVE_REQUEST = UUID("00000000-0000-4000-8000-0000000000e1")
NOW = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
WORKDAY = date(2026, 10, 16)


def _checked_in(
    *, late: bool = False, minutes_late: int | None = None, work_mode: str = "onsite"
) -> dict[str, Any]:
    return attendance_checked_in(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        attendance_id=ATTENDANCE,
        employee_id=EMPLOYEE,
        workday=WORKDAY,
        work_mode=work_mode,
        late=late,
        minutes_late=minutes_late,
    )


def _checked_out(
    *, late: bool = False, minutes_late: int | None = None
) -> dict[str, Any]:
    return attendance_checked_out(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        attendance_id=ATTENDANCE,
        employee_id=EMPLOYEE,
        workday=WORKDAY,
        duration_minutes=510,
        late=late,
        minutes_late=minutes_late,
    )


def test_checked_in_maps_to_one_present_fact() -> None:
    envelope = _checked_in()

    fact = map_envelope(envelope)

    assert fact is not None
    assert fact.tenant_id == TENANT
    assert fact.workday == WORKDAY
    assert fact.employee_id == EMPLOYEE
    assert fact.attendance_id == ATTENDANCE
    assert fact.status == "present"
    assert fact.late is False
    assert fact.minutes_late is None  # only set with `late` (wire rule)
    # `since` = the envelope's `occurred_at` = the service's `now`, which the
    # record stored as `checked_in_at` — the REST board row's `since` exactly.
    assert fact.since == NOW
    assert fact.reason == "checked_in"
    assert fact.occurred_at == NOW


def test_remote_checked_in_maps_to_remote() -> None:
    fact = map_envelope(_checked_in(work_mode="remote"))

    assert fact is not None
    assert fact.status == "remote"
    assert fact.late is False


def test_late_check_in_carries_the_recorded_minutes() -> None:
    fact = map_envelope(_checked_in(late=True, minutes_late=30))

    assert fact is not None
    assert fact.late is True
    assert fact.minutes_late == 30


def test_a_late_check_in_pair_maps_exactly_one_fact() -> None:
    """One transition emits `checked_in` then `late` — one wall frame (R8)."""
    late = attendance_late(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        attendance_id=ATTENDANCE,
        employee_id=EMPLOYEE,
        workday=WORKDAY,
        shift_id=uuid4(),
        minutes_late=30,
    )

    facts = [map_envelope(_checked_in(late=True, minutes_late=30)), map_envelope(late)]

    assert facts[0] is not None
    assert facts[0].reason == "checked_in"
    assert facts[0].minutes_late == 30
    assert facts[1] is None
    assert not is_realtime_envelope(late)  # filtered, not skipped


def test_checked_out_maps_to_the_closed_fact() -> None:
    fact = map_envelope(_checked_out(late=True, minutes_late=30))

    assert fact is not None
    assert fact.status == "checked_out"
    assert fact.reason == "checked_out"
    assert fact.late is True
    assert fact.minutes_late == 30  # the closed record's recomputed minutes
    assert fact.since == NOW
    assert fact.occurred_at == NOW


@pytest.mark.parametrize(
    "event_type",
    [
        "attendance.late",
        "employee.hired",
        "employee.updated",
        "shift.created",
        "unknown.thing",
    ],
)
def test_foreign_event_types_are_filtered_silently(event_type: str) -> None:
    envelope = _checked_in()
    envelope["event_type"] = event_type

    assert map_envelope(envelope) is None
    assert is_realtime_envelope(envelope) is False


@pytest.mark.parametrize("version", [2, "1", None])
def test_other_versions_are_filtered(version: Any) -> None:
    envelope = _checked_in()
    envelope["version"] = version

    assert map_envelope(envelope) is None
    assert is_realtime_envelope(envelope) is False


def test_pre_extension_checked_in_defaults_minutes_to_null() -> None:
    envelope = _checked_in()
    del envelope["data"]["minutes_late"]

    fact = map_envelope(envelope)

    assert fact is not None
    assert fact.late is False
    assert fact.minutes_late is None


def test_pre_extension_late_checked_in_defaults_minutes_to_null() -> None:
    envelope = _checked_in(late=True, minutes_late=30)
    del envelope["data"]["minutes_late"]

    fact = map_envelope(envelope)

    assert fact is not None
    assert fact.late is True  # the flag predates the extension
    assert fact.minutes_late is None  # the absent key defaults to null


def test_pre_extension_checked_out_defaults_late_and_minutes() -> None:
    envelope = _checked_out()
    del envelope["data"]["late"]
    del envelope["data"]["minutes_late"]

    fact = map_envelope(envelope)

    assert fact is not None
    assert fact.status == "checked_out"
    assert fact.late is False
    assert fact.minutes_late is None


@pytest.mark.parametrize(
    "missing", ["attendance_id", "employee_id", "workday", "work_mode"]
)
def test_missing_fact_required_field_is_a_skipped_none(missing: str) -> None:
    envelope = _checked_in()
    del envelope["data"][missing]

    assert map_envelope(envelope) is None
    assert is_realtime_envelope(envelope) is True  # ours — the consumer logs it


@pytest.mark.parametrize("missing", ["tenant_id", "occurred_at", "data"])
def test_missing_envelope_field_is_a_skipped_none(missing: str) -> None:
    envelope = _checked_in()
    del envelope[missing]

    assert map_envelope(envelope) is None
    assert is_realtime_envelope(envelope) is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "not-a-uuid"),
        ("occurred_at", "not-a-time"),
        ("workday", "2026-13-99"),
        ("employee_id", "nope"),
        ("attendance_id", 123),
        ("work_mode", "hybrid"),
        ("late", "yes"),
    ],
)
def test_invalid_checked_in_values_are_skipped(field: str, value: Any) -> None:
    envelope = _checked_in()
    target = envelope if field in {"tenant_id", "occurred_at"} else envelope["data"]
    target[field] = value

    assert map_envelope(envelope) is None
    assert is_realtime_envelope(envelope) is True


@pytest.mark.parametrize("value", [-1, "30", 3.5, True])
def test_invalid_minutes_late_is_skipped(value: Any) -> None:
    envelope = _checked_out(late=True, minutes_late=30)
    envelope["data"]["minutes_late"] = value

    assert map_envelope(envelope) is None
    assert is_realtime_envelope(envelope) is True


def test_non_boolean_late_on_check_out_is_skipped() -> None:
    envelope = _checked_out()
    envelope["data"]["late"] = 1  # a number is not a boolean

    assert map_envelope(envelope) is None
    assert is_realtime_envelope(envelope) is True


def test_a_non_object_data_payload_is_a_skipped_none() -> None:
    envelope = _checked_in()
    envelope["data"] = "surprise"

    assert map_envelope(envelope) is None
    assert is_realtime_envelope(envelope) is True


def test_mapping_never_raises_on_hostile_shapes() -> None:
    hostile: list[dict[str, Any]] = [
        {},
        {"event_type": None, "version": 1},
        {"event_type": [], "version": 1},  # unhashable — must not raise
        {"event_type": "attendance.checked_in", "version": 1},
        {"event_type": "attendance.checked_in", "version": 1, "data": {}},
        {"event_type": "attendance.checked_in", "version": 1, "data": []},
        {"event_type": "attendance.checked_out", "version": 1, "data": {"late": []}},
        {"event_type": "attendance.checked_out", "version": 1, "data": None},
    ]

    for envelope in hostile:
        assert map_envelope(envelope) is None


# --- leave (ADR 0014) ---------------------------------------------------------


def _leave(
    *,
    start_date: date = WORKDAY,
    end_date: date = WORKDAY,
) -> dict[str, Any]:
    return leave_approved(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST,
        employee_id=EMPLOYEE,
        leave_type_code="annual",
        start_date=start_date,
        end_date=end_date,
        days=(end_date - start_date).days + 1,
        decided_by=ACTOR,
    )


def test_leave_approved_covering_today_maps_to_the_on_leave_fact() -> None:
    envelope = _leave(start_date=WORKDAY, end_date=WORKDAY)

    fact = map_envelope(envelope, today=WORKDAY)

    assert fact is not None
    assert fact.tenant_id == TENANT
    assert fact.workday == WORKDAY  # the consume date, never the event date
    assert fact.employee_id == EMPLOYEE
    assert fact.attendance_id is None
    assert fact.status == "on_leave"
    assert fact.late is False
    assert fact.minutes_late is None
    assert fact.since is None
    assert fact.reason == "leave_approved"
    assert fact.occurred_at == NOW
    assert is_realtime_envelope(envelope, today=WORKDAY) is True


def test_leave_window_is_inclusive_at_both_boundaries() -> None:
    envelope = _leave(start_date=date(2026, 10, 14), end_date=date(2026, 10, 18))

    for day in (date(2026, 10, 14), WORKDAY, date(2026, 10, 18)):
        fact = map_envelope(envelope, today=day)
        assert fact is not None
        assert fact.workday == day
        assert fact.status == "on_leave"


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [
        (date(2026, 10, 10), date(2026, 10, 15)),  # ended yesterday
        (date(2026, 10, 17), date(2026, 10, 20)),  # starts tomorrow
    ],
)
def test_leave_outside_the_consume_date_is_silently_filtered(
    start_date: date, end_date: date
) -> None:
    envelope = _leave(start_date=start_date, end_date=end_date)

    assert map_envelope(envelope, today=WORKDAY) is None
    assert is_realtime_envelope(envelope, today=WORKDAY) is False  # no skip log


@pytest.mark.parametrize("event_name", ["requested", "rejected", "withdrawn"])
def test_other_leave_events_never_reach_the_wall(event_name: str) -> None:
    builders = {
        "requested": lambda: leave_requested(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            leave_request_id=LEAVE_REQUEST,
            employee_id=EMPLOYEE,
            leave_type_code="annual",
            start_date=WORKDAY,
            end_date=WORKDAY,
            days=1,
        ),
        "rejected": lambda: leave_rejected(
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
        "withdrawn": lambda: leave_withdrawn(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            leave_request_id=LEAVE_REQUEST,
            employee_id=EMPLOYEE,
            start_date=WORKDAY,
            end_date=WORKDAY,
        ),
    }
    envelope = builders[event_name]()

    assert map_envelope(envelope, today=WORKDAY) is None
    assert is_realtime_envelope(envelope, today=WORKDAY) is False


@pytest.mark.parametrize("missing", ["start_date", "end_date"])
def test_malformed_leave_window_is_filtered(missing: str) -> None:
    envelope = _leave()
    del envelope["data"][missing]

    assert map_envelope(envelope, today=WORKDAY) is None
    assert is_realtime_envelope(envelope, today=WORKDAY) is False


def test_leave_without_an_employee_is_a_skipped_none() -> None:
    envelope = _leave()
    del envelope["data"]["employee_id"]

    assert map_envelope(envelope, today=WORKDAY) is None
    assert is_realtime_envelope(envelope, today=WORKDAY) is True


def test_leave_with_an_invalid_occurred_at_is_a_skipped_none() -> None:
    envelope = _leave()
    envelope["occurred_at"] = "not-a-time"

    assert map_envelope(envelope, today=WORKDAY) is None
    assert is_realtime_envelope(envelope, today=WORKDAY) is True  # ours — log


@pytest.mark.parametrize("version", [2, "1", None])
def test_leave_other_versions_are_filtered(version: Any) -> None:
    envelope = _leave()
    envelope["version"] = version

    assert map_envelope(envelope, today=WORKDAY) is None
    assert is_realtime_envelope(envelope, today=WORKDAY) is False
