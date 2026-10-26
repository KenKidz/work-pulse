"""Data-access abstractions for the Recruitment domain (T2–T4).

Protocols only — the domain never imports infrastructure. Every method takes
`tenant_id` explicitly: tenant scoping is enforced in repository
implementations (`WHERE tenant_id = :tenant`), never in routers or services.
Repositories speak the pinned wire schemas (`schemas.py`); ORM translation
happens inside the Postgres adapter.

Job and candidate state changes are conditional updates on the observed source
status: `None` means the guard lost a concurrent race or the row is not in the
tenant, and the service maps that to the pinned wrong-state error (never a
silent overwrite).

`JobFilters.visible_job_ids` / `CandidateFilters.visible_candidate_ids` carry
the *effective* scope: the service resolves a manager's interview-derived
visibility before calling the list. `None` means "no scoping" (HR/Admin/Super
Admin); an empty sequence matches nothing. The visibility reads are the
interview-assignment join (`visible_job_ids` / `visible_candidate_ids`).

The candidate create/update paths map the `(tenant_id, job_id, email)` unique
violation to `409 candidate_already_applied` inside the adapter — the service
never pre-checks for a race it cannot win (risk 7 in the spec).

The hire flow (T4, ADR 0020) locks the candidate and then its job before
counting filled openings, so two hires cannot both claim the last one
(`lock_candidate` → `lock_job` → `count_hired`). `mark_hired` is the
conditional `offered → hired` flip; `EmployeeHirePort` is the recruitment-owned
write seam to Employees, implemented in `app.infrastructure.postgres` so this
module imports neither Employees nor infrastructure.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.domains.recruitment.schemas import (
    Candidate,
    CandidateRejectedFrom,
    CandidateSource,
    CandidateStatus,
    Job,
    JobStatus,
)
from app.shared.pagination import PageParams


class JobFilters(BaseModel):
    """Effective job-list filters (visibility already resolved)."""

    status: JobStatus | None = None
    q: str | None = None
    department_id: UUID | None = None
    visible_job_ids: Sequence[UUID] | None = None


class CandidateFilters(BaseModel):
    """Effective candidate-list filters (visibility already resolved)."""

    job_id: UUID | None = None
    status: CandidateStatus | None = None
    q: str | None = None
    visible_candidate_ids: Sequence[UUID] | None = None


@dataclass(frozen=True, slots=True)
class EmployeeHirePayload:
    """The effective `CreateEmployee` fields the hire seam applies (ADR 0020).

    Recruitment-owned by design: no Employees type is imported here. The
    service resolves candidate overrides, the job's organization refs and the
    hire terms into these values; the adapter maps them to the Employees
    `CreateEmployee` on the caller's session.
    """

    first_name: str
    last_name: str
    email: str
    phone: str | None
    department_id: UUID
    position_id: UUID
    location_id: UUID
    manager_id: UUID | None
    employment_type: str
    hired_at: date
    employee_code: str | None


class EmployeeHirePort(Protocol):
    """Write seam Recruitment → Employees (ADR 0020).

    `create_employee` runs in the caller's transaction and returns the created
    employee's id. Seam errors propagate unchanged (email/code taken, invalid
    manager, unknown or archived org refs), and any failure rolls the candidate
    flip back with it.
    """

    async def create_employee(
        self,
        tenant_id: UUID,
        actor_id: UUID,
        payload: EmployeeHirePayload,
        now: datetime,
    ) -> UUID:
        """Create the employee through the Employees service; return its id."""
        ...


class RecruitmentRepository(Protocol):
    # --- visibility ---------------------------------------------------------

    async def visible_job_ids(
        self, tenant_id: UUID, employee_id: UUID
    ) -> Sequence[UUID]:
        """Jobs the employee has an interview on — the manager's read scope."""
        ...

    # --- jobs ---------------------------------------------------------------

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
        """Insert the job in `draft`; returns the wire shape."""
        ...

    async def get_job(self, tenant_id: UUID, job_id: UUID) -> Job | None:
        """Tenant-scoped detail; `None` for a missing or foreign id."""
        ...

    async def lock_job(self, tenant_id: UUID, job_id: UUID) -> Job | None:
        """Read the job under `FOR UPDATE` for the hire capacity decision.

        Holding the lock until the request transaction ends serializes two
        hires competing for the same opening (ADR 0020 decision 4).
        """
        ...

    async def list_jobs(
        self, tenant_id: UUID, filters: JobFilters, page: PageParams
    ) -> tuple[Sequence[Job], int]:
        """Paged jobs, newest created first (`created_at`, `id` tiebreak)."""
        ...

    async def update_job(
        self,
        tenant_id: UUID,
        job_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Job | None:
        """Edit `draft`/`open` jobs (`WHERE status IN ('draft', 'open')`).

        `None` when the row is closed (lost the race) or not in the tenant.
        """
        ...

    async def transition_open(
        self, tenant_id: UUID, job_id: UUID, *, now: datetime
    ) -> Job | None:
        """`draft → open`, stamping `opened_at` (`WHERE status = 'draft'`)."""
        ...

    async def transition_close(
        self, tenant_id: UUID, job_id: UUID, *, now: datetime
    ) -> Job | None:
        """`open → closed`, stamping `closed_at` (`WHERE status = 'open'`)."""
        ...

    # --- candidates ---------------------------------------------------------

    async def visible_candidate_ids(
        self, tenant_id: UUID, employee_id: UUID
    ) -> Sequence[UUID]:
        """Candidates the employee has an interview for — the scoped read."""
        ...

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
        """Insert an application in `applied`.

        A `(tenant_id, job_id, email)` violation is the pinned `409
        candidate_already_applied` (the adapter maps `IntegrityError`).
        """
        ...

    async def get_candidate(
        self, tenant_id: UUID, candidate_id: UUID
    ) -> Candidate | None:
        """Tenant-scoped detail; `None` for a missing or foreign id."""
        ...

    async def lock_candidate(
        self, tenant_id: UUID, candidate_id: UUID
    ) -> Candidate | None:
        """Read the candidate under `FOR UPDATE` before the hire decision."""
        ...

    async def list_candidates(
        self, tenant_id: UUID, filters: CandidateFilters, page: PageParams
    ) -> tuple[Sequence[Candidate], int]:
        """Paged candidates, newest created first (`created_at`, `id` tiebreak)."""
        ...

    async def update_candidate(
        self,
        tenant_id: UUID,
        candidate_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Candidate | None:
        """Edit contact fields; `None` when the row is not in the tenant.

        The email uniqueness guard applies exactly as on create.
        """
        ...

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
        """Conditional pipeline move (`WHERE status = :from_status`).

        `None` when the observed source state was lost to a concurrent move or
        the row is not in the tenant. `stage_note` is written only for the
        terminal side exits (`rejected`/`withdrawn`) and `rejected_from` only
        for a rejection; an advance leaves both untouched.
        """
        ...

    async def count_hired(self, tenant_id: UUID, job_id: UUID) -> int:
        """How many candidates on the job are `hired` (the opening capacity)."""
        ...

    async def mark_hired(
        self,
        tenant_id: UUID,
        candidate_id: UUID,
        *,
        employee_id: UUID,
        hired_at: datetime,
        now: datetime,
    ) -> Candidate | None:
        """Conditional `offered → hired`, stamping the seam's employee id.

        `None` when the row is not `offered` (a concurrent decision won) or not
        in the tenant; the service maps that to `409 candidate_stage_conflict`.
        """
        ...
