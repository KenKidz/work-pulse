"""Event payload builders for the Performance domain (T3).

The eight v1 contracts live under `contracts/events/performance/` and land
before any producer (AGENTS.md rule 4). These are pure functions returning full
envelope-shaped dicts; the goal, review and feedback services in T5–T7 hand
exactly this output to the `EventOutbox` port in the same transaction as the
transition (ADR 0012).

Payloads carry identifiers and workflow state only. Goal `title`/`description`,
review `period`/`strengths`/`improvements`, feedback `comment`, employee-skill
`note` and the `rating` are PII (`.ai/security.md`) and must never appear here —
the contract test sweeps for those keys. Every payload carries `employee_id`
(the subject), so the ADR 0012 Kafka key resolves through its first fallback and
needs no amendment.
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from uuid_utils.compat import uuid7

EventName = Literal[
    "performance.goal_created",
    "performance.goal_activated",
    "performance.goal_completed",
    "performance.goal_cancelled",
    "performance.review_started",
    "performance.review_completed",
    "performance.review_cancelled",
    "performance.feedback_submitted",
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


def goal_created(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    goal_id: UUID,
    employee_id: UUID,
) -> dict[str, Any]:
    """Envelope for `performance.goal_created` — the goal entered `draft`."""
    return _envelope(
        "performance.goal_created",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "goal_id": str(goal_id),
            "employee_id": str(employee_id),
            "status": "draft",
        },
    )


def goal_activated(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    goal_id: UUID,
    employee_id: UUID,
) -> dict[str, Any]:
    """Envelope for `performance.goal_activated` — `draft → active`."""
    return _envelope(
        "performance.goal_activated",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "goal_id": str(goal_id),
            "employee_id": str(employee_id),
            "status": "active",
        },
    )


def goal_completed(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    goal_id: UUID,
    employee_id: UUID,
) -> dict[str, Any]:
    """Envelope for `performance.goal_completed` — `active → completed`."""
    return _envelope(
        "performance.goal_completed",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "goal_id": str(goal_id),
            "employee_id": str(employee_id),
            "status": "completed",
        },
    )


def goal_cancelled(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    goal_id: UUID,
    employee_id: UUID,
) -> dict[str, Any]:
    """Envelope for `performance.goal_cancelled` — terminal side exit."""
    return _envelope(
        "performance.goal_cancelled",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "goal_id": str(goal_id),
            "employee_id": str(employee_id),
            "status": "cancelled",
        },
    )


def review_started(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    review_id: UUID,
    employee_id: UUID,
) -> dict[str, Any]:
    """Envelope for `performance.review_started` — the review entered `draft`."""
    return _envelope(
        "performance.review_started",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "review_id": str(review_id),
            "employee_id": str(employee_id),
            "status": "draft",
        },
    )


def review_completed(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    review_id: UUID,
    employee_id: UUID,
) -> dict[str, Any]:
    """Envelope for `performance.review_completed` — `draft → completed`."""
    return _envelope(
        "performance.review_completed",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "review_id": str(review_id),
            "employee_id": str(employee_id),
            "status": "completed",
        },
    )


def review_cancelled(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    review_id: UUID,
    employee_id: UUID,
) -> dict[str, Any]:
    """Envelope for `performance.review_cancelled` — `draft → cancelled`."""
    return _envelope(
        "performance.review_cancelled",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "review_id": str(review_id),
            "employee_id": str(employee_id),
            "status": "cancelled",
        },
    )


def feedback_submitted(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    feedback_id: UUID,
    review_id: UUID,
    employee_id: UUID,
) -> dict[str, Any]:
    """Envelope for `performance.feedback_submitted` — ids only, no comment.

    `employee_id` is the review's subject (the Kafka key), never the provider's
    id; the feedback comment itself stays in the database.
    """
    return _envelope(
        "performance.feedback_submitted",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "feedback_id": str(feedback_id),
            "review_id": str(review_id),
            "employee_id": str(employee_id),
        },
    )
