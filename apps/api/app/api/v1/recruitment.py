"""Recruitment routes — jobs (T2), candidates (T3), interviews (T5).

Thin by rule: identity → domain policy/service; `now` is taken at this edge only
(`_now()` is the single wall-clock read, every service method takes it as its
last parameter). operationIds are pinned by the feature spec and drive the
orval-generated web hooks.

Every route calls the service, which applies `ensure_can_manage` on mutations
and the visibility matrix on reads (`403 recruitment_denied` for roles without
standing, `404` for out-of-scope entities). The interview list keeps the pinned
query names (`from`/`to` are aliased on the date parameters).
"""

from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.dependencies import IdentityDep, RecruitmentServiceDep
from app.domains.recruitment.schemas import (
    Candidate,
    CandidateCreate,
    CandidateDecision,
    CandidateDetail,
    CandidateListPage,
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


# --- candidates --------------------------------------------------------------


@router.post(
    "/recruitment/jobs/{job_id}/candidates",
    response_model=Candidate,
    operation_id="createCandidate",
    status_code=201,
)
async def create_candidate(
    job_id: UUID,
    payload: CandidateCreate,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Candidate:
    """Add an application to an `open` job — always `applied`."""
    return await service.create_candidate(identity, job_id, payload, _now())


@router.get(
    "/recruitment/candidates",
    response_model=CandidateListPage,
    operation_id="listCandidates",
)
async def list_candidates(
    identity: IdentityDep,
    service: RecruitmentServiceDep,
    page: PageParamsDep,
    job_id: UUID | None = Query(default=None),
    status: CandidateStatus | None = Query(default=None),
    q: str | None = Query(default=None),
) -> CandidateListPage:
    """Candidates scoped to the caller's visibility (## Roles & access)."""
    return await service.list_candidates(
        identity,
        job_id=job_id,
        status=status,
        q=q,
        page=page,
    )


@router.get(
    "/recruitment/candidates/{candidate_id}",
    response_model=CandidateDetail,
    operation_id="getCandidate",
)
async def get_candidate(
    candidate_id: UUID,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> CandidateDetail:
    """Candidate detail; unknown, cross-tenant and out-of-scope ids are `404`."""
    return await service.get_candidate(identity, candidate_id)


@router.patch(
    "/recruitment/candidates/{candidate_id}",
    response_model=Candidate,
    operation_id="updateCandidate",
)
async def update_candidate(
    candidate_id: UUID,
    payload: CandidateUpdate,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Candidate:
    """Edit contact fields; the job-scoped email uniqueness guard applies."""
    return await service.update_candidate(identity, candidate_id, payload, _now())


@router.post(
    "/recruitment/candidates/{candidate_id}/advance",
    response_model=Candidate,
    operation_id="advanceCandidate",
)
async def advance_candidate(
    candidate_id: UUID,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Candidate:
    """Step exactly one stage forward (`409 candidate_stage_conflict`)."""
    return await service.advance_candidate(identity, candidate_id, _now())


@router.post(
    "/recruitment/candidates/{candidate_id}/reject",
    response_model=Candidate,
    operation_id="rejectCandidate",
)
async def reject_candidate(
    candidate_id: UUID,
    payload: CandidateDecision,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Candidate:
    """Terminal reject from any pre-hire stage."""
    return await service.reject_candidate(identity, candidate_id, payload, _now())


@router.post(
    "/recruitment/candidates/{candidate_id}/withdraw",
    response_model=Candidate,
    operation_id="withdrawCandidate",
)
async def withdraw_candidate(
    candidate_id: UUID,
    payload: CandidateDecision,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Candidate:
    """HR records a candidate withdrawal from any pre-hire stage."""
    return await service.withdraw_candidate(identity, candidate_id, payload, _now())


@router.post(
    "/recruitment/candidates/{candidate_id}/hire",
    response_model=CandidateDetail,
    operation_id="hireCandidate",
)
async def hire_candidate(
    candidate_id: UUID,
    payload: HireCandidate,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> CandidateDetail:
    """Hire an `offered` candidate; creates the Employee through the seam."""
    return await service.hire_candidate(identity, candidate_id, payload, _now())


# --- interviews --------------------------------------------------------------


@router.post(
    "/recruitment/candidates/{candidate_id}/interviews",
    response_model=Interview,
    operation_id="scheduleInterview",
    status_code=201,
)
async def schedule_interview(
    candidate_id: UUID,
    payload: InterviewSchedule,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Interview:
    """Schedule an interview for an `interviewing` candidate."""
    return await service.schedule_interview(identity, candidate_id, payload, _now())


@router.get(
    "/recruitment/interviews",
    response_model=InterviewListPage,
    operation_id="listInterviews",
)
async def list_interviews(
    identity: IdentityDep,
    service: RecruitmentServiceDep,
    page: PageParamsDep,
    candidate_id: UUID | None = Query(default=None),
    job_id: UUID | None = Query(default=None),
    status: InterviewStatus | None = Query(default=None),
    mine: bool = Query(default=False),
    interviewer_id: UUID | None = Query(default=None),
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
) -> InterviewListPage:
    """Interviews scoped to the caller (`mine=1` for any caller)."""
    return await service.list_interviews(
        identity,
        candidate_id=candidate_id,
        job_id=job_id,
        status=status,
        mine=mine,
        interviewer_id=interviewer_id,
        scheduled_from=from_date,
        scheduled_to=to_date,
        page=page,
    )


@router.patch(
    "/recruitment/interviews/{interview_id}",
    response_model=Interview,
    operation_id="updateInterview",
)
async def update_interview(
    interview_id: UUID,
    payload: InterviewUpdate,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Interview:
    """Reschedule / re-assign / re-note while `scheduled` (management-only)."""
    return await service.update_interview(identity, interview_id, payload, _now())


@router.post(
    "/recruitment/interviews/{interview_id}/complete",
    response_model=Interview,
    operation_id="completeInterview",
)
async def complete_interview(
    interview_id: UUID,
    payload: InterviewFeedback,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Interview:
    """The assigned interviewer files feedback: `scheduled → completed`."""
    return await service.complete_interview(identity, interview_id, payload, _now())


@router.post(
    "/recruitment/interviews/{interview_id}/cancel",
    response_model=Interview,
    operation_id="cancelInterview",
)
async def cancel_interview(
    interview_id: UUID,
    payload: CandidateDecision,
    identity: IdentityDep,
    service: RecruitmentServiceDep,
) -> Interview:
    """Cancel a `scheduled` interview (management-only), terminal."""
    return await service.cancel_interview(identity, interview_id, payload, _now())
