"""Event payload builders for the Recruitment domain (T1).

The contracts live under `contracts/events/recruitment/` and land before any
producer (AGENTS.md rule 4). These are pure functions returning full
envelope-shaped dicts; the job/candidate/interview services in T2–T5 hand
exactly this output to the `EventOutbox` port in the same transaction as the
transition (ADR 0012).

Payloads carry identifiers, pipeline state, the opening count and the deciding
actor only. Candidate names, emails, phones, notes, interview feedback,
ratings and rejection/withdrawal reasons are PII (`.ai/security.md`) and must
never appear here — the contract test sweeps for those keys. `*_by` fields are
actor **user** ids (ADR 0010); `interviewer_id` is the assigned employee's id.
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from uuid_utils.compat import uuid7

EventName = Literal[
    "job.opened",
    "job.closed",
    "candidate.created",
    "candidate.advanced",
    "candidate.rejected",
    "candidate.withdrawn",
    "candidate.hired",
    "interview.scheduled",
    "interview.completed",
    "interview.cancelled",
]
CandidateSource = Literal["referral", "job_board", "agency", "other"]
CandidateStage = Literal["applied", "interviewing", "offered"]


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


def job_opened(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    job_id: UUID,
    position_id: UUID,
    department_id: UUID,
    location_id: UUID,
    openings: int,
) -> dict[str, Any]:
    """Envelope for `job.opened` — `draft → open`, no job text on the bus."""
    return _envelope(
        "job.opened",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "job_id": str(job_id),
            "position_id": str(position_id),
            "department_id": str(department_id),
            "location_id": str(location_id),
            "openings": openings,
        },
    )


def job_closed(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    job_id: UUID,
    closed_by: UUID,
) -> dict[str, Any]:
    """Envelope for `job.closed` — `open → closed`, terminal."""
    return _envelope(
        "job.closed",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "job_id": str(job_id),
            "closed_by": str(closed_by),
        },
    )


def candidate_created(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    candidate_id: UUID,
    job_id: UUID,
    source: CandidateSource,
) -> dict[str, Any]:
    """Envelope for `candidate.created` — intake source code only, no PII."""
    return _envelope(
        "candidate.created",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "candidate_id": str(candidate_id),
            "job_id": str(job_id),
            "source": source,
        },
    )


def candidate_advanced(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    candidate_id: UUID,
    job_id: UUID,
    from_stage: CandidateStage,
    to_stage: CandidateStage,
) -> dict[str, Any]:
    """Envelope for `candidate.advanced` — exactly one stage forward.

    Wire keys are `from`/`to` (`from` is a Python keyword, so the builder names
    the parameters `from_stage`/`to_stage`); the service guarantees the pair is
    one of `applied → interviewing` or `interviewing → offered`.
    """
    return _envelope(
        "candidate.advanced",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "candidate_id": str(candidate_id),
            "job_id": str(job_id),
            "from": from_stage,
            "to": to_stage,
        },
    )


def candidate_rejected(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    candidate_id: UUID,
    job_id: UUID,
    rejected_from: CandidateStage,
    rejected_by: UUID,
) -> dict[str, Any]:
    """Envelope for `candidate.rejected` — source stage and actor only.

    `rejected_by` is the deciding actor's user id (ADR 0010); the rejection
    note never leaves the database.
    """
    return _envelope(
        "candidate.rejected",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "candidate_id": str(candidate_id),
            "job_id": str(job_id),
            "rejected_from": rejected_from,
            "rejected_by": str(rejected_by),
        },
    )


def candidate_withdrawn(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    candidate_id: UUID,
    job_id: UUID,
    withdrawn_from: CandidateStage,
) -> dict[str, Any]:
    """Envelope for `candidate.withdrawn` — source stage only, no note text."""
    return _envelope(
        "candidate.withdrawn",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "candidate_id": str(candidate_id),
            "job_id": str(job_id),
            "withdrawn_from": withdrawn_from,
        },
    )


def candidate_hired(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    candidate_id: UUID,
    job_id: UUID,
    employee_id: UUID,
    hired_by: UUID,
) -> dict[str, Any]:
    """Envelope for `candidate.hired` — after the Employees seam succeeds.

    `employee_id` is the record created through `EmployeeHirePort` (ADR 0020);
    Employees emits `employee.created` as usual and this event is the
    recruitment-side fact. `hired_by` is the acting HR user's id (ADR 0010).
    """
    return _envelope(
        "candidate.hired",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "candidate_id": str(candidate_id),
            "job_id": str(job_id),
            "employee_id": str(employee_id),
            "hired_by": str(hired_by),
        },
    )


def interview_scheduled(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    interview_id: UUID,
    candidate_id: UUID,
    job_id: UUID,
    interviewer_id: UUID,
) -> dict[str, Any]:
    """Envelope for `interview.scheduled` — identifiers, no time or notes."""
    return _envelope(
        "interview.scheduled",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "interview_id": str(interview_id),
            "candidate_id": str(candidate_id),
            "job_id": str(job_id),
            "interviewer_id": str(interviewer_id),
        },
    )


def interview_completed(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    interview_id: UUID,
    candidate_id: UUID,
    job_id: UUID,
    interviewer_id: UUID,
    completed_by: UUID,
) -> dict[str, Any]:
    """Envelope for `interview.completed` — feedback filed, no rating text.

    `completed_by` is the assigned interviewer's user id (ADR 0010);
    `interviewer_id` stays the employee id of the assigned person.
    """
    return _envelope(
        "interview.completed",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "interview_id": str(interview_id),
            "candidate_id": str(candidate_id),
            "job_id": str(job_id),
            "interviewer_id": str(interviewer_id),
            "completed_by": str(completed_by),
        },
    )


def interview_cancelled(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    occurred_at: datetime,
    interview_id: UUID,
    candidate_id: UUID,
    job_id: UUID,
    cancelled_by: UUID,
) -> dict[str, Any]:
    """Envelope for `interview.cancelled` — terminal, no note text."""
    return _envelope(
        "interview.cancelled",
        tenant_id,
        actor_id,
        occurred_at,
        {
            "interview_id": str(interview_id),
            "candidate_id": str(candidate_id),
            "job_id": str(job_id),
            "cancelled_by": str(cancelled_by),
        },
    )
