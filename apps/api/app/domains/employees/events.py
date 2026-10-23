"""Event payload builders for the Employees domain (Phase 1).

The contracts live under `contracts/events/employees/` (T2 — contracts before
any producer/consumer). These are pure functions returning full
envelope-shaped dicts; Phase 3's publisher calls exactly this module once the
outbox lands. Nothing is published in Phase 1 — the contract test
(`tests/contract/test_employee_events.py`) proves the shapes builders emit
match the schemas, and that no PII-shaped key can slip into a payload.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

from uuid_utils.compat import uuid7

EventName = Literal[
    "employee.created",
    "employee.updated",
    "employee.transferred",
    "employee.terminated",
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


def employee_created(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    employee_id: UUID,
    department_id: UUID,
    position_id: UUID,
    location_id: UUID,
    employment_type: str,
    hired_at: date | None = None,
) -> dict[str, Any]:
    """Envelope for `employee.created` — identifiers only, no PII.

    `hired_at` is the tenure anchor Analytics & Reports folds into seniority
    bands (ADR 0019). It is optional on the wire (ADR 0013 additive pattern):
    the producer always fills it; pre-extension envelopes degrade to unknown.
    """
    return _envelope(
        "employee.created",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "position_id": str(position_id),
            "location_id": str(location_id),
            "employment_type": employment_type,
            "hired_at": hired_at.isoformat() if hired_at is not None else None,
        },
    )


def employee_updated(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    employee_id: UUID,
    changed_fields: Sequence[str],
) -> dict[str, Any]:
    """Envelope for `employee.updated` — field NAMES only, never values."""
    return _envelope(
        "employee.updated",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "employee_id": str(employee_id),
            "changed_fields": list(changed_fields),
        },
    )


def employee_transferred(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    employee_id: UUID,
    from_department_id: UUID,
    to_department_id: UUID,
    effective_from: date,
    to_position_id: UUID | None = None,
    to_location_id: UUID | None = None,
) -> dict[str, Any]:
    """Envelope for `employee.transferred` — new period, append-only history.

    `to_position_id`/`to_location_id` ride along so Analytics & Reports can
    stamp a fact with the dimension triple as of its own date (ADR 0019).
    Optional on the wire (ADR 0013 additive pattern): the producer fills them,
    pre-extension envelopes keep the previous values.
    """
    return _envelope(
        "employee.transferred",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "employee_id": str(employee_id),
            "from_department_id": str(from_department_id),
            "to_department_id": str(to_department_id),
            "effective_from": effective_from.isoformat(),
            "to_position_id": (
                str(to_position_id) if to_position_id is not None else None
            ),
            "to_location_id": (
                str(to_location_id) if to_location_id is not None else None
            ),
        },
    )


def employee_terminated(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    employee_id: UUID,
    effective_date: date,
) -> dict[str, Any]:
    """Envelope for `employee.terminated` — reason stays on the audit entry."""
    return _envelope(
        "employee.terminated",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "employee_id": str(employee_id),
            "effective_date": effective_date.isoformat(),
        },
    )
