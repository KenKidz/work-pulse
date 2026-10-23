"""In-memory fakes for the Recruitment jobs unit tests (T2).

No I/O anywhere (`.ai/testing.md` — these tests pass with `postgres-test`
stopped). The fake honors tenant scope, the pinned job filters, the conditional
transition guards and the interview-derived visibility map, so the service is
exercised against the same rules as the Postgres adapter without simulating
transactions.

Every transition returns `None` when its observed source state no longer
matches — exactly the backstop the service maps to the pinned wrong-state
error. `lose_next_transition` simulates the concurrent-editor race: the next
conditional update loses even though the service's pre-check passed.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.domains.recruitment.repository import JobFilters
from app.domains.recruitment.schemas import Job, JobStatus
from app.shared.errors import WorkPulseError
from app.shared.identity import Identity
from app.shared.pagination import PageParams

CREATED_AT = datetime(2026, 9, 1, tzinfo=UTC)
_EDITABLE = (JobStatus.DRAFT, JobStatus.OPEN)


class FakeRecruitmentRepo:
    """Tenant-scoped in-memory implementation of `RecruitmentRepository`."""

    def __init__(self) -> None:
        self.jobs: dict[tuple[UUID, UUID], Job] = {}
        # (tenant, interviewer employee) → the job ids their interviews expose.
        self.visible_jobs: dict[tuple[UUID, UUID], set[UUID]] = {}
        self.calls: list[str] = []
        self.lose_next_transition = False
        self._created = 0

    def _race_lost(self) -> bool:
        """Consume a one-shot simulated lost race (the UPDATE matched 0 rows)."""
        if not self.lose_next_transition:
            return False
        self.lose_next_transition = False
        return True

    def _stamp(self) -> datetime:
        self._created += 1
        return CREATED_AT + timedelta(seconds=self._created)

    # --- visibility ----------------------------------------------------------

    async def visible_job_ids(
        self, tenant_id: UUID, employee_id: UUID
    ) -> Sequence[UUID]:
        self.calls.append("visible_job_ids")
        return sorted(self.visible_jobs.get((tenant_id, employee_id), set()))

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
