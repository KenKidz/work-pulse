"""Pure envelope → fold-command mapping for the reports projection (T6).

The worker consumes exactly the event families the Analytics & Reports spec
lists (`.ai/features/analytics-reports.md` → Events): the employee dimension
stream, attendance check-ins/outs, approved leave and the overtime state
machine. Everything else on the topic is irrelevant and ignored — known types
silently (notifications/payroll/`attendance.late`/other leave transitions),
truly unknown types with an ids-only `unknown_event_type` warning from the
consumer.

Mapping validates only what the consumer needs to load prior state — the
envelope and the entity's identifiers — and raises `FoldError` (the domain's
malformed-message type) when a consumed envelope is not shaped that way. The
fold functions re-read the payload and are total over the contract's required
fields; a gap there is a skip, never a partial row (ADR 0019 decision 4).

The command deliberately carries the envelope: folds are pure functions of
`(prior row, envelope)`, and re-serializing or re-typing the payload here would
create a second contract copy that could drift from the fold rules.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.domains.reports.folds import FoldError

EMPLOYEE_EVENT_TYPES = frozenset(
    {"employee.created", "employee.transferred", "employee.terminated"}
)
ATTENDANCE_EVENT_TYPES = frozenset({"attendance.checked_in", "attendance.checked_out"})
LEAVE_EVENT_TYPES = frozenset({"leave.approved"})
OVERTIME_EVENT_TYPES = frozenset(
    {
        "overtime.requested",
        "overtime.approved",
        "overtime.verified",
        "overtime.rejected",
        "overtime.withdrawn",
    }
)

#: The events this projection folds (spec → Events). Everything else advances
#: the offset untouched.
CONSUMED_EVENT_TYPES = frozenset(
    EMPLOYEE_EVENT_TYPES
    | ATTENDANCE_EVENT_TYPES
    | LEAVE_EVENT_TYPES
    | OVERTIME_EVENT_TYPES
)

#: The frozen `contracts/events/` catalog. A known type outside
#: `CONSUMED_EVENT_TYPES` is filtered silently; a type outside this set is a
#: real unknown and gets the ids-only warning (the notifications precedent).
KNOWN_EVENT_TYPES = frozenset(
    {
        "attendance.checked_in",
        "attendance.checked_out",
        "attendance.late",
        *EMPLOYEE_EVENT_TYPES,
        "employee.updated",
        "leave.requested",
        *LEAVE_EVENT_TYPES,
        "leave.rejected",
        "leave.withdrawn",
        *OVERTIME_EVENT_TYPES,
        "payroll.calculated",
        "payslip.issued",
    }
)


#: Which fold family a consumed event belongs to.
class FoldKind(StrEnum):
    """The fold a command drives (the writer's method is chosen by this)."""

    DIMENSION = "dimension"
    OVERTIME = "overtime"
    ATTENDANCE = "attendance"
    LEAVE = "leave"


@dataclass(frozen=True, slots=True)
class FoldCommand:
    """One mapped event, ready for the consumer to load prior state and fold.

    `entity_id` is the overtime request / attendance record / leave request id
    the prior-state read is keyed by; it is `None` for dimension events, which
    are keyed by the employee alone.
    """

    kind: FoldKind
    event_type: str
    event_id: str | None
    tenant_id: UUID
    employee_id: UUID
    entity_id: UUID | None
    envelope: Mapping[str, Any]


def is_consumed_event_type(event_type: Any) -> bool:
    """Whether `event_type` is one this projection folds (never raises)."""
    return isinstance(event_type, str) and event_type in CONSUMED_EVENT_TYPES


def is_known_event_type(event_type: Any) -> bool:
    """Whether `event_type` is in the frozen event catalog (never raises)."""
    return isinstance(event_type, str) and event_type in KNOWN_EVENT_TYPES


def is_consumed_envelope(envelope: Mapping[str, Any]) -> bool:
    """Whether this is a v1 envelope of a consumed type (never raises)."""
    return envelope.get("version") == 1 and is_consumed_event_type(
        envelope.get("event_type")
    )


def map_envelope(envelope: Mapping[str, Any]) -> FoldCommand | None:
    """Map one envelope to a fold command; `None` for every non-consumed event.

    A consumed v1 envelope whose identifiers cannot be parsed raises
    `FoldError`; the consumer turns that into a committed skip with an
    ids-only log. Never echoes a payload value.
    """
    if not is_consumed_envelope(envelope):
        return None

    event_type = envelope.get("event_type")
    if not isinstance(event_type, str):  # pragma: no cover - narrowed above
        raise FoldError("event_type is missing")
    data = envelope.get("data")
    if not isinstance(data, Mapping):
        raise FoldError("event data is not an object")

    employee_id = _uuid_value(data.get("employee_id"), "employee_id")
    kind, entity_id = _kind_and_entity(event_type, data)
    return FoldCommand(
        kind=kind,
        event_type=event_type,
        event_id=_event_id(envelope),
        tenant_id=_uuid_value(envelope.get("tenant_id"), "tenant_id"),
        employee_id=employee_id,
        entity_id=entity_id,
        envelope=envelope,
    )


def _kind_and_entity(
    event_type: str, data: Mapping[str, Any]
) -> tuple[FoldKind, UUID | None]:
    """The fold family and (for facts) the entity id the prior read uses."""
    if event_type in EMPLOYEE_EVENT_TYPES:
        return FoldKind.DIMENSION, None
    if event_type in OVERTIME_EVENT_TYPES:
        return FoldKind.OVERTIME, _uuid_value(data.get("overtime_id"), "overtime_id")
    if event_type in ATTENDANCE_EVENT_TYPES:
        return (
            FoldKind.ATTENDANCE,
            _uuid_value(data.get("attendance_id"), "attendance_id"),
        )
    if event_type in LEAVE_EVENT_TYPES:
        return (
            FoldKind.LEAVE,
            _uuid_value(data.get("leave_request_id"), "leave_request_id"),
        )
    # Guarded by `is_consumed_envelope`; kept total for type checkers.
    raise FoldError("event_type is not consumed")  # pragma: no cover


def _uuid_value(value: object, field: str) -> UUID:
    """Parse a UUID field; a missing/invalid value is a malformed message."""
    if not isinstance(value, str) or not value:
        raise FoldError(f"{field} is missing")
    try:
        return UUID(value)
    except ValueError as exc:
        raise FoldError(f"{field} is not a UUID") from exc


def _event_id(envelope: Mapping[str, Any]) -> str | None:
    """The envelope's `event_id` when it is a plain string (ids-only logs)."""
    event_id = envelope.get("event_id")
    return event_id if isinstance(event_id, str) else None


__all__ = [
    "CONSUMED_EVENT_TYPES",
    "KNOWN_EVENT_TYPES",
    "FoldCommand",
    "FoldKind",
    "is_consumed_envelope",
    "is_consumed_event_type",
    "is_known_event_type",
    "map_envelope",
]
