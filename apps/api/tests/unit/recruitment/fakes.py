"""In-memory fakes for the Recruitment unit tests (T2–T5).

No I/O anywhere (`.ai/testing.md` — these tests pass with `postgres-test`
stopped). The fake honors tenant scope, the pinned job/candidate/interview
filters, the conditional transition guards and the interview-derived visibility
maps, so the service is exercised against the same rules as the Postgres
adapter without simulating transactions.

Every transition returns `None` when its observed source state no longer
matches — exactly the backstop the service maps to the pinned wrong-state
error. `lose_next_transition`, `lose_next_candidate_transition` and
`lose_next_interview_transition` simulate the concurrent-editor race: the next
conditional update loses even though the service's pre-check passed. Candidate
creates/edits enforce the `(tenant_id, job_id, email)` uniqueness the real
adapter backs with an index; `interviewers` is the tenant-scoped
active/terminated directory `interviewer_facts` reads.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from uuid_utils.compat import uuid7

from app.domains.recruitment.exceptions import CandidateAlreadyAppliedError
from app.domains.recruitment.repository import (
    CandidateFilters,
    EmployeeHirePayload,
    InterviewerFacts,
    InterviewFilters,
    JobFilters,
)
from app.domains.recruitment.schemas import (
    Candidate,
    CandidateRejectedFrom,
    CandidateSource,
    CandidateStatus,
    Interview,
    InterviewStatus,
    Job,
    JobStatus,
)
from app.shared.errors import WorkPulseError
from app.shared.identity import Identity
from app.shared.pagination import PageParams

CREATED_AT = datetime(2026, 9, 1, tzinfo=UTC)
_EDITABLE = (JobStatus.DRAFT, JobStatus.OPEN)


class FakeRecruitmentRepo:
    """Tenant-scoped in-memory implementation of `RecruitmentRepository`."""

    def __init__(self) -> None:
        self.jobs: dict[tuple[UUID, UUID], Job] = {}
        self.candidates: dict[tuple[UUID, UUID], Candidate] = {}
        self.interviews: dict[tuple[UUID, UUID], Interview] = {}
        # (tenant, employee) → active? The `interviewer_facts` directory.
        self.interviewers: dict[tuple[UUID, UUID], bool] = {}
        # (tenant, interviewer employee) → the ids their interviews expose.
        self.visible_jobs: dict[tuple[UUID, UUID], set[UUID]] = {}
        self.visible_candidates: dict[tuple[UUID, UUID], set[UUID]] = {}
        self.calls: list[str] = []
        self.lose_next_transition = False
        self.lose_next_candidate_transition = False
        self.lose_next_hired_transition = False
        self.lose_next_interview_transition = False
        # Simulates a concurrent HR reassignment landing after the service's
        # permission read but before its conditional completion update.
        self.reassign_next_complete_to: UUID | None = None
        self._created = 0

    def _race_lost(self) -> bool:
        """Consume a one-shot simulated lost race (the UPDATE matched 0 rows)."""
        if not self.lose_next_transition:
            return False
        self.lose_next_transition = False
        return True

    def _candidate_race_lost(self) -> bool:
        """Consume a one-shot simulated lost candidate race."""
        if not self.lose_next_candidate_transition:
            return False
        self.lose_next_candidate_transition = False
        return True

    def _hired_race_lost(self) -> bool:
        """Consume a one-shot simulated lost hire transition."""
        if not self.lose_next_hired_transition:
            return False
        self.lose_next_hired_transition = False
        return True

    def _interview_race_lost(self) -> bool:
        """Consume a one-shot simulated lost interview transition."""
        if not self.lose_next_interview_transition:
            return False
        self.lose_next_interview_transition = False
        return True

    def _email_taken(
        self, tenant_id: UUID, job_id: UUID, email: str, *, exclude_id: UUID | None
    ) -> bool:
        """The `(tenant, job, email)` unique guard, exact-match (risk 5)."""
        return any(
            row.tenant_id == tenant_id
            and row.job_id == job_id
            and row.email == email
            and row.id != exclude_id
            for row in self.candidates.values()
        )

    def _stamp(self) -> datetime:
        self._created += 1
        return CREATED_AT + timedelta(seconds=self._created)

    # --- visibility ----------------------------------------------------------

    async def visible_job_ids(
        self, tenant_id: UUID, employee_id: UUID
    ) -> Sequence[UUID]:
        self.calls.append("visible_job_ids")
        return sorted(self.visible_jobs.get((tenant_id, employee_id), set()))

    async def visible_candidate_ids(
        self, tenant_id: UUID, employee_id: UUID
    ) -> Sequence[UUID]:
        self.calls.append("visible_candidate_ids")
        return sorted(self.visible_candidates.get((tenant_id, employee_id), set()))

    # --- jobs ----------------------------------------------------------------

    async def create_job(
        self,
        tenant_id: UUID,
        job_id: UUID,
        *,
        title: str,
        description: str | None,
        department_id: UUID,
        position_id: UUID,
        location_id: UUID,
        openings: int,
        created_by: UUID,
    ) -> Job:
        self.calls.append("create_job")
        created_at = self._stamp()
        job = Job(
            id=job_id,
            tenant_id=tenant_id,
            title=title,
            description=description,
            department_id=department_id,
            position_id=position_id,
            location_id=location_id,
            openings=openings,
            status=JobStatus.DRAFT,
            created_by=created_by,
            created_at=created_at,
            updated_at=created_at,
        )
        self.jobs[(tenant_id, job_id)] = job
        return job

    async def get_job(self, tenant_id: UUID, job_id: UUID) -> Job | None:
        return self.jobs.get((tenant_id, job_id))

    async def lock_job(self, tenant_id: UUID, job_id: UUID) -> Job | None:
        self.calls.append("lock_job")
        return self.jobs.get((tenant_id, job_id))

    async def list_jobs(
        self, tenant_id: UUID, filters: JobFilters, page: PageParams
    ) -> tuple[Sequence[Job], int]:
        if filters.visible_job_ids is not None and not filters.visible_job_ids:
            return [], 0
        allowed = (
            None if filters.visible_job_ids is None else set(filters.visible_job_ids)
        )
        term = filters.q.casefold() if filters.q else None
        rows = [
            row
            for (row_tenant, _), row in self.jobs.items()
            if row_tenant == tenant_id
            and (allowed is None or row.id in allowed)
            and (filters.status is None or row.status is filters.status)
            and (
                filters.department_id is None
                or row.department_id == filters.department_id
            )
            and (term is None or term in row.title.casefold())
        ]
        rows.sort(key=lambda row: (row.created_at, row.id), reverse=True)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def update_job(
        self,
        tenant_id: UUID,
        job_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Job | None:
        self.calls.append("update_job")
        if self._race_lost():
            return None
        key = (tenant_id, job_id)
        row = self.jobs.get(key)
        if row is None or row.status not in _EDITABLE:
            return None
        updated = row.model_copy(update={**changes, "updated_at": now})
        self.jobs[key] = updated
        return updated

    async def transition_open(
        self, tenant_id: UUID, job_id: UUID, *, now: datetime
    ) -> Job | None:
        self.calls.append("transition_open")
        if self._race_lost():
            return None
        key = (tenant_id, job_id)
        row = self.jobs.get(key)
        if row is None or row.status is not JobStatus.DRAFT:
            return None
        updated = row.model_copy(
            update={
                "status": JobStatus.OPEN,
                "opened_at": now,
                "updated_at": now,
            }
        )
        self.jobs[key] = updated
        return updated

    async def transition_close(
        self, tenant_id: UUID, job_id: UUID, *, now: datetime
    ) -> Job | None:
        self.calls.append("transition_close")
        if self._race_lost():
            return None
        key = (tenant_id, job_id)
        row = self.jobs.get(key)
        if row is None or row.status is not JobStatus.OPEN:
            return None
        updated = row.model_copy(
            update={
                "status": JobStatus.CLOSED,
                "closed_at": now,
                "updated_at": now,
            }
        )
        self.jobs[key] = updated
        return updated

    # --- candidates ----------------------------------------------------------

    async def create_candidate(
        self,
        tenant_id: UUID,
        candidate_id: UUID,
        *,
        job_id: UUID,
        first_name: str,
        last_name: str,
        email: str,
        phone: str | None,
        source: CandidateSource,
        notes: str | None,
    ) -> Candidate:
        self.calls.append("create_candidate")
        if self._email_taken(tenant_id, job_id, email, exclude_id=None):
            raise CandidateAlreadyAppliedError()
        created_at = self._stamp()
        candidate = Candidate(
            id=candidate_id,
            tenant_id=tenant_id,
            job_id=job_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            source=source,
            notes=notes,
            status=CandidateStatus.APPLIED,
            created_at=created_at,
            updated_at=created_at,
        )
        self.candidates[(tenant_id, candidate_id)] = candidate
        return candidate

    async def get_candidate(
        self, tenant_id: UUID, candidate_id: UUID
    ) -> Candidate | None:
        return self.candidates.get((tenant_id, candidate_id))

    async def lock_candidate(
        self, tenant_id: UUID, candidate_id: UUID
    ) -> Candidate | None:
        self.calls.append("lock_candidate")
        return self.candidates.get((tenant_id, candidate_id))

    async def list_candidates(
        self, tenant_id: UUID, filters: CandidateFilters, page: PageParams
    ) -> tuple[Sequence[Candidate], int]:
        if (
            filters.visible_candidate_ids is not None
            and not filters.visible_candidate_ids
        ):
            return [], 0
        allowed = (
            None
            if filters.visible_candidate_ids is None
            else set(filters.visible_candidate_ids)
        )
        term = filters.q.casefold() if filters.q else None
        rows = [
            row
            for (row_tenant, _), row in self.candidates.items()
            if row_tenant == tenant_id
            and (allowed is None or row.id in allowed)
            and (filters.job_id is None or row.job_id == filters.job_id)
            and (filters.status is None or row.status is filters.status)
            and (
                term is None
                or term in row.first_name.casefold()
                or term in row.last_name.casefold()
            )
        ]
        rows.sort(key=lambda row: (row.created_at, row.id), reverse=True)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def update_candidate(
        self,
        tenant_id: UUID,
        candidate_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Candidate | None:
        self.calls.append("update_candidate")
        key = (tenant_id, candidate_id)
        row = self.candidates.get(key)
        if row is None:
            return None
        if "email" in changes and self._email_taken(
            tenant_id, row.job_id, changes["email"], exclude_id=candidate_id
        ):
            raise CandidateAlreadyAppliedError()
        updated = row.model_copy(update={**changes, "updated_at": now})
        self.candidates[key] = updated
        return updated

    async def transition_candidate(
        self,
        tenant_id: UUID,
        candidate_id: UUID,
        *,
        from_status: CandidateStatus,
        to_status: CandidateStatus,
        now: datetime,
        stage_note: str | None = None,
        rejected_from: CandidateRejectedFrom | None = None,
    ) -> Candidate | None:
        self.calls.append("transition_candidate")
        if self._candidate_race_lost():
            return None
        key = (tenant_id, candidate_id)
        row = self.candidates.get(key)
        if row is None or row.status is not from_status:
            return None
        changes: dict[str, Any] = {"status": to_status, "updated_at": now}
        if to_status in (CandidateStatus.REJECTED, CandidateStatus.WITHDRAWN):
            changes["stage_note"] = stage_note
        if rejected_from is not None:
            changes["rejected_from"] = rejected_from
        updated = row.model_copy(update=changes)
        self.candidates[key] = updated
        return updated

    async def count_hired(self, tenant_id: UUID, job_id: UUID) -> int:
        self.calls.append("count_hired")
        return sum(
            1
            for (row_tenant, _), row in self.candidates.items()
            if row_tenant == tenant_id
            and row.job_id == job_id
            and row.status is CandidateStatus.HIRED
        )

    async def mark_hired(
        self,
        tenant_id: UUID,
        candidate_id: UUID,
        *,
        employee_id: UUID,
        hired_at: datetime,
        now: datetime,
    ) -> Candidate | None:
        self.calls.append("mark_hired")
        if self._hired_race_lost():
            return None
        key = (tenant_id, candidate_id)
        row = self.candidates.get(key)
        if row is None or row.status is not CandidateStatus.OFFERED:
            return None
        updated = row.model_copy(
            update={
                "status": CandidateStatus.HIRED,
                "employee_id": employee_id,
                "hired_at": hired_at,
                "updated_at": now,
            }
        )
        self.candidates[key] = updated
        return updated

    # --- interviews ----------------------------------------------------------

    async def interviewer_facts(
        self, tenant_id: UUID, employee_id: UUID
    ) -> InterviewerFacts | None:
        self.calls.append("interviewer_facts")
        active = self.interviewers.get((tenant_id, employee_id))
        if active is None:
            return None
        return InterviewerFacts(active=active)

    async def create_interview(
        self,
        tenant_id: UUID,
        interview_id: UUID,
        *,
        candidate_id: UUID,
        job_id: UUID,
        interviewer_id: UUID,
        scheduled_at: datetime,
        notes: str | None,
    ) -> Interview:
        self.calls.append("create_interview")
        created_at = self._stamp()
        interview = Interview(
            id=interview_id,
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            job_id=job_id,
            interviewer_id=interviewer_id,
            scheduled_at=scheduled_at,
            notes=notes,
            status=InterviewStatus.SCHEDULED,
            created_at=created_at,
            updated_at=created_at,
        )
        self.interviews[(tenant_id, interview_id)] = interview
        return interview

    async def get_interview(
        self, tenant_id: UUID, interview_id: UUID
    ) -> Interview | None:
        return self.interviews.get((tenant_id, interview_id))

    async def list_interviews(
        self, tenant_id: UUID, filters: InterviewFilters, page: PageParams
    ) -> tuple[Sequence[Interview], int]:
        rows = [
            row
            for (row_tenant, _), row in self.interviews.items()
            if row_tenant == tenant_id
            and (
                filters.candidate_id is None or row.candidate_id == filters.candidate_id
            )
            and (filters.job_id is None or row.job_id == filters.job_id)
            and (filters.status is None or row.status is filters.status)
            and (
                filters.interviewer_id is None
                or row.interviewer_id == filters.interviewer_id
            )
            and (
                filters.scheduled_from is None
                or row.scheduled_at.astimezone(UTC).date() >= filters.scheduled_from
            )
            and (
                filters.scheduled_to is None
                or row.scheduled_at.astimezone(UTC).date() <= filters.scheduled_to
            )
        ]
        rows.sort(key=lambda row: (row.scheduled_at, row.id), reverse=True)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def list_candidate_interviews(
        self,
        tenant_id: UUID,
        candidate_id: UUID,
        *,
        interviewer_id: UUID | None = None,
    ) -> Sequence[Interview]:
        rows = [
            row
            for (row_tenant, _), row in self.interviews.items()
            if row_tenant == tenant_id
            and row.candidate_id == candidate_id
            and (interviewer_id is None or row.interviewer_id == interviewer_id)
        ]
        rows.sort(key=lambda row: (row.scheduled_at, row.id), reverse=True)
        return rows

    async def update_interview(
        self,
        tenant_id: UUID,
        interview_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Interview | None:
        self.calls.append("update_interview")
        if self._interview_race_lost():
            return None
        key = (tenant_id, interview_id)
        row = self.interviews.get(key)
        if row is None or row.status is not InterviewStatus.SCHEDULED:
            return None
        updated = row.model_copy(update={**changes, "updated_at": now})
        self.interviews[key] = updated
        return updated

    async def complete_interview(
        self,
        tenant_id: UUID,
        interview_id: UUID,
        *,
        interviewer_id: UUID,
        rating: int,
        feedback_notes: str | None,
        now: datetime,
    ) -> Interview | None:
        self.calls.append("complete_interview")
        if self._interview_race_lost():
            return None
        key = (tenant_id, interview_id)
        row = self.interviews.get(key)
        if self.reassign_next_complete_to is not None and row is not None:
            # The concurrent writer landed first: change the assignment, then
            # the guarded update matches nothing.
            self.interviews[key] = row.model_copy(
                update={"interviewer_id": self.reassign_next_complete_to}
            )
            self.reassign_next_complete_to = None
            return None
        if (
            row is None
            or row.status is not InterviewStatus.SCHEDULED
            or row.interviewer_id != interviewer_id
        ):
            return None
        updated = row.model_copy(
            update={
                "status": InterviewStatus.COMPLETED,
                "rating": rating,
                "feedback_notes": feedback_notes,
                "completed_at": now,
                "updated_at": now,
            }
        )
        self.interviews[key] = updated
        return updated

    async def cancel_interview(
        self,
        tenant_id: UUID,
        interview_id: UUID,
        *,
        note: str | None,
        now: datetime,
    ) -> Interview | None:
        self.calls.append("cancel_interview")
        if self._interview_race_lost():
            return None
        key = (tenant_id, interview_id)
        row = self.interviews.get(key)
        if row is None or row.status is not InterviewStatus.SCHEDULED:
            return None
        changes: dict[str, Any] = {
            "status": InterviewStatus.CANCELLED,
            "cancelled_at": now,
            "updated_at": now,
        }
        if note is not None:
            changes["notes"] = note
        updated = row.model_copy(update=changes)
        self.interviews[key] = updated
        return updated


class FakeEmployeeHirePort:
    """Recording `EmployeeHirePort` fake with a settable seam failure.

    On success it returns `employee_id`; setting `error` makes the next and
    every later call raise it unchanged (the service must propagate seam
    errors and leave the candidate untouched).
    """

    def __init__(self) -> None:
        self.employee_id: UUID = uuid7()
        self.error: WorkPulseError | None = None
        self.calls: list[tuple[UUID, UUID, EmployeeHirePayload, datetime]] = []

    async def create_employee(
        self,
        tenant_id: UUID,
        actor_id: UUID,
        payload: EmployeeHirePayload,
        now: datetime,
    ) -> UUID:
        self.calls.append((tenant_id, actor_id, payload, now))
        if self.error is not None:
            raise self.error
        return self.employee_id


class FakeOrgRefs:
    """Organizations reader fake — a set of known ids plus the error to raise."""

    def __init__(self, error: type[WorkPulseError]) -> None:
        self.error = error
        self.ids: set[UUID] = set()
        self.calls: list[UUID] = []

    async def get(self, identity: Identity, reference_id: UUID) -> object:
        self.calls.append(reference_id)
        if reference_id not in self.ids:
            raise self.error()
        return object()
