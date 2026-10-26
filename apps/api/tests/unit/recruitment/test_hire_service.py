"""Unit tests for the Recruitment hire use case (T4, ADR 0020).

No I/O: the service runs against `FakeRecruitmentRepo`, a recording audit
writer, the recording outbox and a recording `EmployeeHirePort`. Covered:

- the happy path: `offered → hired`, the seam's employee id stamped, one
  `candidate.hired` envelope and an audit entry with no PII;
- override resolution (candidate record as the default, hire terms win) and the
  job's organization refs;
- every guard: wrong candidate stage, non-open job, filled openings, future
  hire date, missing/foreign candidate;
- seam failures propagate unchanged and leave the candidate `OFFERED` with no
  outbox row and no audit entry (the atomicity contract);
- role gate and the conditional-update race backstop.
"""

from datetime import date
from typing import Any
from uuid import UUID

import pytest

from app.domains.employees.exceptions import (
    EmployeeCodeTakenError,
    EmployeeEmailTakenError,
    EmployeeManagerInvalidError,
    TransferTargetArchivedError,
)
from app.domains.organizations.exceptions import (
    DepartmentNotFoundError,
    LocationNotFoundError,
    PositionNotFoundError,
)
from app.domains.recruitment.exceptions import (
    CandidateNotFoundError,
    CandidateStageConflictError,
    HireDateInFutureError,
    JobNotOpenError,
    JobOpeningsFilledError,
    RecruitmentDeniedError,
)
from app.domains.recruitment.schemas import (
    CandidateStatus,
    HireCandidate,
    JobStatus,
)
from app.domains.recruitment.service import RecruitmentService
from app.shared.errors import WorkPulseError
from app.shared.identity import Role
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox
from tests.unit.recruitment.conftest import (
    DEPARTMENT,
    LOCATION,
    NOW,
    POSITION,
    TENANT_A,
    TENANT_B,
    USER,
    identity,
    seed_candidate,
    seed_job,
)
from tests.unit.recruitment.fakes import FakeEmployeeHirePort, FakeRecruitmentRepo

MISSING = UUID("00000000-0000-4000-8000-0000000000ff")
MANAGER = UUID("00000000-0000-4000-8000-0000000000cc")


def _hire_payload(**overrides: object) -> HireCandidate:
    payload: dict[str, Any] = {
        "employment_type": "full_time",
        "hired_at": date(2026, 10, 20),
    }
    payload.update(overrides)
    return HireCandidate(**payload)


async def test_hire_creates_employee_and_emits_candidate_hired(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    hire: FakeEmployeeHirePort,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN, openings=1)
    candidate = seed_candidate(
        repo,
        job_id=job.id,
        phone="+44 20 7946 0000",
        status=CandidateStatus.OFFERED,
    )

    hired = await service.hire_candidate(identity(), candidate.id, _hire_payload(), NOW)

    assert hired.status is CandidateStatus.HIRED
    assert hired.employee_id == hire.employee_id
    assert hired.hired_at == NOW
    assert hired.interviews == []
    stored = repo.candidates[(TENANT_A, candidate.id)]
    assert stored.status is CandidateStatus.HIRED
    assert stored.employee_id == hire.employee_id
    assert stored.hired_at == NOW
    assert repo.calls[-4:] == [
        "lock_candidate",
        "lock_job",
        "count_hired",
        "mark_hired",
    ]

    # The seam receives the effective CreateEmployee fields on the job's refs.
    assert len(hire.calls) == 1
    tenant_id, actor_id, payload, now = hire.calls[0]
    assert (tenant_id, actor_id, now) == (TENANT_A, USER, NOW)
    assert payload.first_name == "Ada"
    assert payload.last_name == "Lovelace"
    assert payload.email == "ada@example.test"
    assert payload.phone == "+44 20 7946 0000"
    assert payload.department_id == DEPARTMENT
    assert payload.position_id == POSITION
    assert payload.location_id == LOCATION
    assert payload.manager_id is None
    assert payload.employment_type == "full_time"
    assert payload.hired_at == date(2026, 10, 20)
    assert payload.employee_code is None

    assert len(outbox.envelopes) == 1
    envelope = outbox.envelopes[0]
    assert envelope["event_type"] == "candidate.hired"
    assert envelope["actor_id"] == str(USER)
    assert envelope["tenant_id"] == str(TENANT_A)
    assert envelope["data"] == {
        "candidate_id": str(candidate.id),
        "job_id": str(job.id),
        "employee_id": str(hire.employee_id),
        "hired_by": str(USER),
    }

    assert [entry.action for entry in audit.entries] == ["candidate.hired"]
    changes = audit.entries[0].changes or {}
    assert changes == {
        "status": {"old": "offered", "new": "hired"},
        "employee_id": {"old": None, "new": str(hire.employee_id)},
        "hired_at": {"old": None, "new": NOW.isoformat()},
    }
    rendered = f"{changes} {envelope}"
    for pii in ("Ada", "Lovelace", "ada@example.test", "7946"):
        assert pii not in rendered


async def test_hire_overrides_candidate_contact_fields(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    hire: FakeEmployeeHirePort,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(
        repo,
        job_id=job.id,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.test",
        phone="+44 20 7946 0000",
        status=CandidateStatus.OFFERED,
    )

    hired = await service.hire_candidate(
        identity(),
        candidate.id,
        _hire_payload(
            employment_type="contract",
            hired_at=date(2026, 10, 1),
            manager_id=MANAGER,
            employee_code="EMP-77777",
            first_name="Augusta",
            last_name="King",
            email="augusta@example.test",
            phone="+1 555 0100",
        ),
        NOW,
    )

    payload = hire.calls[0][2]
    assert payload.first_name == "Augusta"
    assert payload.last_name == "King"
    assert payload.email == "augusta@example.test"
    assert payload.phone == "+1 555 0100"
    assert payload.manager_id == MANAGER
    assert payload.employment_type == "contract"
    assert payload.hired_at == date(2026, 10, 1)
    assert payload.employee_code == "EMP-77777"
    assert hired.employee_id == hire.employee_id


async def test_explicit_none_overrides_fall_back_to_the_candidate(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    hire: FakeEmployeeHirePort,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(
        repo,
        job_id=job.id,
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.test",
        phone="+1 555 0199",
        status=CandidateStatus.OFFERED,
    )

    await service.hire_candidate(
        identity(),
        candidate.id,
        _hire_payload(
            first_name=None,
            last_name=None,
            email=None,
            phone=None,
        ),
        NOW,
    )

    payload = hire.calls[0][2]
    assert payload.first_name == "Grace"
    assert payload.last_name == "Hopper"
    assert payload.email == "grace@example.test"
    assert payload.phone == "+1 555 0199"


@pytest.mark.parametrize(
    "status",
    [
        CandidateStatus.APPLIED,
        CandidateStatus.INTERVIEWING,
        CandidateStatus.HIRED,
        CandidateStatus.REJECTED,
        CandidateStatus.WITHDRAWN,
    ],
)
async def test_hire_requires_an_offered_candidate(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    hire: FakeEmployeeHirePort,
    status: CandidateStatus,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(repo, job_id=job.id, status=status)

    with pytest.raises(CandidateStageConflictError):
        await service.hire_candidate(identity(), candidate.id, _hire_payload(), NOW)

    assert hire.calls == []
    assert outbox.envelopes == []
    assert audit.entries == []
    assert repo.candidates[(TENANT_A, candidate.id)].status is status


@pytest.mark.parametrize("status", [JobStatus.DRAFT, JobStatus.CLOSED])
async def test_hire_requires_an_open_job(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    hire: FakeEmployeeHirePort,
    status: JobStatus,
) -> None:
    job = seed_job(repo, status=status)
    candidate = seed_candidate(repo, job_id=job.id, status=CandidateStatus.OFFERED)

    with pytest.raises(JobNotOpenError):
        await service.hire_candidate(identity(), candidate.id, _hire_payload(), NOW)

    assert hire.calls == []
    assert outbox.envelopes == []
    assert audit.entries == []
    assert repo.candidates[(TENANT_A, candidate.id)].status is CandidateStatus.OFFERED


async def test_hire_refuses_when_openings_are_filled(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    outbox: RecordingEventOutbox,
    audit: RecordingAuditWriter,
    hire: FakeEmployeeHirePort,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN, openings=1)
    first = seed_candidate(repo, job_id=job.id, status=CandidateStatus.OFFERED)
    second = seed_candidate(
        repo,
        job_id=job.id,
        email="grace@example.test",
        status=CandidateStatus.OFFERED,
    )

    await service.hire_candidate(identity(), first.id, _hire_payload(), NOW)
    with pytest.raises(JobOpeningsFilledError):
        await service.hire_candidate(identity(), second.id, _hire_payload(), NOW)

    assert repo.candidates[(TENANT_A, second.id)].status is CandidateStatus.OFFERED
    assert [entry.action for entry in audit.entries] == ["candidate.hired"]
    assert [envelope["event_type"] for envelope in outbox.envelopes] == [
        "candidate.hired"
    ]
    assert len(hire.calls) == 1


async def test_hire_rejects_a_future_hire_date(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    hire: FakeEmployeeHirePort,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(repo, job_id=job.id, status=CandidateStatus.OFFERED)

    with pytest.raises(HireDateInFutureError):
        await service.hire_candidate(
            identity(),
            candidate.id,
            _hire_payload(hired_at=date(2026, 10, 24)),  # NOW is 2026-10-23
            NOW,
        )

    assert hire.calls == []
    assert outbox.envelopes == []
    assert audit.entries == []


@pytest.mark.parametrize(
    "error_type",
    [
        EmployeeEmailTakenError,
        EmployeeCodeTakenError,
        EmployeeManagerInvalidError,
        DepartmentNotFoundError,
        PositionNotFoundError,
        LocationNotFoundError,
        TransferTargetArchivedError,
    ],
    ids=[
        "employee_email_taken",
        "employee_code_taken",
        "employee_manager_invalid",
        "department_not_found",
        "position_not_found",
        "location_not_found",
        "transfer_target_archived",
    ],
)
async def test_seam_errors_propagate_and_leave_the_candidate_offered(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    hire: FakeEmployeeHirePort,
    error_type: type[WorkPulseError],
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(repo, job_id=job.id, status=CandidateStatus.OFFERED)
    error = error_type()
    hire.error = error

    with pytest.raises(error_type) as excinfo:
        await service.hire_candidate(identity(), candidate.id, _hire_payload(), NOW)

    assert excinfo.value is error
    assert repo.candidates[(TENANT_A, candidate.id)].status is CandidateStatus.OFFERED
    assert repo.candidates[(TENANT_A, candidate.id)].employee_id is None
    assert outbox.envelopes == []
    assert audit.entries == []
    assert len(hire.calls) == 1


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
async def test_hire_is_management_only(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    hire: FakeEmployeeHirePort,
    role: Role,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(repo, job_id=job.id, status=CandidateStatus.OFFERED)

    with pytest.raises(RecruitmentDeniedError):
        await service.hire_candidate(
            identity(role=role), candidate.id, _hire_payload(), NOW
        )

    assert hire.calls == []


@pytest.mark.parametrize("tenant_id", [TENANT_A, TENANT_B])
async def test_hire_missing_or_foreign_candidate_is_not_found(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    hire: FakeEmployeeHirePort,
    tenant_id: UUID,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(repo, job_id=job.id, status=CandidateStatus.OFFERED)
    candidate_id = MISSING if tenant_id == TENANT_A else candidate.id

    with pytest.raises(CandidateNotFoundError):
        await service.hire_candidate(
            identity(tenant_id=tenant_id), candidate_id, _hire_payload(), NOW
        )

    assert hire.calls == []
    assert outbox.envelopes == []
    assert audit.entries == []


async def test_lost_hire_transition_race_is_a_stage_conflict(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(repo, job_id=job.id, status=CandidateStatus.OFFERED)
    repo.lose_next_hired_transition = True

    with pytest.raises(CandidateStageConflictError):
        await service.hire_candidate(identity(), candidate.id, _hire_payload(), NOW)

    assert repo.candidates[(TENANT_A, candidate.id)].status is CandidateStatus.OFFERED
    assert outbox.envelopes == []
    assert audit.entries == []
