"""Recruitment routes — jobs first (T2), candidates/interviews in T3/T5.

Thin by rule: identity → domain policy/service; `now` is taken at this edge only
(`_now()` is the single wall-clock read, every service method takes it as its
last parameter). operationIds are pinned by the feature spec and drive the
orval-generated web hooks.

Every route calls the service, which applies `ensure_can_manage` on mutations
and the visibility matrix on reads (`403 recruitment_denied` for roles without
standing, `404` for out-of-scope entities).
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.dependencies import IdentityDep, RecruitmentServiceDep
from app.domains.recruitment.schemas import (
    Job,
    JobCreate,
    JobListPage,
    JobStatus,
    JobUpdate,
)
from app.shared.pagination import PageParamsDep

router = APIRouter()


def _now() -> datetime:
    """The single place wall-clock time is read (time is a parameter below)."""
    return datetime.now(UTC)


@router.post(
    "/recruitment/jobs",
    response_model=Job,
    operation_id="createJob",
    status_code=201,
)
async def create_job(
    payload: JobCreate,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Job:
    """Create a job in `draft`."""
    return await service.create_job(identity, payload, _now())


@router.get(
    "/recruitment/jobs",
    response_model=JobListPage,
    operation_id="listJobs",
)
async def list_jobs(
    identity: IdentityDep,
    service: RecruitmentServiceDep,
    page: PageParamsDep,
    status: JobStatus | None = Query(default=None),
    q: str | None = Query(default=None),
    department_id: UUID | None = Query(default=None),
) -> JobListPage:
    """Jobs scoped to the caller's visibility (## Roles & access)."""
    return await service.list_jobs(
        identity,
        status=status,
        q=q,
        department_id=department_id,
        page=page,
    )


@router.get(
    "/recruitment/jobs/{job_id}",
    response_model=Job,
    operation_id="getJob",
)
async def get_job(
    job_id: UUID,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Job:
    """Job detail; unknown, cross-tenant and out-of-scope ids are `404`."""
    return await service.get_job(identity, job_id)


@router.patch(
    "/recruitment/jobs/{job_id}",
    response_model=Job,
    operation_id="updateJob",
)
async def update_job(
    job_id: UUID,
    payload: JobUpdate,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Job:
    """Edit a `draft`/`open` job (`409 job_not_open` when closed)."""
    return await service.update_job(identity, job_id, payload, _now())


@router.post(
    "/recruitment/jobs/{job_id}/open",
    response_model=Job,
    operation_id="openJob",
)
async def open_job(
    job_id: UUID,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Job:
    """Open a draft job: `draft → open` (`409 job_not_draft` otherwise)."""
    return await service.open_job(identity, job_id, _now())


@router.post(
    "/recruitment/jobs/{job_id}/close",
    response_model=Job,
    operation_id="closeJob",
)
async def close_job(
    job_id: UUID,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Job:
    """Close an open job: `open → closed`, terminal."""
    return await service.close_job(identity, job_id, _now())
