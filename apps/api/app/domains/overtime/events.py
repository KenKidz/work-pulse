"""Event payload builders for the Overtime domain (T2).

The contracts live under `contracts/events/overtime/` and were written before
any producer (AGENTS.md rule 4). These are pure functions returning full
envelope-shaped dicts; the overtime service hands exactly this output to the
`EventOutbox` port in the same transaction as the transition (ADR 0012).

Payloads carry identifiers, the work date, hours and the deciding actor only.
`reason`, `manager_note` and `hr_note` are PII (`.ai/security.md`) and must
never appear here — the contract test sweeps for those keys. `hours` is a
count, not an amount; it is rendered as a JSON number at the wire boundary
(ADR 0017 decision 5), never a formatted string.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from uuid_utils.compat import uuid7

EventName = Literal[
    "overtime.requested",
    "overtime.approved",
    "overtime.verified",
    "overtime.rejected",
    "overtime.withdrawn",
]
RejectedFrom = Literal["requested", "manager_approved", "hr_verified"]


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


def _hours(value: Decimal) -> float:
    """Render 2dp hours as a JSON number (ADR 0017 decision 5)."""
    return float(value.quantize(Decimal("0.01")))


def overtime_requested(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    overtime_id: UUID,
    employee_id: UUID,
    work_date: date,
    hours: Decimal,
) -> dict[str, Any]:
    """Envelope for `overtime.requested` — submit to `REQUESTED`, no reason text."""
    return _envelope(
        "overtime.requested",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "overtime_id": str(overtime_id),
            "employee_id": str(employee_id),
            "work_date": work_date.isoformat(),
            "hours": _hours(hours),
        },
    )


def overtime_approved(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    overtime_id: UUID,
    employee_id: UUID,
    work_date: date,
    hours: Decimal,
    approved_by: UUID,
) -> dict[str, Any]:
    """Envelope for `overtime.approved` — the manager step.

    `approved_by` is the deciding actor's user id (ADR 0010); the service
    guarantees it is never the requester.
    """
    return _envelope(
        "overtime.approved",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "overtime_id": str(overtime_id),
            "employee_id": str(employee_id),
            "work_date": work_date.isoformat(),
            "hours": _hours(hours),
            "approved_by": str(approved_by),
        },
    )


def overtime_verified(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    overtime_id: UUID,
    employee_id: UUID,
    work_date: date,
    hours: Decimal,
    verified_by: UUID,
) -> dict[str, Any]:
    """Envelope for `overtime.verified` — the HR step, hours unchanged.

    HR accepts the requested hours exactly (PO Decision 3); `verified_by` is
    the deciding actor's user id (ADR 0010).
    """
    return _envelope(
        "overtime.verified",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "overtime_id": str(overtime_id),
            "employee_id": str(employee_id),
            "work_date": work_date.isoformat(),
            "hours": _hours(hours),
            "verified_by": str(verified_by),
        },
    )


def overtime_rejected(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    overtime_id: UUID,
    employee_id: UUID,
    work_date: date,
    hours: Decimal,
    rejected_by: UUID,
    rejected_from: RejectedFrom,
) -> dict[str, Any]:
    """Envelope for `overtime.rejected` — any open state, note text excluded."""
    return _envelope(
        "overtime.rejected",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "overtime_id": str(overtime_id),
            "employee_id": str(employee_id),
            "work_date": work_date.isoformat(),
            "hours": _hours(hours),
            "rejected_by": str(rejected_by),
            "rejected_from": rejected_from,
        },
    )


def overtime_withdrawn(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    overtime_id: UUID,
    employee_id: UUID,
    work_date: date,
) -> dict[str, Any]:
    """Envelope for `overtime.withdrawn` — own pre-verification request only."""
    return _envelope(
        "overtime.withdrawn",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "overtime_id": str(overtime_id),
            "employee_id": str(employee_id),
            "work_date": work_date.isoformat(),
        },
    )
