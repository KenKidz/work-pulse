"""Event payload builders for the Attendance domain (Phase 1).

The contracts live under `contracts/events/attendance/` (T2 — contracts before
any producer/consumer). These are pure functions returning full
envelope-shaped dicts; Phase 3's publisher calls exactly this module once the
outbox lands. Nothing is published in Phase 1 — the contract test
(`tests/contract/test_attendance_events.py`) proves the shapes builders emit
match the schemas, and that no PII-shaped key (notes included) can slip into a
payload.
"""

from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

from uuid_utils.compat import uuid7

EventName = Literal[
    "attendance.checked_in",
    "attendance.checked_out",
    "attendance.late",
]


def _envelope(
    event_type: EventName,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Wrap a validated domain payload in the shared v1 envelope.

    `occurred_at` is the domain time of the fact (RFC 3339 date-time in UTC);
    the router-injected `now` clock (conventions) flows into it so builders
    stay deterministic — Phase 3's publisher must not re-stamp.
    """
    return {
        "event_id": f"evt_{uuid7()}",
        "event_type": event_type,
        "version": 1,
        "tenant_id": str(tenant_id),
        "occurred_at": occurred_at.astimezone(UTC).isoformat(),
        "actor_id": str(actor_id),
        "data": data,
    }


def attendance_checked_in(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    attendance_id: UUID,
    employee_id: UUID,
    workday: date,
    work_mode: str,
    late: bool,
    minutes_late: int | None,
) -> dict[str, Any]:
    """Envelope for `attendance.checked_in` — identifiers, date and flags only.

    `minutes_late` is the board's whole minutes past shift start + grace when
    `late` is true, `null` otherwise (ADR 0013's additive extension — the key
    is always present so the realtime consumer stays stateless).
    """
    return _envelope(
        "attendance.checked_in",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "attendance_id": str(attendance_id),
            "employee_id": str(employee_id),
            "workday": workday.isoformat(),
            "work_mode": work_mode,
            "late": late,
            "minutes_late": minutes_late,
        },
    )


def attendance_checked_out(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    attendance_id: UUID,
    employee_id: UUID,
    workday: date,
    duration_minutes: int,
    late: bool,
    minutes_late: int | None,
) -> dict[str, Any]:
    """Envelope for `attendance.checked_out` — derived duration, no times.

    `late` mirrors the closed record's immutable flag; `minutes_late` is
    recomputed at emit time from the record's shift (`null` when not late or
    the shift no longer resolves) — ADR 0013's additive extension, always
    present.
    """
    return _envelope(
        "attendance.checked_out",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "attendance_id": str(attendance_id),
            "employee_id": str(employee_id),
            "workday": workday.isoformat(),
            "duration_minutes": duration_minutes,
            "late": late,
            "minutes_late": minutes_late,
        },
    )


def attendance_late(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    attendance_id: UUID,
    employee_id: UUID,
    workday: date,
    shift_id: UUID,
    minutes_late: int,
) -> dict[str, Any]:
    """Envelope for `attendance.late` — the shift and whole minutes past it."""
    return _envelope(
        "attendance.late",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "attendance_id": str(attendance_id),
            "employee_id": str(employee_id),
            "workday": workday.isoformat(),
            "shift_id": str(shift_id),
            "minutes_late": minutes_late,
        },
    )
