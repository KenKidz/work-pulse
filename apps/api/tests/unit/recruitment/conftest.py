"""Shared fixtures for the Recruitment domain unit tests (no database, no broker).

`NOW` is the injected clock every test passes explicitly — the service never
reads the wall clock (`.ai/testing.md`: no wall-time assertions).
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from uuid_utils.compat import uuid7

from app.domains.organizations.exceptions import (
    DepartmentNotFoundError,
    LocationNotFoundError,
    PositionNotFoundError,
)
from app.domains.recruitment.schemas import (
    Candidate,
    CandidateCreate,
    CandidateRejectedFrom,
    CandidateSource,
    CandidateStatus,
    Interview,
    InterviewSchedule,
    InterviewStatus,
    Job,
    JobCreate,
    JobStatus,
)
from app.domains.recruitment.service import RecruitmentService
from app.shared.identity import Identity, Role
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox
from tests.unit.recruitment.fakes import (
    CREATED_AT,
    FakeEmployeeHirePort,
    FakeOrgRefs,
    FakeRecruitmentRepo,
)

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
USER = UUID("00000000-0000-4000-8000-000000000031")  # the acting user id
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000002")
OTHER_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000005")

DEPARTMENT = UUID("00000000-0000-4000-8000-0000000000d1")
POSITION = UUID("00000000-0000-4000-8000-0000000000e1")
LOCATION = UUID("00000000-0000-4000-8000-0000000000f1")
OTHER_DEPARTMENT = UUID("00000000-0000-4000-8000-0000000000d2")

# NOW = 2026-10-23 12:00 UTC — the pinned clock for every mutation test.
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
# The default interview slot used by the service/HTTP tests.
INTERVIEW_AT = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


def identity(
    tenant_id: UUID = TENANT_A,
    role: Role = Role.HR,
    *,
    employee_id: UUID | None = EMPLOYEE,
    actor_id: UUID = USER,
) -> Identity:
    """The acting user (`actor_id`) plus its optional employee link."""
    return Identity(
        tenant_id=tenant_id,
        actor_id=actor_id,
        role=role,
        employee_id=employee_id,
    )


@pytest.fixture
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def repo() -> FakeRecruitmentRepo:
    return FakeRecruitmentRepo()


@pytest.fixture
def outbox() -> RecordingEventOutbox:
    return RecordingEventOutbox()


@pytest.fixture
def departments() -> FakeOrgRefs:
    refs = FakeOrgRefs(DepartmentNotFoundError)
    refs.ids.add(DEPARTMENT)
    return refs


@pytest.fixture
def positions() -> FakeOrgRefs:
    refs = FakeOrgRefs(PositionNotFoundError)
    refs.ids.add(POSITION)
    return refs


@pytest.fixture
def locations() -> FakeOrgRefs:
    refs = FakeOrgRefs(LocationNotFoundError)
    refs.ids.add(LOCATION)
    return refs


@pytest.fixture
def hire() -> FakeEmployeeHirePort:
    return FakeEmployeeHirePort()


@pytest.fixture
def service(
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    departments: FakeOrgRefs,
    positions: FakeOrgRefs,
    locations: FakeOrgRefs,
    hire: FakeEmployeeHirePort,
) -> RecruitmentService:
    return RecruitmentService(
        repo=repo,
        audit=audit,
        outbox=outbox,
        departments=departments,
        positions=positions,
        locations=locations,
        hire=hire,
    )


# --- seeding helpers ---------------------------------------------------------


def seed_job(
    repo: FakeRecruitmentRepo,
    *,
    tenant_id: UUID = TENANT_A,
    title: str = "Senior Engineer",
    department_id: UUID = DEPARTMENT,
    position_id: UUID = POSITION,
    location_id: UUID = LOCATION,
    openings: int = 1,
    status: JobStatus = JobStatus.DRAFT,
    created_at: datetime = CREATED_AT,
) -> Job:
    """Insert a job directly — only for states no service path has produced."""
    job = Job(
        id=uuid7(),
        tenant_id=tenant_id,
        title=title,
        description=None,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        openings=openings,
        status=status,
        created_by=USER,
        opened_at=created_at if status is not JobStatus.DRAFT else None,
        closed_at=created_at if status is JobStatus.CLOSED else None,
        created_at=created_at,
        updated_at=created_at,
    )
    repo.jobs[(tenant_id, job.id)] = job
    return job


async def create_job(
    service: RecruitmentService,
    *,
    caller: Identity | None = None,
    title: str = "Senior Engineer",
    description: str | None = None,
    openings: int = 1,
    now: datetime = NOW,
) -> Job:
    return await service.create_job(
        caller or identity(),
        JobCreate(
            title=title,
            description=description,
            department_id=DEPARTMENT,
            position_id=POSITION,
            location_id=LOCATION,
            openings=openings,
        ),
        now,
    )


def seed_candidate(
    repo: FakeRecruitmentRepo,
    *,
    job_id: UUID,
    tenant_id: UUID = TENANT_A,
    first_name: str = "Ada",
    last_name: str = "Lovelace",
    email: str = "ada@example.test",
    phone: str | None = None,
    source: CandidateSource = CandidateSource.REFERRAL,
    notes: str | None = None,
    status: CandidateStatus = CandidateStatus.APPLIED,
    stage_note: str | None = None,
    rejected_from: CandidateRejectedFrom | None = None,
    created_at: datetime = CREATED_AT,
) -> Candidate:
    """Insert a candidate directly — only for states no service path produced."""
    candidate = Candidate(
        id=uuid7(),
        tenant_id=tenant_id,
        job_id=job_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        source=source,
        notes=notes,
        status=status,
        stage_note=stage_note,
        rejected_from=rejected_from,
        created_at=created_at,
        updated_at=created_at,
    )
    repo.candidates[(tenant_id, candidate.id)] = candidate
    return candidate


async def create_candidate(
    service: RecruitmentService,
    job_id: UUID,
    *,
    caller: Identity | None = None,
    first_name: str = "Ada",
    last_name: str = "Lovelace",
    email: str = "ada@example.test",
    phone: str | None = None,
    source: CandidateSource = CandidateSource.REFERRAL,
    notes: str | None = None,
    now: datetime = NOW,
) -> Candidate:
    return await service.create_candidate(
        caller or identity(),
        job_id,
        CandidateCreate(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            source=source,
            notes=notes,
        ),
        now,
    )


def seed_interview(
    repo: FakeRecruitmentRepo,
    *,
    candidate_id: UUID,
    job_id: UUID,
    tenant_id: UUID = TENANT_A,
    interviewer_id: UUID = EMPLOYEE,
    scheduled_at: datetime = INTERVIEW_AT,
    notes: str | None = None,
    status: InterviewStatus = InterviewStatus.SCHEDULED,
    rating: int | None = None,
    feedback_notes: str | None = None,
    created_at: datetime = CREATED_AT,
) -> Interview:
    """Insert an interview directly — only for states no service path produced."""
    interview = Interview(
        id=uuid7(),
        tenant_id=tenant_id,
        candidate_id=candidate_id,
        job_id=job_id,
        interviewer_id=interviewer_id,
        scheduled_at=scheduled_at,
        notes=notes,
        status=status,
        rating=rating,
        feedback_notes=feedback_notes,
        completed_at=created_at if status is InterviewStatus.COMPLETED else None,
        cancelled_at=created_at if status is InterviewStatus.CANCELLED else None,
        created_at=created_at,
        updated_at=created_at,
    )
    repo.interviews[(tenant_id, interview.id)] = interview
    return interview


async def schedule_interview(
    service: RecruitmentService,
    candidate_id: UUID,
    *,
    caller: Identity | None = None,
    interviewer_id: UUID = EMPLOYEE,
    scheduled_at: datetime = INTERVIEW_AT,
    notes: str | None = None,
    now: datetime = NOW,
) -> Interview:
    return await service.schedule_interview(
        caller or identity(),
        candidate_id,
        InterviewSchedule(
            interviewer_id=interviewer_id,
            scheduled_at=scheduled_at,
            notes=notes,
        ),
        now,
    )
