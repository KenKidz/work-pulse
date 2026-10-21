"""Unit tests — the pure notification-envelope mapper (T5, ADR 0015).

Every test runs without I/O. The five trigger kinds map to the pinned
recipients and content fields; manager-addressed kinds defer the literal
recipient to `resolve_recipient` (events carry no `manager_id`); every other
type/version is filtered; a trigger with a malformed payload is a skip; and
free text such as `reason`/`decision_note` can never enter the closed content
allowlist.
"""

from dataclasses import fields
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from app.domains.attendance.events import attendance_late
from app.domains.leave.events import (
    leave_approved,
    leave_rejected,
    leave_requested,
    leave_withdrawn,
)
from app.domains.notifications.models import NotificationKind
from app.domains.notifications.schemas import NotificationContent, SubjectFacts
from workers.notifications.mapping import (
    is_known_event_type,
    is_trigger_envelope,
    map_envelope,
    resolve_recipient,
)

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
ACTOR = UUID("00000000-0000-4000-8000-000000000031")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000001")
MANAGER = UUID("00000000-0000-4000-8000-000000000002")
LEAVE_REQUEST = UUID("00000000-0000-4000-8000-0000000000e1")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000a1")
SHIFT = UUID("00000000-0000-4000-8000-0000000000b1")
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
START = date(2026, 10, 22)
END = date(2026, 10, 24)


def _requested() -> dict[str, Any]:
    return leave_requested(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST,
        employee_id=EMPLOYEE,
        leave_type_code="annual",
        start_date=START,
        end_date=END,
        days=3,
    )


def _approved() -> dict[str, Any]:
    return leave_approved(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST,
        employee_id=EMPLOYEE,
        leave_type_code="annual",
        start_date=START,
        end_date=END,
        days=3,
        decided_by=ACTOR,
    )


def _rejected() -> dict[str, Any]:
    return leave_rejected(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST,
        employee_id=EMPLOYEE,
        leave_type_code="sick",
        start_date=START,
        end_date=END,
        decided_by=ACTOR,
    )


def _withdrawn() -> dict[str, Any]:
    return leave_withdrawn(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST,
        employee_id=EMPLOYEE,
        start_date=START,
        end_date=END,
    )


def _late(*, minutes_late: int = 30) -> dict[str, Any]:
    return attendance_late(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        attendance_id=ATTENDANCE,
        employee_id=EMPLOYEE,
        workday=START,
        shift_id=SHIFT,
        minutes_late=minutes_late,
    )


def test_leave_requested_maps_to_the_manager_addressed_kind() -> None:
    envelope = _requested()

    intent = map_envelope(envelope)

    assert intent is not None
    assert intent.kind is NotificationKind.LEAVE_REQUESTED
    assert intent.tenant_id == TENANT
    assert intent.event_id == envelope["event_id"]
    assert intent.event_type == "leave.requested"
    assert intent.content.subject_employee_id == EMPLOYEE
    assert intent.content.leave_type_code == "annual"
    assert intent.content.start_date == START
    assert intent.content.end_date == END
    assert intent.content.days == 3
    assert intent.content.decided_by is None
    # The event has no manager id: the directory supplies it at consume time.
    facts = SubjectFacts(display_name="Ada Lovelace", manager_id=MANAGER)
    assert resolve_recipient(intent, facts) == MANAGER


def test_leave_requested_without_a_manager_is_unresolvable() -> None:
    intent = map_envelope(_requested())
    assert intent is not None

    assert (
        resolve_recipient(
            intent, SubjectFacts(display_name="Ada Lovelace", manager_id=None)
        )
        is None
    )


def test_requester_addressed_kinds_never_use_the_manager() -> None:
    facts = SubjectFacts(display_name="Ada Lovelace", manager_id=MANAGER)

    for envelope, kind in (
        (_approved(), NotificationKind.LEAVE_APPROVED),
        (_rejected(), NotificationKind.LEAVE_REJECTED),
        (_withdrawn(), NotificationKind.LEAVE_WITHDRAWN),
    ):
        intent = map_envelope(envelope)
        assert intent is not None
        assert intent.kind is kind
        assert resolve_recipient(intent, facts) == EMPLOYEE


def test_attendance_late_maps_to_the_manager_addressed_kind() -> None:
    intent = map_envelope(_late(minutes_late=17))

    assert intent is not None
    assert intent.kind is NotificationKind.ATTENDANCE_LATE
    assert intent.content.subject_employee_id == EMPLOYEE
    assert intent.content.workday == START
    assert intent.content.minutes_late == 17
    assert intent.content.start_date is None
    assert (
        resolve_recipient(
            intent, SubjectFacts(display_name="Ada Lovelace", manager_id=MANAGER)
        )
        == MANAGER
    )


def test_content_fields_follow_the_contract_asymmetry() -> None:
    """`approved` has days + decider; `rejected` decider only; `withdrawn` dates."""
    approved = map_envelope(_approved())
    rejected = map_envelope(_rejected())
    withdrawn = map_envelope(_withdrawn())

    assert approved is not None and approved.content.days == 3
    assert approved.content.decided_by == ACTOR
    assert rejected is not None and rejected.content.days is None
    assert rejected.content.decided_by == ACTOR
    assert withdrawn is not None and withdrawn.content.days is None
    assert withdrawn.content.decided_by is None
    assert withdrawn.content.leave_type_code is None


def test_other_catalog_types_are_filtered_silently() -> None:
    checked_in = {
        "event_id": "evt_checked_in",
        "event_type": "attendance.checked_in",
        "version": 1,
        "tenant_id": str(TENANT),
        "occurred_at": NOW.isoformat(),
        "actor_id": str(ACTOR),
        "data": {},
    }
    employee_created = {
        "event_id": "evt_employee",
        "event_type": "employee.created",
        "version": 1,
        "tenant_id": str(TENANT),
        "occurred_at": NOW.isoformat(),
        "actor_id": str(ACTOR),
        "data": {},
    }

    for envelope in (checked_in, employee_created):
        assert is_trigger_envelope(envelope) is False
        assert is_known_event_type(envelope["event_type"]) is True
        assert map_envelope(envelope) is None


def test_unknown_types_and_future_versions_are_not_triggers() -> None:
    unknown = {"event_type": "employee.hired", "version": 1, "data": {}}
    future = {**_requested(), "version": 2}

    assert is_known_event_type(unknown["event_type"]) is False
    assert is_trigger_envelope(unknown) is False
    assert map_envelope(unknown) is None
    assert is_known_event_type(future["event_type"]) is True
    assert is_trigger_envelope(future) is False
    assert map_envelope(future) is None


def test_malformed_trigger_payloads_are_skips_not_intents() -> None:
    missing_days = _requested()
    missing_days["data"].pop("days")
    zero_days = _requested()
    zero_days["data"]["days"] = 0
    unknown_type = _requested()
    unknown_type["data"]["leave_type_code"] = "sabbatical"
    bad_date = _requested()
    bad_date["data"]["start_date"] = "not-a-date"
    missing_tenant = _requested()
    del missing_tenant["tenant_id"]
    negative_minutes = _late(minutes_late=-1)
    missing_workday = _late()
    del missing_workday["data"]["workday"]

    for envelope in (
        missing_days,
        zero_days,
        unknown_type,
        bad_date,
        missing_tenant,
        negative_minutes,
        missing_workday,
    ):
        assert is_trigger_envelope(envelope) is True
        assert map_envelope(envelope) is None


def test_non_object_data_and_non_string_event_id_are_skips() -> None:
    non_object = _requested()
    non_object["data"] = ["not", "an", "object"]
    bad_event_id = _requested()
    bad_event_id["event_id"] = 17

    for envelope in (non_object, bad_event_id):
        assert is_trigger_envelope(envelope) is True
        assert map_envelope(envelope) is None


def test_free_text_can_never_enter_the_content_allowlist() -> None:
    hostile = _requested()
    hostile["data"]["reason"] = "SENTINEL-REASON"
    hostile["data"]["decision_note"] = "SENTINEL-NOTE"

    intent = map_envelope(hostile)

    assert intent is not None
    names = {field.name for field in fields(NotificationContent)}
    assert "reason" not in names
    assert "decision_note" not in names
    assert "SENTINEL" not in repr(intent.content)


def test_envelope_without_a_string_event_type_is_not_a_trigger() -> None:
    assert is_trigger_envelope({"event_type": None, "version": 1}) is False
    assert is_trigger_envelope({"event_type": ["leave.requested"]}) is False
    assert is_known_event_type(None) is False
    assert is_known_event_type(["leave.requested"]) is False
