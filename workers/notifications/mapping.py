"""Pure envelope → `NotificationIntent` mapping (ADR 0015 decision 4).

The worker's whole trigger surface: exactly the five v1 kinds. Every other
event on the topic is ignored — known-but-untriggering types silently, truly
unknown types with an ids-only warning from the consumer. A trigger of ours
with a missing or invalid field maps to `None` as a *skip*; the consumer tells
the two `None`s apart with `is_trigger_envelope` (the realtime consumer's
`is_realtime_envelope` shape).

The mapper never resolves reference data: events carry no PII, so display
names, the manager link and account addresses come from the read-only
directory at consume time. For the manager-addressed kinds the intent's
`recipient_employee_id` is therefore only provisional (the affected employee)
until the consumer calls `resolve_recipient` with the directory's
`SubjectFacts`. Requester-addressed kinds address the event's employee
directly. `decided_by` is an actor **user** id (ADR 0010); templates render
its display name only when it resolves (spec R3).
"""

from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID

from app.domains.notifications.models import NotificationKind
from app.domains.notifications.schemas import (
    NotificationContent,
    NotificationIntent,
    SubjectFacts,
)

LEAVE_REQUESTED = "leave.requested"
LEAVE_APPROVED = "leave.approved"
LEAVE_REJECTED = "leave.rejected"
LEAVE_WITHDRAWN = "leave.withdrawn"
ATTENDANCE_LATE = "attendance.late"

TRIGGER_EVENT_TYPES = frozenset(
    {
        LEAVE_REQUESTED,
        LEAVE_APPROVED,
        LEAVE_REJECTED,
        LEAVE_WITHDRAWN,
        ATTENDANCE_LATE,
    }
)

# The five v1 kinds are pinned by the trigger table; the map is the only place
# an event type becomes a kind.
_KINDS: dict[str, NotificationKind] = {
    LEAVE_REQUESTED: NotificationKind.LEAVE_REQUESTED,
    LEAVE_APPROVED: NotificationKind.LEAVE_APPROVED,
    LEAVE_REJECTED: NotificationKind.LEAVE_REJECTED,
    LEAVE_WITHDRAWN: NotificationKind.LEAVE_WITHDRAWN,
    ATTENDANCE_LATE: NotificationKind.ATTENDANCE_LATE,
}

# Kinds whose recipient is the affected employee's direct manager
# (`employees.manager_id`, the leave decide-scope source).
MANAGER_ADDRESSED_KINDS = frozenset(
    {NotificationKind.LEAVE_REQUESTED, NotificationKind.ATTENDANCE_LATE}
)

# Every type the topic carries today (the frozen `contracts/events/` catalog).
# A type outside this set is *unknown* and gets an ids-only warning; a known
# type outside `TRIGGER_EVENT_TYPES` is filtered silently.
KNOWN_EVENT_TYPES = frozenset(
    {
        "attendance.checked_in",
        "attendance.checked_out",
        ATTENDANCE_LATE,
        "employee.created",
        "employee.updated",
        "employee.transferred",
        "employee.terminated",
        LEAVE_REQUESTED,
        LEAVE_APPROVED,
        LEAVE_REJECTED,
        LEAVE_WITHDRAWN,
    }
)

_LEAVE_TYPE_CODES = frozenset({"annual", "sick", "unpaid"})


def is_trigger_envelope(envelope: Mapping[str, Any]) -> bool:
    """Whether this is one of our five v1 trigger types (never raises)."""
    event_type = envelope.get("event_type")
    return (
        isinstance(event_type, str)
        and event_type in TRIGGER_EVENT_TYPES
        and envelope.get("version") == 1
    )


def is_known_event_type(event_type: Any) -> bool:
    """Whether `event_type` is in the frozen event catalog (never raises)."""
    return isinstance(event_type, str) and event_type in KNOWN_EVENT_TYPES


def map_envelope(envelope: Mapping[str, Any]) -> NotificationIntent | None:
    """Map one envelope onto a notification intent; never raises.

    `None` for filtered events (every non-trigger type or version) and for
    trigger envelopes that are not intent-shaped. Callers use
    `is_trigger_envelope` to tell the two apart (silent vs. logged skip).
    """
    if not is_trigger_envelope(envelope):
        return None
    try:
        return _build_intent(envelope)
    except (KeyError, TypeError, ValueError):
        # A missing intent-required field or an unparseable value: the consumer
        # logs the skip ids-only and the offset still advances — never a
        # partial intent, never a crash on one bad message.
        return None


def resolve_recipient(intent: NotificationIntent, facts: SubjectFacts) -> UUID | None:
    """The person this intent addresses, once the directory has spoken.

    Manager-addressed kinds need `facts.manager_id` — `None` means no direct
    manager and the consumer skips the message (no rows, ids-only log).
    Requester-addressed kinds address the affected employee directly.
    """
    if intent.kind in MANAGER_ADDRESSED_KINDS:
        return facts.manager_id
    return intent.content.subject_employee_id


def _build_intent(envelope: Mapping[str, Any]) -> NotificationIntent:
    """Derive the intent; raises internally on anything not intent-shaped."""
    event_type = envelope["event_type"]
    if not isinstance(event_type, str):
        raise TypeError("event_type must be a string")
    data = envelope["data"]
    if not isinstance(data, Mapping):
        raise TypeError("data must be an object")

    employee_id = _identifier(data["employee_id"])
    kind = _KINDS.get(event_type)
    if kind is None:  # pragma: no cover — guarded by `is_trigger_envelope`
        raise ValueError("unsupported event type")

    return NotificationIntent(
        kind=kind,
        tenant_id=_identifier(envelope["tenant_id"]),
        event_id=_text(envelope["event_id"], "event_id"),
        event_type=event_type,
        # Provisional for manager-addressed kinds; `resolve_recipient` replaces
        # it before the service sees the intent.
        recipient_employee_id=employee_id,
        content=_content(event_type, data, employee_id),
    )


def _content(
    event_type: str, data: Mapping[str, Any], employee_id: UUID
) -> NotificationContent:
    """Only the trigger table's allowlisted content fields ever leave here."""
    if event_type == LEAVE_REQUESTED:
        return NotificationContent(
            subject_employee_id=employee_id,
            leave_type_code=_leave_type(data["leave_type_code"]),
            start_date=_date(data["start_date"]),
            end_date=_date(data["end_date"]),
            days=_positive_int(data["days"], "days"),
        )
    if event_type == LEAVE_APPROVED:
        return NotificationContent(
            subject_employee_id=employee_id,
            leave_type_code=_leave_type(data["leave_type_code"]),
            start_date=_date(data["start_date"]),
            end_date=_date(data["end_date"]),
            days=_positive_int(data["days"], "days"),
            decided_by=_identifier(data["decided_by"]),
        )
    if event_type == LEAVE_REJECTED:
        # The rejected contract has no `days` (F6); a hostile extra value is
        # deliberately ignored — the template table does not include it.
        return NotificationContent(
            subject_employee_id=employee_id,
            leave_type_code=_leave_type(data["leave_type_code"]),
            start_date=_date(data["start_date"]),
            end_date=_date(data["end_date"]),
            decided_by=_identifier(data["decided_by"]),
        )
    if event_type == LEAVE_WITHDRAWN:
        return NotificationContent(
            subject_employee_id=employee_id,
            start_date=_date(data["start_date"]),
            end_date=_date(data["end_date"]),
        )
    if event_type == ATTENDANCE_LATE:
        return NotificationContent(
            subject_employee_id=employee_id,
            workday=_date(data["workday"]),
            minutes_late=_non_negative_int(data["minutes_late"], "minutes_late"),
        )
    raise ValueError("unsupported event type")  # pragma: no cover


def _identifier(value: Any) -> UUID:
    """UUID from the wire form; anything unparseable raises (caught above)."""
    if not isinstance(value, str):
        raise TypeError("identifier must be a string")
    return UUID(value)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be a non-empty string")
    return value


def _date(value: Any) -> date:
    if not isinstance(value, str):
        raise TypeError("date must be a string")
    return date.fromisoformat(value)


def _leave_type(value: Any) -> str:
    if not isinstance(value, str) or value not in _LEAVE_TYPE_CODES:
        raise ValueError("unknown leave type code")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value
