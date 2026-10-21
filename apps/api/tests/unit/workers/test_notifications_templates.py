"""Unit tests — pure notification templates (T5, ADR 0015 decision 6).

The rendered subject/body use exactly the trigger table's content fields:
display names, leave type, ISO dates, `days`, `workday`, `minutes_late` and the
decider name when it resolves. Hostile `reason`/`decision_note` values are
discarded by the mapper and can never appear; a malformed intent raises instead
of rendering a partial message. No I/O, no wall clock.
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest

from app.domains.attendance.events import attendance_late
from app.domains.leave.events import (
    leave_approved,
    leave_rejected,
    leave_requested,
    leave_withdrawn,
)
from app.domains.notifications.models import NotificationKind, RenderedMessage
from app.domains.notifications.schemas import NotificationContent, NotificationIntent
from workers.notifications.mapping import map_envelope
from workers.notifications.templates import render

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
ACTOR = UUID("00000000-0000-4000-8000-000000000031")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000001")
LEAVE_REQUEST = UUID("00000000-0000-4000-8000-0000000000e1")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000a1")
SHIFT = UUID("00000000-0000-4000-8000-0000000000b1")
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
START = date(2026, 10, 22)
END = date(2026, 10, 24)
SUBJECT_NAME = "Ada Lovelace"
RECIPIENT_NAME = "Mary Manager"
ACTOR_NAME = "Grace Hopper"


def _render(
    envelope: dict[str, Any],
    *,
    subject_name: str = SUBJECT_NAME,
    recipient_name: str = RECIPIENT_NAME,
    actor_name: str | None = ACTOR_NAME,
) -> RenderedMessage:
    intent = map_envelope(envelope)
    assert intent is not None
    return render(
        intent,
        subject_name=subject_name,
        recipient_name=recipient_name,
        actor_name=actor_name,
    )


def _requested(*, leave_type_code: str = "annual", days: int = 3) -> dict[str, Any]:
    return leave_requested(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST,
        employee_id=EMPLOYEE,
        leave_type_code=leave_type_code,
        start_date=START,
        end_date=END,
        days=days,
    )


def _approved(*, days: int = 3) -> dict[str, Any]:
    return leave_approved(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST,
        employee_id=EMPLOYEE,
        leave_type_code="annual",
        start_date=START,
        end_date=END,
        days=days,
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


def test_leave_requested_names_the_employee_and_the_inclusive_dates() -> None:
    message = _render(_requested())

    assert message.subject == f"Leave request from {SUBJECT_NAME}"
    assert message.body == (
        f"Hello {RECIPIENT_NAME},\n\n"
        f"{SUBJECT_NAME} requested annual leave from 2026-10-22 to 2026-10-24 "
        "(3 days)."
    )


def test_leave_requested_pluralizes_one_day() -> None:
    message = _render(_requested(days=1))

    assert "(1 day)" in message.body


@pytest.mark.parametrize(
    ("code", "label"),
    [("annual", "annual leave"), ("sick", "sick leave"), ("unpaid", "unpaid leave")],
)
def test_every_allowlisted_leave_type_has_a_label(code: str, label: str) -> None:
    message = _render(_requested(leave_type_code=code))

    assert f"requested {label} from" in message.body


def test_leave_approved_includes_days_and_the_decider() -> None:
    message = _render(_approved(), recipient_name=SUBJECT_NAME)

    assert message.subject == "Leave approved: annual leave"
    assert message.body == (
        f"Hello {SUBJECT_NAME},\n\n"
        "Your annual leave from 2026-10-22 to 2026-10-24 (3 days) "
        f"was approved by {ACTOR_NAME}."
    )


def test_leave_approved_omits_the_decider_clause_when_the_name_does_not_resolve() -> (
    None
):
    message = _render(_approved(), recipient_name=SUBJECT_NAME, actor_name=None)

    assert message.body.endswith("was approved.")
    assert " by " not in message.body
    assert str(ACTOR) not in message.body


def test_leave_rejected_has_no_days_and_renders_the_decider() -> None:
    message = _render(_rejected(), recipient_name=SUBJECT_NAME)

    assert message.subject == "Leave rejected: sick leave"
    assert message.body == (
        f"Hello {SUBJECT_NAME},\n\n"
        "Your sick leave from 2026-10-22 to 2026-10-24 "
        f"was rejected by {ACTOR_NAME}."
    )
    assert "3 days" not in message.body


def test_leave_withdrawn_renders_dates_only() -> None:
    message = _render(_withdrawn(), recipient_name=SUBJECT_NAME)

    assert message.subject == "Leave request withdrawn"
    assert message.body == (
        f"Hello {SUBJECT_NAME},\n\n"
        "Your leave request from 2026-10-22 to 2026-10-24 was withdrawn."
    )
    assert "annual" not in message.body


def test_attendance_late_names_the_employee_workday_and_minutes() -> None:
    message = _render(_late(minutes_late=30))

    assert message.subject == f"Late check-in: {SUBJECT_NAME}"
    assert message.body == (
        f"Hello {RECIPIENT_NAME},\n\n"
        f"{SUBJECT_NAME} checked in late on 2026-10-22: 30 minutes late."
    )


def test_attendance_late_pluralizes_one_minute() -> None:
    message = _render(_late(minutes_late=1))

    assert "1 minute late." in message.body


def test_hostile_free_text_never_reaches_the_message() -> None:
    hostile = _requested()
    hostile["data"]["reason"] = "SENTINEL-REASON"
    hostile["data"]["decision_note"] = "SENTINEL-NOTE"

    message = _render(hostile)

    assert "SENTINEL" not in message.subject
    assert "SENTINEL" not in message.body


def test_a_malformed_intent_raises_instead_of_rendering_partially() -> None:
    intent = NotificationIntent(
        kind=NotificationKind.LEAVE_REQUESTED,
        tenant_id=TENANT,
        event_id="evt_malformed",
        event_type="leave.requested",
        recipient_employee_id=EMPLOYEE,
        content=NotificationContent(subject_employee_id=EMPLOYEE),
    )

    with pytest.raises(ValueError):
        render(
            intent,
            subject_name=SUBJECT_NAME,
            recipient_name=RECIPIENT_NAME,
        )
