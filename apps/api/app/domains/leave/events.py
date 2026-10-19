"""Event payload builders for the Leave domain (Phase 3, T2).

The contracts live under `contracts/events/leave/` (contracts before any
producer/consumer). These are pure functions returning full envelope-shaped
dicts; the leave service hands exactly this output to the `EventOutbox` port in
the same transaction as the transition (ADR 0012).

Payloads carry identifiers, the leave-type code, inclusive dates and the
decision actor only. `reason` and `decision_note` are PII (`.ai/security.md`)
and must never appear here — the contract test sweeps for those keys.
"""

from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

from uuid_utils.compat import uuid7

EventName = Literal[
    "leave.requested",
    "leave.approved",
    "leave.rejected",
    "leave.withdrawn",
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
    stay deterministic — the outbox stores and the relay publishes it verbatim
    (ADR 0012), never re-stamped.
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


def leave_requested(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    leave_request_id: UUID,
    employee_id: UUID,
    leave_type_code: str,
    start_date: date,
    end_date: date,
    days: int,
) -> dict[str, Any]:
    """Envelope for `leave.requested` — submit to `PENDING`, no reason text."""
    return _envelope(
        "leave.requested",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "leave_request_id": str(leave_request_id),
            "employee_id": str(employee_id),
            "leave_type_code": leave_type_code,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days": days,
        },
    )


def leave_approved(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    leave_request_id: UUID,
    employee_id: UUID,
    leave_type_code: str,
    start_date: date,
    end_date: date,
    days: int,
    decided_by: UUID,
) -> dict[str, Any]:
    """Envelope for `leave.approved` — the only wall-affecting transition.

    `decided_by` is the deciding actor's user id (ADR 0010); the service
    guarantees it is never the requester.
    """
    return _envelope(
        "leave.approved",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "leave_request_id": str(leave_request_id),
            "employee_id": str(employee_id),
            "leave_type_code": leave_type_code,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days": days,
            "decided_by": str(decided_by),
        },
    )


def leave_rejected(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    leave_request_id: UUID,
    employee_id: UUID,
    leave_type_code: str,
    start_date: date,
    end_date: date,
    decided_by: UUID,
) -> dict[str, Any]:
    """Envelope for `leave.rejected` — balance untouched, wall-silent."""
    return _envelope(
        "leave.rejected",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "leave_request_id": str(leave_request_id),
            "employee_id": str(employee_id),
            "leave_type_code": leave_type_code,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "decided_by": str(decided_by),
        },
    )


def leave_withdrawn(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    leave_request_id: UUID,
    employee_id: UUID,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Envelope for `leave.withdrawn` — own `PENDING` only, wall-silent."""
    return _envelope(
        "leave.withdrawn",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "leave_request_id": str(leave_request_id),
            "employee_id": str(employee_id),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    )
