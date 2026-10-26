"""Use cases for the Recruitment domain — jobs (T2), candidates (T3), hire (T4),
interviews (T5).

Rules implemented here (pinned in `.ai/features/recruitment.md`):

- **Job state machine**: created `DRAFT`; `open` moves `DRAFT → OPEN` and stamps
  `opened_at`; `close` moves `OPEN → CLOSED` and stamps `closed_at`; `CLOSED`
  is terminal. Edits are allowed while `DRAFT`/`OPEN` only. Every transition is
  an event + an audit entry appended in the mutation's transaction (ADR 0012);
  creating a draft emits nothing (the catalog's first job event is `job.opened`).
- **Candidate state machine**: created `APPLIED` on an `OPEN` job; `advance`
  steps exactly one stage (`APPLIED → INTERVIEWING → OFFERED`); `reject` and
  `withdraw` are the terminal side exits from any pre-hire state. Terminal
  candidates are never re-opened. Every mutation is an event + an audit entry
  in the same transaction (ADR 0012).
- **Hire** (T4, ADR 0020): only from `OFFERED` on an `OPEN` job with a free
  `openings` slot. The candidate then the job are locked, the employee is
  created through the `EmployeeHirePort` on the caller's session, and the
  candidate flips to `HIRED` with the returned id. A seam failure propagates
  unchanged and rolls the whole hire back (the candidate stays `OFFERED`).
- **Interview state machine** (T5): scheduling is management-only and requires
  an `interviewing` candidate plus an active employee interviewer. `update` and
  `cancel` are management-only while `scheduled`; `complete` is reserved for
  the assigned interviewer (feedback is the only path to `completed`) and never
  moves the candidate's stage. Scheduled/completed/cancelled/updated audit
  entries carry no interview or feedback text.
- **Org references**: `department_id`/`position_id`/`location_id` are validated
  through the Organizations services — never their tables (AGENTS.md rule 3).
  Their 404s propagate unchanged; archived refs are accepted at job creation
  (risk 4 in the spec) and refused later by Employees at hire time.
- **Access**: management mutations are HR/Admin/Super Admin only
  (`recruitment_denied` 403). Reads: management sees everything, an assigned
  interviewer sees what their interviews make visible (jobs, candidates, own
  interviews), everyone else gets `403`; an out-of-scope entity id is `404`
  (existence is not leaked).
- **Clock**: every mutation takes `now` as its last parameter — the router is
  the only wall-clock reader, and tests inject a fixed instant.
- **PII**: candidate names, emails, phones, notes and side-exit notes never
  appear in this module's events or audit `changes` values. Audit records the
  PII field names with the `"changed"` marker only; events carry identifiers,
  state and the intake source code. Interview notes and feedback notes follow
  the same rule; ratings reach the audit trail but never the event stream.
"""

from collections.abc import Sequence
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Protocol, cast
from uuid import UUID

from uuid_utils.compat import uuid7

from app.domains.recruitment import events
from app.domains.recruitment.exceptions import (
    CandidateNotFoundError,
    CandidateNotOpenError,
    CandidateStageConflictError,
    HireDateInFutureError,
    InterviewerNotActiveError,
    InterviewerNotFoundError,
    InterviewNotFoundError,
    InterviewNotScheduledError,
    JobNotDraftError,
    JobNotFoundError,
    JobNotOpenError,
    JobOpeningsFilledError,
    RecruitmentDeniedError,
)
from app.domains.recruitment.policies import (
    ensure_can_manage,
    ensure_has_standing,
    is_management,
)
from app.domains.recruitment.repository import (
    CandidateFilters,
    EmployeeHirePayload,
    EmployeeHirePort,
    InterviewFilters,
    JobFilters,
    RecruitmentRepository,
)
from app.domains.recruitment.schemas import (
    Candidate,
    CandidateCreate,
    CandidateDecision,
    CandidateDetail,
    CandidateListPage,
    CandidateRejectedFrom,
    CandidateStatus,
    CandidateUpdate,
    HireCandidate,
    Interview,
    InterviewFeedback,
    InterviewListPage,
    InterviewSchedule,
    InterviewStatus,
    InterviewUpdate,
    Job,
    JobCreate,
    JobListPage,
    JobStatus,
    JobUpdate,
)
from app.shared.audit import AuditEntryCreate, AuditWriter
from app.shared.identity import Identity
from app.shared.outbox import EventOutbox
from app.shared.pagination import PageParams

PII_CHANGED = "changed"  # audit marker: field changed, value deliberately absent
# The states a candidate may be rejected/withdrawn from (all pre-hire stages).
_CANDIDATE_OPEN = (
    CandidateStatus.APPLIED,
    CandidateStatus.INTERVIEWING,
    CandidateStatus.OFFERED,
)
_CANDIDATE_PII_FIELDS = frozenset(
    {"first_name", "last_name", "email", "phone", "notes"}
)


class OrgReferenceReader(Protocol):
    """The Organizations read used to validate job refs (service-level seam)."""

    async def get(self, identity: Identity, reference_id: UUID, /) -> object: ...


def _value(value: object) -> object:
    """Audit-safe JSON value: UUIDs/datetimes/StrEnums become strings."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    return value


class RecruitmentService:
    def __init__(
        self,
        repo: RecruitmentRepository,
        audit: AuditWriter,
        outbox: EventOutbox,
        departments: OrgReferenceReader,
        positions: OrgReferenceReader,
        locations: OrgReferenceReader,
        hire: EmployeeHirePort,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._outbox = outbox
        self._departments = departments
        self._positions = positions
        self._locations = locations
        self._hire = hire

    # --- mutations -----------------------------------------------------------

    async def create_job(
        self, identity: Identity, payload: JobCreate, now: datetime
    ) -> Job:
        """Create a job in `draft` — no event until it is opened."""
        ensure_can_manage(identity)
        await self._ensure_org_refs(
            identity,
            payload.department_id,
            payload.position_id,
            payload.location_id,
        )

        job = await self._repo.create_job(
            identity.tenant_id,
            uuid7(),
            title=payload.title,
            description=payload.description,
            department_id=payload.department_id,
            position_id=payload.position_id,
            location_id=payload.location_id,
            openings=payload.openings,
            created_by=identity.actor_id,
        )

        changes: dict[str, Any] = {
            "title": {"old": None, "new": job.title},
            "department_id": {"old": None, "new": str(job.department_id)},
            "position_id": {"old": None, "new": str(job.position_id)},
            "location_id": {"old": None, "new": str(job.location_id)},
            "openings": {"old": None, "new": job.openings},
            "status": {"old": None, "new": job.status.value},
        }
        if job.description is not None:
            changes["description"] = {"old": None, "new": job.description}
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="job.created",
                entity_type="job",
                entity_id=job.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return job

    async def update_job(
        self,
        identity: Identity,
        job_id: UUID,
        payload: JobUpdate,
        now: datetime,
    ) -> Job:
        """Edit a `draft`/`open` job; a closed job is `409 job_not_open`."""
        ensure_can_manage(identity)
        existing = await self._get_manageable(identity, job_id)
        if existing.status is JobStatus.CLOSED:
            raise JobNotOpenError()

        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            return existing

        # Only the refs actually supplied are re-validated (Organizations 404s
        # propagate unchanged).
        if "department_id" in changes:
            await self._departments.get(identity, changes["department_id"])
        if "position_id" in changes:
            await self._positions.get(identity, changes["position_id"])
        if "location_id" in changes:
            await self._locations.get(identity, changes["location_id"])

        updated = await self._repo.update_job(
            identity.tenant_id, job_id, now=now, **changes
        )
        if updated is None:
            # The job was closed between the read and the conditional update.
            raise JobNotOpenError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="job.updated",
                entity_type="job",
                entity_id=job_id,
                occurred_at=now,
                changes={
                    field: {
                        "old": _value(getattr(existing, field)),
                        "new": _value(getattr(updated, field)),
                    }
                    for field in changes
                },
            )
        )
        return updated

    async def open_job(self, identity: Identity, job_id: UUID, now: datetime) -> Job:
        """Open a draft job: `draft → open` (`409 job_not_draft` otherwise)."""
        ensure_can_manage(identity)
        existing = await self._get_manageable(identity, job_id)
        if existing.status is not JobStatus.DRAFT:
            raise JobNotDraftError()

        updated = await self._repo.transition_open(identity.tenant_id, job_id, now=now)
        if updated is None:
            raise JobNotDraftError()

        await self._outbox.append(
            events.job_opened(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                job_id=updated.id,
                position_id=updated.position_id,
                department_id=updated.department_id,
                location_id=updated.location_id,
                openings=updated.openings,
            )
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="job.opened",
                entity_type="job",
                entity_id=updated.id,
                occurred_at=now,
                changes={
                    "status": {
                        "old": JobStatus.DRAFT.value,
                        "new": JobStatus.OPEN.value,
                    }
                },
            )
        )
        return updated

    async def close_job(self, identity: Identity, job_id: UUID, now: datetime) -> Job:
        """Close an open job: `open → closed`, terminal (`409 job_not_open`)."""
        ensure_can_manage(identity)
        existing = await self._get_manageable(identity, job_id)
        if existing.status is not JobStatus.OPEN:
            raise JobNotOpenError()

        updated = await self._repo.transition_close(identity.tenant_id, job_id, now=now)
        if updated is None:
            raise JobNotOpenError()

        await self._outbox.append(
            events.job_closed(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                job_id=updated.id,
                closed_by=identity.actor_id,
            )
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="job.closed",
                entity_type="job",
                entity_id=updated.id,
                occurred_at=now,
                changes={
                    "status": {
                        "old": JobStatus.OPEN.value,
                        "new": JobStatus.CLOSED.value,
                    }
                },
            )
        )
        return updated

    async def create_candidate(
        self,
        identity: Identity,
        job_id: UUID,
        payload: CandidateCreate,
        now: datetime,
    ) -> Candidate:
        """Add an application to an `open` job — always `applied`."""
        ensure_can_manage(identity)
        job = await self._repo.get_job(identity.tenant_id, job_id)
        if job is None:
            raise JobNotFoundError()
        if job.status is not JobStatus.OPEN:
            raise JobNotOpenError()

        candidate = await self._repo.create_candidate(
            identity.tenant_id,
            uuid7(),
            job_id=job_id,
            first_name=payload.first_name,
            last_name=payload.last_name,
            email=payload.email,
            phone=payload.phone,
            source=payload.source,
            notes=payload.notes,
        )

        await self._outbox.append(
            events.candidate_created(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                candidate_id=candidate.id,
                job_id=candidate.job_id,
                source=cast("events.CandidateSource", candidate.source.value),
            )
        )
        changes: dict[str, Any] = {
            "job_id": {"old": None, "new": str(candidate.job_id)},
            "source": {"old": None, "new": candidate.source.value},
            "status": {"old": None, "new": candidate.status.value},
            # PII: field names only, never the values (`.ai/security.md`).
            "first_name": PII_CHANGED,
            "last_name": PII_CHANGED,
            "email": PII_CHANGED,
        }
        if candidate.phone is not None:
            changes["phone"] = PII_CHANGED
        if candidate.notes is not None:
            changes["notes"] = PII_CHANGED
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="candidate.created",
                entity_type="candidate",
                entity_id=candidate.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return candidate

    async def update_candidate(
        self,
        identity: Identity,
        candidate_id: UUID,
        payload: CandidateUpdate,
        now: datetime,
    ) -> Candidate:
        """Edit contact fields; the job-scoped email uniqueness guard applies."""
        ensure_can_manage(identity)
        existing = await self._manageable_candidate(identity, candidate_id)

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            return existing

        updated = await self._repo.update_candidate(
            identity.tenant_id, candidate_id, now=now, **updates
        )
        if updated is None:
            raise CandidateNotFoundError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="candidate.updated",
                entity_type="candidate",
                entity_id=candidate_id,
                occurred_at=now,
                changes={
                    field: (
                        PII_CHANGED
                        if field in _CANDIDATE_PII_FIELDS
                        else {
                            "old": _value(getattr(existing, field)),
                            "new": _value(getattr(updated, field)),
                        }
                    )
                    for field in updates
                },
            )
        )
        return updated

    async def advance_candidate(
        self, identity: Identity, candidate_id: UUID, now: datetime
    ) -> Candidate:
        """Step exactly one stage forward (`409 candidate_stage_conflict`)."""
        ensure_can_manage(identity)
        existing = await self._manageable_candidate(identity, candidate_id)
        match existing.status:
            case CandidateStatus.APPLIED:
                to_status = CandidateStatus.INTERVIEWING
            case CandidateStatus.INTERVIEWING:
                to_status = CandidateStatus.OFFERED
            case _:
                raise CandidateStageConflictError()

        updated = await self._repo.transition_candidate(
            identity.tenant_id,
            candidate_id,
            from_status=existing.status,
            to_status=to_status,
            now=now,
        )
        if updated is None:
            # A concurrent move won between the read and the conditional update.
            raise CandidateStageConflictError()

        await self._outbox.append(
            events.candidate_advanced(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                candidate_id=updated.id,
                job_id=updated.job_id,
                from_stage=cast("events.CandidateStage", existing.status.value),
                to_stage=cast("events.CandidateStage", to_status.value),
            )
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="candidate.advanced",
                entity_type="candidate",
                entity_id=updated.id,
                occurred_at=now,
                changes={
                    "status": {
                        "old": existing.status.value,
                        "new": to_status.value,
                    }
                },
            )
        )
        return updated

    async def reject_candidate(
        self,
        identity: Identity,
        candidate_id: UUID,
        payload: CandidateDecision,
        now: datetime,
    ) -> Candidate:
        """Terminal reject from any pre-hire stage, recording the source stage."""
        ensure_can_manage(identity)
        existing = await self._manageable_candidate(identity, candidate_id)
        if existing.status not in _CANDIDATE_OPEN:
            raise CandidateNotOpenError()
        rejected_from = CandidateRejectedFrom(existing.status.value)

        updated = await self._repo.transition_candidate(
            identity.tenant_id,
            candidate_id,
            from_status=existing.status,
            to_status=CandidateStatus.REJECTED,
            now=now,
            stage_note=payload.note,
            rejected_from=rejected_from,
        )
        if updated is None:
            # A concurrent decision already moved the row out of an open state.
            raise CandidateNotOpenError()

        await self._outbox.append(
            events.candidate_rejected(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                candidate_id=updated.id,
                job_id=updated.job_id,
                rejected_from=cast("events.CandidateStage", rejected_from.value),
                rejected_by=identity.actor_id,
            )
        )
        changes: dict[str, Any] = {
            "status": {
                "old": existing.status.value,
                "new": CandidateStatus.REJECTED.value,
            },
            "rejected_from": {"old": None, "new": rejected_from.value},
        }
        if payload.note is not None:
            changes["stage_note"] = PII_CHANGED  # PII: never the value
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="candidate.rejected",
                entity_type="candidate",
                entity_id=updated.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return updated

    async def withdraw_candidate(
        self,
        identity: Identity,
        candidate_id: UUID,
        payload: CandidateDecision,
        now: datetime,
    ) -> Candidate:
        """HR records a candidate withdrawal from any pre-hire stage."""
        ensure_can_manage(identity)
        existing = await self._manageable_candidate(identity, candidate_id)
        if existing.status not in _CANDIDATE_OPEN:
            raise CandidateNotOpenError()

        updated = await self._repo.transition_candidate(
            identity.tenant_id,
            candidate_id,
            from_status=existing.status,
            to_status=CandidateStatus.WITHDRAWN,
            now=now,
            stage_note=payload.note,
        )
        if updated is None:
            raise CandidateNotOpenError()

        await self._outbox.append(
            events.candidate_withdrawn(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                candidate_id=updated.id,
                job_id=updated.job_id,
                withdrawn_from=cast("events.CandidateStage", existing.status.value),
            )
        )
        changes: dict[str, Any] = {
            "status": {
                "old": existing.status.value,
                "new": CandidateStatus.WITHDRAWN.value,
            }
        }
        if payload.note is not None:
            changes["stage_note"] = PII_CHANGED  # PII: never the value
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="candidate.withdrawn",
                entity_type="candidate",
                entity_id=updated.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return updated

    async def hire_candidate(
        self,
        identity: Identity,
        candidate_id: UUID,
        payload: HireCandidate,
        now: datetime,
    ) -> CandidateDetail:
        """Hire an `offered` candidate and create the Employee through the seam.

        Guards run in the pinned order: candidate (`404`/`409
        candidate_stage_conflict`), job (`409 job_not_open`), capacity (`409
        job_openings_filled`), then the hire date (`422`). The candidate then
        its job are locked before the capacity count (ADR 0020 decision 4).
        `candidate.hired_at` records the transition instant; the employment
        start date is `payload.hired_at` on the Employee record.
        """
        ensure_can_manage(identity)

        candidate = await self._repo.lock_candidate(identity.tenant_id, candidate_id)
        if candidate is None:
            raise CandidateNotFoundError()
        if candidate.status is not CandidateStatus.OFFERED:
            raise CandidateStageConflictError()

        job = await self._repo.lock_job(identity.tenant_id, candidate.job_id)
        if job is None:  # pragma: no cover — the candidate FK guarantees the job
            raise JobNotFoundError()
        if job.status is not JobStatus.OPEN:
            raise JobNotOpenError()
        hired = await self._repo.count_hired(identity.tenant_id, job.id)
        if hired >= job.openings:
            raise JobOpeningsFilledError()
        if payload.hired_at > now.date():
            raise HireDateInFutureError()

        employee_id = await self._hire.create_employee(
            identity.tenant_id,
            identity.actor_id,
            self._hire_payload(candidate, job, payload),
            now,
        )

        updated = await self._repo.mark_hired(
            identity.tenant_id,
            candidate_id,
            employee_id=employee_id,
            hired_at=now,
            now=now,
        )
        if updated is None:  # pragma: no cover — the FOR UPDATE lock prevents it
            raise CandidateStageConflictError()

        await self._outbox.append(
            events.candidate_hired(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                candidate_id=updated.id,
                job_id=updated.job_id,
                employee_id=employee_id,
                hired_by=identity.actor_id,
            )
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="candidate.hired",
                entity_type="candidate",
                entity_id=updated.id,
                occurred_at=now,
                changes={
                    "status": {
                        "old": CandidateStatus.OFFERED.value,
                        "new": CandidateStatus.HIRED.value,
                    },
                    "employee_id": {"old": None, "new": str(employee_id)},
                    "hired_at": {"old": None, "new": now.isoformat()},
                },
            )
        )
        return self._detail(
            updated,
            await self._repo.list_candidate_interviews(identity.tenant_id, updated.id),
        )

    # --- reads ---------------------------------------------------------------

    async def list_jobs(
        self,
        identity: Identity,
        *,
        status: JobStatus | None,
        q: str | None,
        department_id: UUID | None,
        page: PageParams,
    ) -> JobListPage:
        """Paged jobs scoped to the caller's visibility (## Roles & access)."""
        scope = await self._read_scope(identity)
        filters = JobFilters(
            status=status,
            q=q,
            department_id=department_id,
            visible_job_ids=scope,
        )
        items, total = await self._repo.list_jobs(identity.tenant_id, filters, page)
        return JobListPage(items=list(items), page=page.make_page(total))

    async def get_job(self, identity: Identity, job_id: UUID) -> Job:
        """Job detail; unknown, cross-tenant and out-of-scope ids are `404`."""
        job = await self._repo.get_job(identity.tenant_id, job_id)
        if job is None:
            raise JobNotFoundError()
        scope = await self._read_scope(identity)
        if scope is not None and job.id not in scope:
            raise JobNotFoundError()
        return job

    async def list_candidates(
        self,
        identity: Identity,
        *,
        job_id: UUID | None,
        status: CandidateStatus | None,
        q: str | None,
        page: PageParams,
    ) -> CandidateListPage:
        """Paged candidates scoped to the caller's visibility (## Roles & access)."""
        scope = await self._candidate_scope(identity)
        if job_id is not None:
            # Foreign/unknown filter ids are `404` (pinned query semantics).
            job = await self._repo.get_job(identity.tenant_id, job_id)
            if job is None:
                raise JobNotFoundError()
        filters = CandidateFilters(
            job_id=job_id,
            status=status,
            q=q,
            visible_candidate_ids=scope,
        )
        items, total = await self._repo.list_candidates(
            identity.tenant_id, filters, page
        )
        return CandidateListPage(items=list(items), page=page.make_page(total))

    async def get_candidate(
        self, identity: Identity, candidate_id: UUID
    ) -> CandidateDetail:
        """Candidate detail; unknown, cross-tenant and out-of-scope ids are `404`.

        `interviews` is management-complete and interviewer-scoped: an assigned
        interviewer sees only their own conversations on the candidate, never
        another interviewer's feedback.
        """
        scope = await self._candidate_scope(identity)
        candidate = await self._repo.get_candidate(identity.tenant_id, candidate_id)
        if candidate is None:
            raise CandidateNotFoundError()
        if scope is not None and candidate.id not in scope:
            raise CandidateNotFoundError()
        interviewer_id: UUID | None = None
        if not is_management(identity):
            # The scope above proves the employee link exists; keeping the
            # explicit guard makes the read fail closed if that ever changes.
            if identity.employee_id is None:
                raise CandidateNotFoundError()
            interviewer_id = identity.employee_id
        interviews = await self._repo.list_candidate_interviews(
            identity.tenant_id, candidate.id, interviewer_id=interviewer_id
        )
        return self._detail(candidate, interviews)

    async def list_interviews(
        self,
        identity: Identity,
        *,
        candidate_id: UUID | None,
        job_id: UUID | None,
        status: InterviewStatus | None,
        mine: bool,
        interviewer_id: UUID | None,
        scheduled_from: date | None,
        scheduled_to: date | None,
        page: PageParams,
    ) -> InterviewListPage:
        """Paged interviews with the pinned scope and filter semantics.

        Management may filter by any `interviewer_id`; a non-management caller
        is always forced to their own employee id and may not pass the filter
        (`403 recruitment_denied`). `mine=1` restricts any caller to their own
        assignments (empty when the account has no employee link). Foreign
        filter ids are `404` with the entity's code.
        """
        ensure_has_standing(identity)
        management = is_management(identity)

        if not management and interviewer_id is not None:
            raise RecruitmentDeniedError()

        if candidate_id is not None:
            candidate = await self._repo.get_candidate(identity.tenant_id, candidate_id)
            if candidate is None:
                raise CandidateNotFoundError()
            if not management:
                scope = await self._candidate_scope(identity)
                if scope is not None and candidate.id not in scope:
                    raise CandidateNotFoundError()

        if job_id is not None:
            job = await self._repo.get_job(identity.tenant_id, job_id)
            if job is None:
                raise JobNotFoundError()
            if not management:
                scope = await self._read_scope(identity)
                if scope is not None and job.id not in scope:
                    raise JobNotFoundError()

        effective_interviewer: UUID | None = None
        empty_scope = False
        if management:
            if mine:
                effective_interviewer = identity.employee_id
                empty_scope = identity.employee_id is None
            else:
                effective_interviewer = interviewer_id
                if interviewer_id is not None:
                    facts = await self._repo.interviewer_facts(
                        identity.tenant_id, interviewer_id
                    )
                    if facts is None:
                        raise InterviewerNotFoundError()
        elif identity.employee_id is None:
            empty_scope = True
        else:
            effective_interviewer = identity.employee_id

        if empty_scope:
            return InterviewListPage(items=[], page=page.make_page(0))

        items, total = await self._repo.list_interviews(
            identity.tenant_id,
            InterviewFilters(
                candidate_id=candidate_id,
                job_id=job_id,
                status=status,
                interviewer_id=effective_interviewer,
                scheduled_from=scheduled_from,
                scheduled_to=scheduled_to,
            ),
            page,
        )
        return InterviewListPage(items=list(items), page=page.make_page(total))

    async def schedule_interview(
        self,
        identity: Identity,
        candidate_id: UUID,
        payload: InterviewSchedule,
        now: datetime,
    ) -> Interview:
        """Schedule a conversation on an `interviewing` candidate.

        Management-only. The candidate stage and the interviewer's active
        status are checked before the row is written; scheduling is the only
        interview transition that emits `interview.scheduled`.
        """
        ensure_can_manage(identity)
        # `FOR UPDATE` serializes this stage check with a concurrent
        # advance/reject/withdraw so the insert cannot land on a candidate that
        # left `interviewing` between the read and the write.
        candidate = await self._repo.lock_candidate(identity.tenant_id, candidate_id)
        if candidate is None:
            raise CandidateNotFoundError()
        if candidate.status is not CandidateStatus.INTERVIEWING:
            raise CandidateStageConflictError()
        await self._ensure_active_interviewer(
            identity.tenant_id, payload.interviewer_id
        )

        interview = await self._repo.create_interview(
            identity.tenant_id,
            uuid7(),
            candidate_id=candidate.id,
            job_id=candidate.job_id,
            interviewer_id=payload.interviewer_id,
            scheduled_at=payload.scheduled_at,
            notes=payload.notes,
        )

        await self._outbox.append(
            events.interview_scheduled(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                interview_id=interview.id,
                candidate_id=interview.candidate_id,
                job_id=interview.job_id,
                interviewer_id=interview.interviewer_id,
            )
        )
        changes: dict[str, Any] = {
            "candidate_id": {"old": None, "new": str(interview.candidate_id)},
            "job_id": {"old": None, "new": str(interview.job_id)},
            "interviewer_id": {"old": None, "new": str(interview.interviewer_id)},
            "scheduled_at": {"old": None, "new": interview.scheduled_at.isoformat()},
            "status": {"old": None, "new": InterviewStatus.SCHEDULED.value},
        }
        if interview.notes is not None:
            changes["notes"] = PII_CHANGED  # PII: never the value
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="interview.scheduled",
                entity_type="interview",
                entity_id=interview.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return interview

    async def update_interview(
        self,
        identity: Identity,
        interview_id: UUID,
        payload: InterviewUpdate,
        now: datetime,
    ) -> Interview:
        """Reschedule / re-assign / re-note while `scheduled` (management-only).

        A reassignment re-runs the active-interviewer check. No event: the
        catalog has no `interview.updated` fact; the audit entry records it.
        """
        ensure_can_manage(identity)
        existing = await self._repo.get_interview(identity.tenant_id, interview_id)
        if existing is None:
            raise InterviewNotFoundError()
        if existing.status is not InterviewStatus.SCHEDULED:
            raise InterviewNotScheduledError()

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            return existing
        if "interviewer_id" in updates:
            await self._ensure_active_interviewer(
                identity.tenant_id, updates["interviewer_id"]
            )

        updated = await self._repo.update_interview(
            identity.tenant_id, interview_id, now=now, **updates
        )
        if updated is None:
            # A concurrent completion/cancellation won the row.
            raise InterviewNotScheduledError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="interview.updated",
                entity_type="interview",
                entity_id=interview_id,
                occurred_at=now,
                changes={
                    field: (
                        PII_CHANGED
                        if field in {"notes", "feedback_notes"}
                        else {
                            "old": _value(getattr(existing, field)),
                            "new": _value(getattr(updated, field)),
                        }
                    )
                    for field in updates
                },
            )
        )
        return updated

    async def complete_interview(
        self,
        identity: Identity,
        interview_id: UUID,
        payload: InterviewFeedback,
        now: datetime,
    ) -> Interview:
        """The assigned interviewer files feedback: `scheduled → completed`.

        Only the assigned interviewer may complete; a management caller who is
        not assigned gets `403`, a non-management caller who is not assigned
        gets `404` (existence is not leaked). The candidate's stage is never
        changed here.
        """
        ensure_has_standing(identity)
        existing = await self._repo.get_interview(identity.tenant_id, interview_id)
        if existing is None:
            raise InterviewNotFoundError()
        interviewer_id = identity.employee_id
        if interviewer_id is None or interviewer_id != existing.interviewer_id:
            if is_management(identity):
                raise RecruitmentDeniedError()
            raise InterviewNotFoundError()
        if existing.status is not InterviewStatus.SCHEDULED:
            raise InterviewNotScheduledError()

        updated = await self._repo.complete_interview(
            identity.tenant_id,
            interview_id,
            interviewer_id=interviewer_id,
            rating=payload.rating,
            feedback_notes=payload.notes,
            now=now,
        )
        if updated is None:
            # The guarded update lost: a concurrent completion/cancellation won,
            # or HR reassigned the interview after the permission read. Re-read
            # so the caller gets the pinned code for the actual outcome rather
            # than a blanket wrong-state conflict.
            latest = await self._repo.get_interview(identity.tenant_id, interview_id)
            if latest is None:
                raise InterviewNotFoundError()
            if latest.interviewer_id != interviewer_id:
                if is_management(identity):
                    raise RecruitmentDeniedError()
                raise InterviewNotFoundError()
            raise InterviewNotScheduledError()

        await self._outbox.append(
            events.interview_completed(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                interview_id=updated.id,
                candidate_id=updated.candidate_id,
                job_id=updated.job_id,
                interviewer_id=updated.interviewer_id,
                completed_by=identity.actor_id,
            )
        )
        changes: dict[str, Any] = {
            "status": {
                "old": InterviewStatus.SCHEDULED.value,
                "new": InterviewStatus.COMPLETED.value,
            },
            "rating": {"old": None, "new": payload.rating},
            "completed_at": {"old": None, "new": now.isoformat()},
        }
        if payload.notes is not None:
            changes["feedback_notes"] = PII_CHANGED  # PII: never the value
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="interview.completed",
                entity_type="interview",
                entity_id=updated.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return updated

    async def cancel_interview(
        self,
        identity: Identity,
        interview_id: UUID,
        payload: CandidateDecision,
        now: datetime,
    ) -> Interview:
        """Cancel a `scheduled` interview (management-only), terminal.

        The optional note is PII: it is stored on the interview row and
        recorded in the audit trail as `"changed"`, never in the event.
        """
        ensure_can_manage(identity)
        existing = await self._repo.get_interview(identity.tenant_id, interview_id)
        if existing is None:
            raise InterviewNotFoundError()
        if existing.status is not InterviewStatus.SCHEDULED:
            raise InterviewNotScheduledError()

        updated = await self._repo.cancel_interview(
            identity.tenant_id, interview_id, note=payload.note, now=now
        )
        if updated is None:
            raise InterviewNotScheduledError()

        await self._outbox.append(
            events.interview_cancelled(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                interview_id=updated.id,
                candidate_id=updated.candidate_id,
                job_id=updated.job_id,
                cancelled_by=identity.actor_id,
            )
        )
        changes: dict[str, Any] = {
            "status": {
                "old": InterviewStatus.SCHEDULED.value,
                "new": InterviewStatus.CANCELLED.value,
            },
            "cancelled_at": {"old": None, "new": now.isoformat()},
        }
        if payload.note is not None:
            changes["notes"] = PII_CHANGED  # PII: never the value
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="interview.cancelled",
                entity_type="interview",
                entity_id=updated.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return updated

    # --- internals -----------------------------------------------------------

    async def _get_manageable(self, identity: Identity, job_id: UUID) -> Job:
        """Fetch a job for a management mutation; missing/foreign is `404`."""
        job = await self._repo.get_job(identity.tenant_id, job_id)
        if job is None:
            raise JobNotFoundError()
        return job

    async def _manageable_candidate(
        self, identity: Identity, candidate_id: UUID
    ) -> Candidate:
        """Fetch a candidate for a management mutation; missing/foreign is `404`."""
        candidate = await self._repo.get_candidate(identity.tenant_id, candidate_id)
        if candidate is None:
            raise CandidateNotFoundError()
        return candidate

    async def _candidate_scope(self, identity: Identity) -> Sequence[UUID] | None:
        """Effective candidate scope; `None` means unscoped (management)."""
        ensure_has_standing(identity)
        if is_management(identity):
            return None
        if identity.employee_id is None:
            return []
        return await self._repo.visible_candidate_ids(
            identity.tenant_id, identity.employee_id
        )

    async def _read_scope(self, identity: Identity) -> Sequence[UUID] | None:
        """Effective job scope; `None` means unscoped (hr/admin/super_admin)."""
        ensure_has_standing(identity)
        if is_management(identity):
            return None
        if identity.employee_id is None:
            return []
        return await self._repo.visible_job_ids(
            identity.tenant_id, identity.employee_id
        )

    async def _ensure_org_refs(
        self,
        identity: Identity,
        department_id: UUID,
        position_id: UUID,
        location_id: UUID,
    ) -> None:
        """Refs must exist in the tenant (Organizations 404s propagate)."""
        await self._departments.get(identity, department_id)
        await self._positions.get(identity, position_id)
        await self._locations.get(identity, location_id)

    async def _ensure_active_interviewer(
        self, tenant_id: UUID, interviewer_id: UUID
    ) -> None:
        """Unknown/foreign is `404`, a terminated employee is `409`."""
        facts = await self._repo.interviewer_facts(tenant_id, interviewer_id)
        if facts is None:
            raise InterviewerNotFoundError()
        if not facts.active:
            raise InterviewerNotActiveError()

    @staticmethod
    def _detail(
        candidate: Candidate, interviews: Sequence[Interview]
    ) -> CandidateDetail:
        """One `CandidateDetail` assembly for reads and the hire response.

        T5 fills `interviews` (management: all; assigned interviewer: own), so
        the hire endpoint cannot drift from `getCandidate`.
        """
        return CandidateDetail(**candidate.model_dump(), interviews=list(interviews))

    @staticmethod
    def _hire_payload(
        candidate: Candidate, job: Job, payload: HireCandidate
    ) -> EmployeeHirePayload:
        """Effective hire terms: candidate record + overrides + the job's refs.

        `None` overrides fall back to the candidate's recorded contact data
        (the pinned "defaulting to the candidate's record" semantics).
        """
        return EmployeeHirePayload(
            first_name=(
                candidate.first_name
                if payload.first_name is None
                else payload.first_name
            ),
            last_name=(
                candidate.last_name if payload.last_name is None else payload.last_name
            ),
            email=candidate.email if payload.email is None else payload.email,
            phone=candidate.phone if payload.phone is None else payload.phone,
            department_id=job.department_id,
            position_id=job.position_id,
            location_id=job.location_id,
            manager_id=payload.manager_id,
            employment_type=payload.employment_type.value,
            hired_at=payload.hired_at,
            employee_code=payload.employee_code,
        )
