"""Pure envelope → `PresenceFact` mapping for the realtime consumer (ADR 0013).

The consumer's whole domain surface: `attendance.checked_in` /
`attendance.checked_out` v1 envelopes and date-covering `leave.approved`
envelopes become full-row `PresenceFact`s derived from the event alone — never
PostgreSQL (D1, ADR 0014). Redelivery maps to an identical fact, and the
wall's upserts are full-row and idempotent, so replay is harmless by contract.

Filtering and failure rules:

- every other event type or version (`attendance.late`, `employee.*`, future
  versions) maps to `None` **silently**: the topic carries the whole catalog
  and the wall projects its own events (`is_realtime_envelope` is the filter —
  `attendance.late` stays Notifications'/Analytics' fact, while the wall's
  minutes ride the checked-in payload, D1).
- `leave.approved` is windowed by the **consume date**: it maps to an
  on-leave fact only when `[start_date, end_date]` covers that date. A
  future-dated or already-ended approval is filtered silently — it changes no
  wall state today. `leave.requested`/`rejected`/`withdrawn` never reach the
  wall (ADR 0014: `PENDING` is invisible and a rejection/withdrawal changes
  no presence state).
- an event of ours with a missing or invalid fact-required field maps to
  `None` too, but that `None` is a *skip*: `map_envelope` never raises and
  never yields a partial fact, and the consumer tells the two `None`s apart
  with `is_realtime_envelope` to log the skip (ids only).
- pre-extension payloads (no `minutes_late`, or no `late`/`minutes_late` on
  checked-out) default to the values their absence implies today
  (`null` / `false`) — the crash-window replay the spec accepts (ADR 0013).
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.shared.presence import PresenceFact, PresenceReason, PresenceStatus

CHECKED_IN = "attendance.checked_in"
CHECKED_OUT = "attendance.checked_out"
LEAVE_APPROVED = "leave.approved"

_REALTIME_EVENT_TYPES = frozenset({CHECKED_IN, CHECKED_OUT, LEAVE_APPROVED})

# `work_mode` → the wall's status on check-in (the write path's mapping:
# onsite → present, remote → remote). Anything else is a malformed payload.
_CHECKED_IN_STATUS: dict[str, PresenceStatus] = {
    "onsite": "present",
    "remote": "remote",
}


def _today() -> date:
    """The wall's consume date (server UTC), used only to window leave."""
    return datetime.now(UTC).date()


def is_realtime_envelope(
    envelope: Mapping[str, Any], *, today: date | None = None
) -> bool:
    """The consumer's filter: a v1 event the wall projects for `today`.

    `False` for everything else — `attendance.late` included (it stays the
    lateness fact for Notifications/Analytics), and a `leave.approved` whose
    range does not cover the consume date (ADR 0014: no future/past leave on
    today's wall). Never raises: a missing or non-string `event_type`, or a
    malformed leave window, is simply not projected.
    """
    event_type = envelope.get("event_type")
    if (
        not isinstance(event_type, str)
        or event_type not in _REALTIME_EVENT_TYPES
        or envelope.get("version") != 1
    ):
        return False
    if event_type != LEAVE_APPROVED:
        return True
    window = _leave_window(envelope)
    if window is None:
        return False
    start_date, end_date = window
    return start_date <= (today or _today()) <= end_date


def map_envelope(
    envelope: Mapping[str, Any], *, today: date | None = None
) -> PresenceFact | None:
    """Project one envelope onto a `PresenceFact`; never raises.

    `today` is the consume date used to window `leave.approved`; omit it to
    use the server's UTC date. `None` for filtered events and for
    ours-but-malformed ones; callers use `is_realtime_envelope` to tell the
    two apart (silent vs. logged skip).
    """
    consume_date = today if today is not None else _today()
    if not is_realtime_envelope(envelope, today=consume_date):
        return None
    try:
        return _build_fact(envelope, today=consume_date)
    except (KeyError, TypeError, ValueError, ValidationError):
        # A missing fact-required field or an unparseable value: the consumer
        # logs the skip (ids only) and the offset still advances — never a
        # partial fact, never a crash on one bad message.
        return None


def _leave_window(envelope: Mapping[str, Any]) -> tuple[date, date] | None:
    """`(start_date, end_date)` from a leave envelope; `None` if malformed."""
    try:
        data = envelope["data"]
        if not isinstance(data, Mapping):
            return None
        return _workday(data["start_date"]), _workday(data["end_date"])
    except (KeyError, TypeError, ValueError):
        return None


def _build_fact(envelope: Mapping[str, Any], *, today: date) -> PresenceFact:
    """Derive the fact; raises internally on anything not fact-shaped."""
    data = envelope["data"]
    if not isinstance(data, Mapping):
        raise TypeError("data must be an object")
    event_type = envelope["event_type"]
    occurred_at = _timestamp(envelope["occurred_at"])

    if event_type == LEAVE_APPROVED:
        # The approval is a full-day state for the consume date: no record, no
        # timestamp, not late (ADR 0014). The window was checked by
        # `is_realtime_envelope`; re-checking here keeps the fact honest if
        # `_build_fact` is ever called directly.
        window = _leave_window(envelope)
        if window is None or not (window[0] <= today <= window[1]):
            raise ValueError("leave does not cover the consume date")
        return PresenceFact(
            tenant_id=_identifier(envelope["tenant_id"]),
            workday=today,
            employee_id=_identifier(data["employee_id"]),
            attendance_id=None,
            status="on_leave",
            late=False,
            minutes_late=None,
            since=None,
            reason="leave_approved",
            occurred_at=occurred_at,
        )

    # `since` is the envelope's `occurred_at` for both attendance transitions:
    # the write path passes one `now` to the record and the builder
    # (checked_in_at / checked_out_at), so the mapped row equals the REST board
    # row exactly.
    status: PresenceStatus
    reason: PresenceReason
    if event_type == CHECKED_IN:
        mode = data["work_mode"]
        mode_status = _CHECKED_IN_STATUS.get(mode) if isinstance(mode, str) else None
        if mode_status is None:
            raise ValueError("unknown work_mode")
        status = mode_status
        reason = "checked_in"
    else:
        status = "checked_out"
        reason = "checked_out"

    return PresenceFact(
        tenant_id=_identifier(envelope["tenant_id"]),
        workday=_workday(data["workday"]),
        employee_id=_identifier(data["employee_id"]),
        attendance_id=_identifier(data["attendance_id"]),
        status=status,
        late=_flag(data, "late"),
        minutes_late=_minutes_late(data),
        since=occurred_at,
        reason=reason,
        occurred_at=occurred_at,
    )


def _identifier(value: Any) -> UUID:
    """UUID from the wire form; anything unparseable raises (caught above)."""
    return UUID(str(value))


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError("occurred_at must be a string")
    return datetime.fromisoformat(value)


def _workday(value: Any) -> date:
    if not isinstance(value, str):
        raise TypeError("workday must be a string")
    return date.fromisoformat(value)


def _flag(data: Mapping[str, Any], key: str) -> bool:
    """`key` when it is a boolean; `False` when absent (pre-extension rows)."""
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _minutes_late(data: Mapping[str, Any]) -> int | None:
    """The recorded minutes; absent → `null` (pre-extension rows)."""
    value = data.get("minutes_late")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("minutes_late must be a non-negative integer or null")
    return value
