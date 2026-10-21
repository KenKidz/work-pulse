"""Event payload builders for the Payroll domain (T3).

The contracts live under `contracts/events/payroll/` and were written before
any producer (T1, AGENTS.md rule 4). These are pure functions returning full
envelope-shaped dicts; the payroll service hands exactly this output to the
`EventOutbox` port in the same transaction as the transition (ADR 0012).

Payloads carry identifiers, period and counts only. Amounts, payslip lines,
bonus notes and run totals are PII and must never appear here
(`.ai/security.md`; `.ai/features/payroll.md` → Events). `payroll.calculated`
is run-level and therefore keyed by `run_id` (ADR 0012 amendment);
`payslip.issued` stays employee-keyed.
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from uuid_utils.compat import uuid7

EventName = Literal["payroll.calculated", "payslip.issued"]


def _envelope(
    event_type: EventName,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Wrap a validated domain payload in the shared v1 envelope.

    `occurred_at` is the domain time of the fact (RFC 3339 date-time in UTC);
    the caller-supplied clock flows into it so builders stay deterministic —
    the outbox stores and the relay publishes it verbatim (ADR 0012), never
    re-stamped.
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


def payroll_calculated(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    run_id: UUID,
    period: str,
    employee_count: int,
) -> dict[str, Any]:
    """Envelope for `payroll.calculated` — provisional payslips exist.

    Run-level: `data.run_id` is the Kafka message key (ADR 0012 amendment).
    """
    return _envelope(
        "payroll.calculated",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "run_id": str(run_id),
            "period": period,
            "employee_count": employee_count,
        },
    )


def payslip_issued(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    payslip_id: UUID,
    run_id: UUID,
    employee_id: UUID,
    period: str,
) -> dict[str, Any]:
    """Envelope for `payslip.issued` — one per payslip flipped at finalize."""
    return _envelope(
        "payslip.issued",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "payslip_id": str(payslip_id),
            "run_id": str(run_id),
            "employee_id": str(employee_id),
            "period": period,
        },
    )
