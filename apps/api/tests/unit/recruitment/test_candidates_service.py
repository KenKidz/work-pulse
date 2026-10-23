"""Unit tests for the Recruitment candidate use cases (T3).

No I/O: the service runs against `FakeRecruitmentRepo`, a recording audit writer
and the recording outbox. Covered:

- create on an `open` job → `applied` + `candidate.created`, no PII on the bus;
- duplicate `(tenant, job, email)` and non-open jobs (`409`), foreign jobs (`404`);
- `advance` steps exactly one stage and emits the observed `from`/`to`;
- `reject`/`withdraw` from every pre-hire stage, terminal refusals and lost
  conditional-update races;
- contact edits with PII audit markers and the job-scoped email guard;
- read visibility: management, interview-scoped managers, no-standing roles;
- list filters (`job_id`, `status`, `q`) and pagination.
"""

from datetime import datetime
from uuid import UUID

import pytest

from app.domains.recruitment.exceptions import (
    CandidateAlreadyAppliedError,
    CandidateNotFoundError,
    CandidateNotOpenError,
    CandidateStageConflictError,
    JobNotFoundError,
    JobNotOpenError,
    RecruitmentDeniedError,
)
from app.domains.recruitment.schemas import (
    CandidateDecision,
    CandidateRejectedFrom,
    CandidateSource,
    CandidateStatus,
    CandidateUpdate,
    JobStatus,
)
from app.domains.recruitment.service import RecruitmentService
from app.shared.identity import Role
from app.shared.pagination import PageParams
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox
from tests.unit.recruitment.conftest import (
    EMPLOYEE,
    NOW,
    TENANT_A,
    TENANT_B,
    USER,
    create_candidate,
    identity,
    seed_candidate,
    seed_job,
)
from tests.unit.recruitment.fakes import FakeRecruitmentRepo

MISSING = UUID("00000000-0000-4000-8000-0000000000ff")


async def test_create_candidate_is_applied_with_event_and_pii_safe_audit(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)

    candidate = await create_candidate(
        service,
        job.id,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.test",
        phone="+44 20 7946 0000",
        notes="Referred by Grace",
    )

    assert candidate.status is CandidateStatus.APPLIED
    assert candidate.job_id == job.id
    assert candidate.source is CandidateSource.REFERRAL
    assert candidate.rejected_from is None
    assert repo.candidates[(TENANT_A, candidate.id)] == candidate

    assert len(outbox.envelopes) == 1
    envelope = outbox.envelopes[0]
    assert envelope["event_type"] == "candidate.created"
    assert envelope["actor_id"] == str(USER)
    assert envelope["data"] == {
        "candidate_id": str(candidate.id),
        "job_id": str(job.id),
        "source": "referral",
    }

    assert [entry.action for entry in audit.entries] == ["candidate.created"]
    changes = audit.entries[0].changes or {}
    assert changes["status"] == {"old": None, "new": "applied"}
    assert changes["source"] == {"old": None, "new": "referral"}
    assert changes["first_name"] == "changed"
    assert changes["last_name"] == "changed"
    assert changes["email"] == "changed"
    assert changes["phone"] == "changed"
    assert changes["notes"] == "changed"
    # PII values are recorded as "changed", never written.
    rendered = f"{changes} {envelope}"
    for pii in ("Ada", "Lovelace", "ada@example.test", "7946", "Grace"):
        assert pii not in rendered


async def test_create_candidate_requires_an_open_job(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    draft = seed_job(repo, status=JobStatus.DRAFT)
    closed = seed_job(repo, status=JobStatus.CLOSED)

    with pytest.raises(JobNotOpenError):
        await create_candidate(service, draft.id)
    with pytest.raises(JobNotOpenError):
        await create_candidate(service, closed.id)
    with pytest.raises(JobNotFoundError):
        await create_candidate(service, MISSING)
    with pytest.raises(JobNotFoundError):
        await create_candidate(service, draft.id, caller=identity(tenant_id=TENANT_B))


async def test_create_candidate_refuses_a_duplicate_application(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    await create_candidate(service, job.id, email="ada@example.test")

    with pytest.raises(CandidateAlreadyAppliedError):
        await create_candidate(service, job.id, email="ada@example.test")

    # The same email on a different job is a separate application.
    other_job = seed_job(repo, status=JobStatus.OPEN, title="Designer")
    other = await create_candidate(service, other_job.id, email="ada@example.test")
    assert other.job_id == other_job.id


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
async def test_candidate_mutations_deny_non_management_roles(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    role: Role,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(repo, job_id=job.id)
    caller = identity(role=role)

    with pytest.raises(RecruitmentDeniedError):
        await create_candidate(service, job.id, caller=caller)
    with pytest.raises(RecruitmentDeniedError):
        await service.update_candidate(
            caller, candidate.id, CandidateUpdate(first_name="X"), NOW
        )
    with pytest.raises(RecruitmentDeniedError):
        await service.advance_candidate(caller, candidate.id, NOW)
    with pytest.raises(RecruitmentDeniedError):
        await service.reject_candidate(caller, candidate.id, CandidateDecision(), NOW)
    with pytest.raises(RecruitmentDeniedError):
        await service.withdraw_candidate(caller, candidate.id, CandidateDecision(), NOW)


async def test_advance_steps_exactly_one_stage_and_emits(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = await create_candidate(service, job.id)

    interviewing = await service.advance_candidate(identity(), candidate.id, NOW)
    offered = await service.advance_candidate(identity(), candidate.id, NOW)

    assert interviewing.status is CandidateStatus.INTERVIEWING
    assert offered.status is CandidateStatus.OFFERED
    assert [entry.action for entry in audit.entries] == [
        "candidate.created",
        "candidate.advanced",
        "candidate.advanced",
    ]
    assert audit.entries[1].changes == {
        "status": {"old": "applied", "new": "interviewing"}
    }
    assert audit.entries[2].changes == {
        "status": {"old": "interviewing", "new": "offered"}
    }

    advanced = [
        envelope
        for envelope in outbox.envelopes
        if envelope["event_type"] == "candidate.advanced"
    ]
    assert [envelope["data"] for envelope in advanced] == [
        {
            "candidate_id": str(candidate.id),
            "job_id": str(job.id),
            "from": "applied",
            "to": "interviewing",
        },
        {
            "candidate_id": str(candidate.id),
            "job_id": str(job.id),
            "from": "interviewing",
            "to": "offered",
        },
    ]

    with pytest.raises(CandidateStageConflictError):
        await service.advance_candidate(identity(), candidate.id, NOW)


@pytest.mark.parametrize(
    "status",
    [
        CandidateStatus.APPLIED,
        CandidateStatus.INTERVIEWING,
        CandidateStatus.OFFERED,
    ],
)
async def test_reject_records_source_note_and_emits(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    status: CandidateStatus,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(repo, job_id=job.id, status=status)

    rejected = await service.reject_candidate(
        identity(), candidate.id, CandidateDecision(note="Not a fit"), NOW
    )

    assert rejected.status is CandidateStatus.REJECTED
    assert rejected.rejected_from is CandidateRejectedFrom(status.value)
    assert rejected.stage_note == "Not a fit"

    envelope = outbox.envelopes[-1]
    assert envelope["event_type"] == "candidate.rejected"
    assert envelope["data"] == {
        "candidate_id": str(candidate.id),
        "job_id": str(job.id),
        "rejected_from": status.value,
        "rejected_by": str(USER),
    }
    assert audit.entries[-1].changes == {
        "status": {"old": status.value, "new": "rejected"},
        "rejected_from": {"old": None, "new": status.value},
        "stage_note": "changed",
    }
    assert "Not a fit" not in str(audit.entries[-1].changes)
    assert "Not a fit" not in str(envelope)


@pytest.mark.parametrize(
    "status",
    [CandidateStatus.HIRED, CandidateStatus.REJECTED, CandidateStatus.WITHDRAWN],
)
async def test_reject_terminal_candidate_is_not_open(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    status: CandidateStatus,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(repo, job_id=job.id, status=status)

    with pytest.raises(CandidateNotOpenError):
        await service.reject_candidate(
            identity(), candidate.id, CandidateDecision(), NOW
        )


async def test_advance_and_reject_lost_races_map_to_pinned_errors(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    outbox: RecordingEventOutbox,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(repo, job_id=job.id)

    repo.lose_next_candidate_transition = True
    with pytest.raises(CandidateStageConflictError):
        await service.advance_candidate(identity(), candidate.id, NOW)

    repo.lose_next_candidate_transition = True
    with pytest.raises(CandidateNotOpenError):
        await service.reject_candidate(
            identity(), candidate.id, CandidateDecision(), NOW
        )

    assert outbox.envelopes == []


async def test_withdraw_records_note_and_emits(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(repo, job_id=job.id, status=CandidateStatus.INTERVIEWING)

    withdrawn = await service.withdraw_candidate(
        identity(), candidate.id, CandidateDecision(note="Took another offer"), NOW
    )

    assert withdrawn.status is CandidateStatus.WITHDRAWN
    assert withdrawn.stage_note == "Took another offer"
    assert withdrawn.rejected_from is None
    assert outbox.envelopes[-1]["event_type"] == "candidate.withdrawn"
    assert outbox.envelopes[-1]["data"] == {
        "candidate_id": str(candidate.id),
        "job_id": str(job.id),
        "withdrawn_from": "interviewing",
    }
    assert audit.entries[-1].changes == {
        "status": {"old": "interviewing", "new": "withdrawn"},
        "stage_note": "changed",
    }
    assert "Took another offer" not in str(audit.entries[-1].changes)

    with pytest.raises(CandidateNotOpenError):
        await service.withdraw_candidate(
            identity(), candidate.id, CandidateDecision(), NOW
        )


async def test_update_candidate_audits_pii_markers_and_source_values(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = await create_candidate(
        service, job.id, phone="+1 555 0100", notes="First note"
    )

    updated = await service.update_candidate(
        identity(),
        candidate.id,
        CandidateUpdate(
            first_name="Augusta",
            email="ada.lovelace@example.test",
            phone=None,
            notes="Second note",
            source=CandidateSource.AGENCY,
        ),
        NOW,
    )

    assert updated.first_name == "Augusta"
    assert updated.email == "ada.lovelace@example.test"
    assert updated.phone is None
    assert updated.notes == "Second note"
    assert updated.source is CandidateSource.AGENCY
    assert updated.updated_at == NOW

    assert audit.entries[-1].action == "candidate.updated"
    changes = audit.entries[-1].changes or {}
    assert changes["first_name"] == "changed"
    assert changes["email"] == "changed"
    assert changes["phone"] == "changed"
    assert changes["notes"] == "changed"
    assert changes["source"] == {"old": "referral", "new": "agency"}
    rendered = str(changes)
    for pii in ("Augusta", "ada.lovelace", "555", "Second note"):
        assert pii not in rendered


async def test_update_candidate_without_changes_is_a_noop(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(repo, job_id=job.id)

    updated = await service.update_candidate(
        identity(), candidate.id, CandidateUpdate(), NOW
    )

    assert updated == candidate
    assert audit.entries == []
    assert "update_candidate" not in repo.calls


async def test_update_candidate_email_guard_and_missing_id(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    first = seed_candidate(repo, job_id=job.id, email="first@example.test")
    second = seed_candidate(repo, job_id=job.id, email="second@example.test")

    with pytest.raises(CandidateAlreadyAppliedError):
        await service.update_candidate(
            identity(),
            second.id,
            CandidateUpdate(email="first@example.test"),
            NOW,
        )
    with pytest.raises(CandidateNotFoundError):
        await service.update_candidate(
            identity(), MISSING, CandidateUpdate(first_name="X"), NOW
        )
    # The first candidate may keep its own email.
    same = await service.update_candidate(
        identity(),
        first.id,
        CandidateUpdate(email="first@example.test"),
        NOW,
    )
    assert same.email == "first@example.test"


async def test_candidate_reads_scope_by_interview_assignment(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    assigned = seed_candidate(repo, job_id=job.id, email="assigned@example.test")
    other = seed_candidate(repo, job_id=job.id, email="other@example.test")

    detail = await service.get_candidate(identity(), assigned.id)
    assert detail.id == assigned.id
    assert detail.interviews == []

    with pytest.raises(CandidateNotFoundError):
        await service.get_candidate(identity(tenant_id=TENANT_B), assigned.id)
    with pytest.raises(CandidateNotFoundError):
        await service.get_candidate(identity(), MISSING)

    for role in (Role.EMPLOYEE, Role.FINANCE):
        with pytest.raises(RecruitmentDeniedError):
            await service.get_candidate(identity(role=role), assigned.id)

    manager = identity(role=Role.MANAGER)
    with pytest.raises(CandidateNotFoundError):
        await service.get_candidate(manager, assigned.id)
    empty = await service.list_candidates(
        manager, job_id=None, status=None, q=None, page=PageParams()
    )
    assert empty.items == []
    assert empty.page.total_items == 0

    repo.visible_candidates[(TENANT_A, EMPLOYEE)] = {assigned.id}
    assert (await service.get_candidate(manager, assigned.id)).id == assigned.id
    with pytest.raises(CandidateNotFoundError):
        await service.get_candidate(manager, other.id)
    scoped = await service.list_candidates(
        manager, job_id=None, status=None, q=None, page=PageParams()
    )
    assert [item.id for item in scoped.items] == [assigned.id]

    for role in (Role.EMPLOYEE, Role.FINANCE):
        with pytest.raises(RecruitmentDeniedError):
            await service.list_candidates(
                identity(role=role),
                job_id=None,
                status=None,
                q=None,
                page=PageParams(),
            )


async def test_list_candidates_filters_q_status_job_and_pagination(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    other_job = seed_job(repo, status=JobStatus.OPEN, title="Designer")
    first = seed_candidate(
        repo,
        job_id=job.id,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.test",
        status=CandidateStatus.INTERVIEWING,
        created_at=datetime(2026, 9, 1),
    )
    second = seed_candidate(
        repo,
        job_id=job.id,
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.test",
        created_at=datetime(2026, 9, 2),
    )
    third = seed_candidate(
        repo,
        job_id=other_job.id,
        first_name="Katherine",
        last_name="Johnson",
        email="kj@example.test",
        created_at=datetime(2026, 9, 3),
    )
    page = PageParams()

    everything = await service.list_candidates(
        identity(), job_id=None, status=None, q=None, page=page
    )
    assert [item.id for item in everything.items] == [third.id, second.id, first.id]

    by_job = await service.list_candidates(
        identity(), job_id=job.id, status=None, q=None, page=page
    )
    assert [item.id for item in by_job.items] == [second.id, first.id]

    by_status = await service.list_candidates(
        identity(),
        job_id=None,
        status=CandidateStatus.INTERVIEWING,
        q=None,
        page=page,
    )
    assert [item.id for item in by_status.items] == [first.id]

    by_first = await service.list_candidates(
        identity(), job_id=None, status=None, q="GRACE", page=page
    )
    assert [item.id for item in by_first.items] == [second.id]
    by_last = await service.list_candidates(
        identity(), job_id=None, status=None, q="lovelace", page=page
    )
    assert [item.id for item in by_last.items] == [first.id]

    paged = await service.list_candidates(
        identity(), job_id=None, status=None, q=None, page=PageParams(2, 2)
    )
    assert [item.id for item in paged.items] == [first.id]
    assert paged.page.total_pages == 2

    with pytest.raises(JobNotFoundError):
        await service.list_candidates(
            identity(), job_id=MISSING, status=None, q=None, page=page
        )
