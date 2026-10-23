"""Data-access abstractions for the Recruitment domain (T2).

Protocols only — the domain never imports infrastructure. Every method takes
`tenant_id` explicitly: tenant scoping is enforced in repository
implementations (`WHERE tenant_id = :tenant`), never in routers or services.
Repositories speak the pinned wire schemas (`schemas.py`); ORM translation
happens inside the Postgres adapter.

Job state changes are conditional updates on the observed source status: `None`
means the guard lost a concurrent race or the row is not in the tenant, and the
service maps that to the pinned wrong-state error (never a silent overwrite).

`JobFilters.visible_job_ids` carries the *effective* scope: the service resolves
a manager's interview-derived visibility before calling `list_jobs`. `None`
means "no job scoping" (HR/Admin/Super Admin); an empty sequence matches
nothing. `visible_job_ids` is the interview-assignment read the scoped viewer
uses (T5 makes it non-empty once interviews exist).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.domains.recruitment.schemas import Job, JobStatus
from app.shared.pagination import PageParams


class JobFilters(BaseModel):
    """Effective job-list filters (visibility already resolved)."""

    status: JobStatus | None = None
    q: str | None = None
    department_id: UUID | None = None
    visible_job_ids: Sequence[UUID] | None = None


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
