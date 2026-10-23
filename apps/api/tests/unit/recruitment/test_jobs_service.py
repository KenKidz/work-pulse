"""Unit tests for the Recruitment job use cases (T2).

No I/O: the service runs against `FakeRecruitmentRepo`, a recording audit writer
and the recording outbox. Covered:

- create → `draft` + audit, no event (the first catalog job event is opened);
- org-ref validation through the Organizations readers (their 404s propagate);
- `draft → open → closed` with stamps, events and audit;
- pinned wrong-state conflicts, including a lost conditional-update race;
- edits only while `draft`/`open`; absent fields stay unchanged;
- read visibility: management, interview-scoped managers, no-standing roles;
- list filters (`status`, `q`, `department_id`) and clamped pagination.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domains.organizations.exceptions import (
    DepartmentNotFoundError,
    LocationNotFoundError,
    PositionNotFoundError,
)
from app.domains.recruitment.exceptions import (
    JobNotDraftError,
    JobNotFoundError,
    JobNotOpenError,
    RecruitmentDeniedError,
)
from app.domains.recruitment.schemas import JobStatus, JobUpdate
from app.domains.recruitment.service import RecruitmentService
from app.shared.identity import Role
from app.shared.pagination import PageParams
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox
from tests.unit.recruitment.conftest import (
    DEPARTMENT,
    EMPLOYEE,
    LOCATION,
    NOW,
    OTHER_DEPARTMENT,
    POSITION,
    TENANT_A,
    TENANT_B,
    USER,
    create_job,
    identity,
    seed_job,
)
from tests.unit.recruitment.fakes import FakeOrgRefs, FakeRecruitmentRepo


async def test_create_job_is_draft_with_audit_and_no_event(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    job = await create_job(service, description="Build the thing")

    assert job.status is JobStatus.DRAFT
    assert job.created_by == USER
    assert job.opened_at is None
    assert job.closed_at is None
    assert repo.jobs[(TENANT_A, job.id)] == job

    # Job creation is not a catalog event (the first job fact is `job.opened`).
    assert outbox.envelopes == []
    assert [entry.action for entry in audit.entries] == ["job.created"]
    changes = audit.entries[0].changes or {}
    assert changes["status"] == {"old": None, "new": "draft"}
    assert changes["title"] == {"old": None, "new": "Senior Engineer"}
    assert changes["openings"] == {"old": None, "new": 1}
    assert changes["department_id"] == {"old": None, "new": str(DEPARTMENT)}


@pytest.mark.parametrize(
    ("reader", "error"),
    [
        ("departments", DepartmentNotFoundError),
        ("positions", PositionNotFoundError),
        ("locations", LocationNotFoundError),
    ],
)
async def test_create_job_validates_org_refs(
    service: RecruitmentService,
    request: pytest.FixtureRequest,
    reader: str,
    error: type[Exception],
) -> None:
    refs = request.getfixturevalue(reader)
    assert isinstance(refs, FakeOrgRefs)
    refs.ids.clear()

    with pytest.raises(error):
        await create_job(service)


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
async def test_create_job_denies_non_management_roles(
    service: RecruitmentService, role: Role
) -> None:
    with pytest.raises(RecruitmentDeniedError):
        await create_job(service, caller=identity(role=role))


async def test_open_job_stamps_and_emits(
    service: RecruitmentService,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    job = await create_job(service, openings=3)

    opened = await service.open_job(identity(), job.id, NOW)

    assert opened.status is JobStatus.OPEN
    assert opened.opened_at == NOW
    assert [entry.action for entry in audit.entries] == ["job.created", "job.opened"]
    assert audit.entries[-1].changes == {"status": {"old": "draft", "new": "open"}}

    assert len(outbox.envelopes) == 1
    envelope = outbox.envelopes[0]
    assert envelope["event_type"] == "job.opened"
    assert envelope["actor_id"] == str(USER)
    assert envelope["data"] == {
        "job_id": str(job.id),
        "position_id": str(POSITION),
        "department_id": str(DEPARTMENT),
        "location_id": str(LOCATION),
        "openings": 3,
    }


async def test_open_job_requires_draft(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)

    with pytest.raises(JobNotDraftError):
        await service.open_job(identity(), job.id, NOW)


async def test_open_job_lost_race_is_a_stage_conflict(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    outbox: RecordingEventOutbox,
) -> None:
    job = await create_job(service)
    repo.lose_next_transition = True

    with pytest.raises(JobNotDraftError):
        await service.open_job(identity(), job.id, NOW)
    assert outbox.envelopes == []


async def test_close_job_stamps_and_emits(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    job = await create_job(service)
    await service.open_job(identity(), job.id, NOW)
    closed_at = datetime(2026, 10, 24, 9, 30, tzinfo=UTC)

    closed = await service.close_job(identity(), job.id, closed_at)

    assert closed.status is JobStatus.CLOSED
    assert closed.closed_at == closed_at
    assert [entry.action for entry in audit.entries][-1] == "job.closed"
    assert outbox.envelopes[-1]["event_type"] == "job.closed"
    assert outbox.envelopes[-1]["data"] == {
        "job_id": str(job.id),
        "closed_by": str(USER),
    }


async def test_close_job_requires_open(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    job = seed_job(repo, status=JobStatus.DRAFT)

    with pytest.raises(JobNotOpenError):
        await service.close_job(identity(), job.id, NOW)


async def test_close_job_lost_race_is_not_open(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    outbox: RecordingEventOutbox,
) -> None:
    job = await create_job(service)
    await service.open_job(identity(), job.id, NOW)
    repo.lose_next_transition = True

    with pytest.raises(JobNotOpenError):
        await service.close_job(identity(), job.id, NOW)
    assert [envelope["event_type"] for envelope in outbox.envelopes] == ["job.opened"]


async def test_update_job_changes_fields_and_audits(
    service: RecruitmentService, audit: RecordingAuditWriter
) -> None:
    job = await create_job(service, title="Engineer", description="Old")

    updated = await service.update_job(
        identity(),
        job.id,
        JobUpdate(title="Staff Engineer", description=None, openings=2),
        NOW,
    )

    assert updated.title == "Staff Engineer"
    assert updated.description is None
    assert updated.openings == 2
    assert updated.status is JobStatus.DRAFT
    assert updated.updated_at == NOW

    assert [entry.action for entry in audit.entries] == ["job.created", "job.updated"]
    assert audit.entries[-1].changes == {
        "title": {"old": "Engineer", "new": "Staff Engineer"},
        "description": {"old": "Old", "new": None},
        "openings": {"old": 1, "new": 2},
    }


async def test_update_job_without_changes_returns_existing(
    service: RecruitmentService,
    audit: RecordingAuditWriter,
    repo: FakeRecruitmentRepo,
) -> None:
    job = await create_job(service)

    updated = await service.update_job(identity(), job.id, JobUpdate(), NOW)

    assert updated == job
    assert [entry.action for entry in audit.entries] == ["job.created"]
    assert "update_job" not in repo.calls


async def test_update_job_requires_an_open_or_draft_job(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    job = seed_job(repo, status=JobStatus.CLOSED)

    with pytest.raises(JobNotOpenError):
        await service.update_job(identity(), job.id, JobUpdate(title="Nope"), NOW)


async def test_update_job_lost_race_is_not_open(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    job = await create_job(service)
    repo.lose_next_transition = True

    with pytest.raises(JobNotOpenError):
        await service.update_job(identity(), job.id, JobUpdate(title="Staff"), NOW)


async def test_update_job_revalidates_supplied_refs_only(
    service: RecruitmentService, departments: FakeOrgRefs
) -> None:
    job = await create_job(service)
    departments.ids.clear()

    # A title-only edit does not re-check references it did not touch.
    updated = await service.update_job(
        identity(), job.id, JobUpdate(title="Staff Engineer"), NOW
    )
    assert updated.title == "Staff Engineer"

    missing_ref = UUID("00000000-0000-4000-8000-0000000000ff")
    with pytest.raises(DepartmentNotFoundError):
        await service.update_job(
            identity(), job.id, JobUpdate(department_id=missing_ref), NOW
        )


async def test_get_job_scopes_by_role_and_assignment(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    job = await create_job(service)

    assert await service.get_job(identity(), job.id) == job
    with pytest.raises(JobNotFoundError):
        await service.get_job(identity(tenant_id=TENANT_B), job.id)

    for role in (Role.EMPLOYEE, Role.FINANCE):
        with pytest.raises(RecruitmentDeniedError):
            await service.get_job(identity(role=role), job.id)

    # A manager sees nothing without an interview assignment…
    manager = identity(role=Role.MANAGER)
    with pytest.raises(JobNotFoundError):
        await service.get_job(manager, job.id)

    # …and exactly the assigned jobs once interviews exist.
    repo.visible_jobs[(TENANT_A, EMPLOYEE)] = {job.id}
    assert await service.get_job(manager, job.id) == job


async def test_list_jobs_filters_and_visibility(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    first = seed_job(
        repo,
        title="Backend Engineer",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    second = seed_job(
        repo,
        title="Product Designer",
        department_id=OTHER_DEPARTMENT,
        status=JobStatus.OPEN,
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    page = PageParams()

    everything = await service.list_jobs(
        identity(), status=None, q=None, department_id=None, page=page
    )
    assert [job.id for job in everything.items] == [second.id, first.id]
    assert everything.page.total_items == 2

    by_status = await service.list_jobs(
        identity(), status=JobStatus.OPEN, q=None, department_id=None, page=page
    )
    assert [job.id for job in by_status.items] == [second.id]

    by_department = await service.list_jobs(
        identity(),
        status=None,
        q=None,
        department_id=OTHER_DEPARTMENT,
        page=page,
    )
    assert [job.id for job in by_department.items] == [second.id]

    by_q = await service.list_jobs(
        identity(), status=None, q="DESIGN", department_id=None, page=page
    )
    assert [job.id for job in by_q.items] == [second.id]

    # Manager with no assignment sees an empty page, not a denial.
    manager = identity(role=Role.MANAGER)
    empty = await service.list_jobs(
        manager, status=None, q=None, department_id=None, page=page
    )
    assert empty.items == []
    assert empty.page.total_items == 0

    repo.visible_jobs[(TENANT_A, EMPLOYEE)] = {first.id}
    scoped = await service.list_jobs(
        manager, status=None, q=None, department_id=None, page=page
    )
    assert [job.id for job in scoped.items] == [first.id]

    for role in (Role.EMPLOYEE, Role.FINANCE):
        with pytest.raises(RecruitmentDeniedError):
            await service.list_jobs(
                identity(role=role),
                status=None,
                q=None,
                department_id=None,
                page=page,
            )


async def test_list_jobs_paginates_with_clamped_params(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    for index in range(3):
        seed_job(
            repo,
            title=f"Job {index}",
            created_at=datetime(2026, 9, 1 + index, tzinfo=UTC),
        )

    page_one = await service.list_jobs(
        identity(), status=None, q=None, department_id=None, page=PageParams(1, 2)
    )
    page_two = await service.list_jobs(
        identity(), status=None, q=None, department_id=None, page=PageParams(2, 2)
    )

    assert len(page_one.items) == 2
    assert len(page_two.items) == 1
    assert page_one.page.total_pages == 2
    assert page_two.page.page == 2


async def test_get_or_transition_missing_job_is_404(
    service: RecruitmentService,
) -> None:
    missing = UUID("00000000-0000-4000-8000-0000000000ff")

    with pytest.raises(JobNotFoundError):
        await service.get_job(identity(), missing)
    with pytest.raises(JobNotFoundError):
        await service.update_job(identity(), missing, JobUpdate(title="X"), NOW)
    with pytest.raises(JobNotFoundError):
        await service.open_job(identity(), missing, NOW)
    with pytest.raises(JobNotFoundError):
        await service.close_job(identity(), missing, NOW)
