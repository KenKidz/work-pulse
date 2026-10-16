"""T3 unit tests — the outbox port's write-time gate (no I/O).

`EventEnvelope` is the port's contract check: every one of the 7 catalogued
builders' outputs must pass, and anything that is not a valid v1 envelope —
missing `event_id`, no `data.employee_id` (the Kafka message key), unknown
fields — must be rejected before any row could be written. No DB, no bus:
these tests pass with every container stopped (`.ai/testing.md`).
"""

import traceback
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, Never, cast
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.attendance import events as attendance_events
from app.domains.employees import events as employee_events
from app.infrastructure.postgres.outbox import PostgresEventOutbox
from app.shared.outbox import EventEnvelope, InvalidEnvelopeError

TENANT_ID = UUID("00000000-0000-4000-8000-00000000000a")
USER_ID = UUID("00000000-0000-4000-8000-000000000031")  # the acting user id
EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000002")  # its employee link
DEPARTMENT_ID = UUID("00000000-0000-4000-8000-000000000003")
POSITION_ID = UUID("00000000-0000-4000-8000-000000000004")
LOCATION_ID = UUID("00000000-0000-4000-8000-000000000005")
NEW_DEPARTMENT_ID = UUID("00000000-0000-4000-8000-000000000006")
ATTENDANCE_ID = UUID("00000000-0000-4000-8000-000000000007")
SHIFT_ID = UUID("00000000-0000-4000-8000-000000000008")
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
WORKDAY = date(2026, 9, 24)
WRITE_TIME = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)

ENVELOPE_FIELDS = {
    "event_id",
    "event_type",
    "version",
    "tenant_id",
    "actor_id",
    "occurred_at",
    "data",
}

EVENT_TYPES = {
    "employee.created",
    "employee.updated",
    "employee.transferred",
    "employee.terminated",
    "attendance.checked_in",
    "attendance.checked_out",
    "attendance.late",
}


def _employee_created() -> dict[str, Any]:
    return employee_events.employee_created(
        tenant_id=TENANT_ID,
        actor_id=USER_ID,
        occurred_at=NOW,
        employee_id=EMPLOYEE_ID,
        department_id=DEPARTMENT_ID,
        position_id=POSITION_ID,
        location_id=LOCATION_ID,
        employment_type="full_time",
    )


def _employee_updated() -> dict[str, Any]:
    return employee_events.employee_updated(
        tenant_id=TENANT_ID,
        actor_id=USER_ID,
        occurred_at=NOW,
        employee_id=EMPLOYEE_ID,
        changed_fields=["first_name", "email"],
    )


def _employee_transferred() -> dict[str, Any]:
    return employee_events.employee_transferred(
        tenant_id=TENANT_ID,
        actor_id=USER_ID,
        occurred_at=NOW,
        employee_id=EMPLOYEE_ID,
        from_department_id=DEPARTMENT_ID,
        to_department_id=NEW_DEPARTMENT_ID,
        effective_from=WORKDAY,
    )


def _employee_terminated() -> dict[str, Any]:
    return employee_events.employee_terminated(
        tenant_id=TENANT_ID,
        actor_id=USER_ID,
        occurred_at=NOW,
        employee_id=EMPLOYEE_ID,
        effective_date=WORKDAY,
    )


def _attendance_checked_in() -> dict[str, Any]:
    return attendance_events.attendance_checked_in(
        tenant_id=TENANT_ID,
        actor_id=USER_ID,
        occurred_at=NOW,
        attendance_id=ATTENDANCE_ID,
        employee_id=EMPLOYEE_ID,
        workday=WORKDAY,
        work_mode="onsite",
        late=True,
    )


def _attendance_checked_out() -> dict[str, Any]:
    return attendance_events.attendance_checked_out(
        tenant_id=TENANT_ID,
        actor_id=USER_ID,
        occurred_at=NOW,
        attendance_id=ATTENDANCE_ID,
        employee_id=EMPLOYEE_ID,
        workday=WORKDAY,
        duration_minutes=480,
    )


def _attendance_late() -> dict[str, Any]:
    return attendance_events.attendance_late(
        tenant_id=TENANT_ID,
        actor_id=USER_ID,
        occurred_at=NOW,
        attendance_id=ATTENDANCE_ID,
        employee_id=EMPLOYEE_ID,
        workday=WORKDAY,
        shift_id=SHIFT_ID,
        minutes_late=18,
    )


BUILDER_CASES: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    ("employee.created", _employee_created),
    ("employee.updated", _employee_updated),
    ("employee.transferred", _employee_transferred),
    ("employee.terminated", _employee_terminated),
    ("attendance.checked_in", _attendance_checked_in),
    ("attendance.checked_out", _attendance_checked_out),
    ("attendance.late", _attendance_late),
]


def test_envelope_carries_exactly_the_pinned_fields() -> None:
    assert set(EventEnvelope.model_fields) == ENVELOPE_FIELDS


def test_builder_cases_cover_the_catalogued_event_types() -> None:
    assert {event_type for event_type, _ in BUILDER_CASES} == EVENT_TYPES


@pytest.mark.parametrize(("event_type", "build"), BUILDER_CASES)
def test_builder_output_is_a_valid_envelope(
    event_type: str, build: Callable[[], dict[str, Any]]
) -> None:
    validated = EventEnvelope.model_validate(build())

    assert validated.event_type == event_type
    assert validated.version == 1
    assert validated.tenant_id == TENANT_ID
    assert validated.actor_id == USER_ID
    assert validated.event_id.startswith("evt_")


@pytest.mark.parametrize("field", sorted(ENVELOPE_FIELDS))
def test_missing_envelope_field_is_rejected(field: str) -> None:
    envelope = _employee_created()
    del envelope[field]

    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(envelope)


def test_non_envelope_dict_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate({"event_type": "attendance.checked_in"})


def test_envelope_without_employee_id_is_rejected() -> None:
    envelope = _employee_created()
    del envelope["data"]["employee_id"]

    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(envelope)


def test_unknown_top_level_field_is_rejected() -> None:
    envelope = {**_employee_created(), "note": "not part of the envelope"}

    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(envelope)


class _NeverTouchedSession:
    """Any attribute access fails — rejection must happen before any I/O."""

    def __getattr__(self, name: str) -> Never:
        raise AssertionError(f"session.{name} reached before validation")


def _clock() -> datetime:
    return WRITE_TIME


def _writer_on_exploding_session() -> PostgresEventOutbox:
    return PostgresEventOutbox(cast(AsyncSession, _NeverTouchedSession()), _clock)


async def test_writer_rejects_a_non_envelope_before_any_io() -> None:
    with pytest.raises(InvalidEnvelopeError):
        await _writer_on_exploding_session().append(
            {"event_type": "attendance.checked_in"}
        )


async def test_writer_rejection_never_echoes_payload_contents() -> None:
    """Pydantic's detail embeds input values; neither the port's message nor
    the rendered exception chain may surface payload contents."""
    bad = {**_employee_created(), "note": "Ada Lovelace <ada@example.test>"}

    with pytest.raises(InvalidEnvelopeError) as excinfo:
        await _writer_on_exploding_session().append(bad)

    rendered = "".join(traceback.format_exception(excinfo.value))
    assert "Ada" not in rendered
    assert "@" not in rendered
