"""Unit tests for the Recruitment interview use cases (T5).

No I/O: the service runs against `FakeRecruitmentRepo`, a recording audit writer
and the recording outbox. Covered:

- schedule on an `interviewing` candidate for an active employee → `scheduled`
  + `interview.scheduled`, with interview notes audited as `"changed"`;
- schedule/update/cancel are management-only; complete is assigned-interviewer
  only (management non-assignee `403`, peer non-assignee `404`, no-standing
  roles `403`);
- wrong-state transitions (`candidate_stage_conflict`,
  `interview_not_scheduled`), unknown/foreign ids and inactive interviewers;
- update (reschedule/re-assign/re-note) and cancel with PII-safe audit values;
- list scope (`mine`, forced manager scope), filters, inclusive date bounds,
  foreign filter ids and pagination;
- `CandidateDetail.interviews` is management-complete and interviewer-scoped.
"""

from datetime import datetime
from uuid import UUID

import pytest

from app.domains.recruitment.exceptions import (
    CandidateNotFoundError,
    CandidateStageConflictError,
    InterviewerNotActiveError,
    InterviewerNotFoundError,
    InterviewNotFoundError,
    InterviewNotScheduledError,
    JobNotFoundError,
    RecruitmentDeniedError,
)
from app.domains.recruitment.schemas import (
    CandidateDecision,
    CandidateStatus,
    InterviewFeedback,
    InterviewStatus,
    InterviewUpdate,
    JobStatus,
)
from app.domains.recruitment.service import RecruitmentService
from app.shared.identity import Identity, Role
from app.shared.pagination import PageParams
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox
from tests.unit.recruitment.conftest import (
    EMPLOYEE,
    INTERVIEW_AT,
    NOW,
    OTHER_EMPLOYEE,
    TENANT_A,
    TENANT_B,
    USER,
    identity,
    schedule_interview,
    seed_candidate,
    seed_interview,
    seed_job,
)
from tests.unit.recruitment.fakes import FakeRecruitmentRepo

MISSING = UUID("00000000-0000-4000-8000-0000000000ff")
UNASSIGNED_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000099")


def _manager(
    employee_id: UUID = EMPLOYEE,
    *,
    tenant_id: UUID = TENANT_A,
) -> Identity:
    return identity(tenant_id=tenant_id, role=Role.MANAGER, employee_id=employee_id)


async def _interviewing_candidate(
    repo: FakeRecruitmentRepo,
    *,
    email: str = "ada@example.test",
):
    repo.interviewers[(TENANT_A, EMPLOYEE)] = True
    repo.interviewers[(TENANT_A, OTHER_EMPLOYEE)] = True
    job = seed_job(repo, status=JobStatus.OPEN)
    candidate = seed_candidate(
        repo,
        job_id=job.id,
        status=CandidateStatus.INTERVIEWING,
        email=email,
    )
    return job, candidate


async def test_schedule_emits_and_audits_without_pii(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    job, candidate = await _interviewing_candidate(repo)

    interview = await schedule_interview(service, candidate.id, notes="Panel room 2")

    assert interview.status is InterviewStatus.SCHEDULED
    assert interview.candidate_id == candidate.id
    assert interview.job_id == job.id
    assert interview.interviewer_id == EMPLOYEE
    assert interview.scheduled_at == INTERVIEW_AT
    assert interview.notes == "Panel room 2"
    assert interview.rating is None
    assert repo.interviews[(TENANT_A, interview.id)] == interview
    # The candidate row is locked for the stage check (concurrent-kind guard).
    assert "lock_candidate" in repo.calls

    assert len(outbox.envelopes) == 1
    envelope = outbox.envelopes[0]
    assert envelope["event_type"] == "interview.scheduled"
    assert envelope["actor_id"] == str(USER)
    assert envelope["data"] == {
        "interview_id": str(interview.id),
        "candidate_id": str(candidate.id),
        "job_id": str(job.id),
        "interviewer_id": str(EMPLOYEE),
    }

    entry = audit.entries[-1]
    assert entry.action == "interview.scheduled"
    assert entry.entity_type == "interview"
    assert entry.entity_id == interview.id
    changes = entry.changes or {}
    assert changes["candidate_id"] == {"old": None, "new": str(candidate.id)}
    assert changes["job_id"] == {"old": None, "new": str(job.id)}
    assert changes["interviewer_id"] == {"old": None, "new": str(EMPLOYEE)}
    assert changes["scheduled_at"] == {
        "old": None,
        "new": INTERVIEW_AT.isoformat(),
    }
    assert changes["status"] == {"old": None, "new": "scheduled"}
    assert changes["notes"] == "changed"
    assert "Panel room 2" not in str(changes)
    assert "Panel room 2" not in str(envelope)


async def test_schedule_allows_a_past_slot_and_a_closed_job(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    """Backfilled interviews and in-flight candidates on a closed job are legal."""
    repo.interviewers[(TENANT_A, EMPLOYEE)] = True
    closed = seed_job(repo, status=JobStatus.CLOSED)
    candidate = seed_candidate(
        repo,
        job_id=closed.id,
        status=CandidateStatus.INTERVIEWING,
    )

    interview = await schedule_interview(
        service,
        candidate.id,
        scheduled_at=datetime(2026, 1, 5, 9, 0, tzinfo=INTERVIEW_AT.tzinfo),
    )

    assert interview.status is InterviewStatus.SCHEDULED
    assert interview.job_id == closed.id


async def test_completion_is_allowed_after_the_interviewer_is_terminated(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    """The conversation happened; only NEW assignments check active status."""
    _, candidate = await _interviewing_candidate(repo)
    interview = await schedule_interview(service, candidate.id)

    repo.interviewers[(TENANT_A, EMPLOYEE)] = False

    completed = await service.complete_interview(
        _manager(),
        interview.id,
        InterviewFeedback(rating=3),
        NOW,
    )
    assert completed.status is InterviewStatus.COMPLETED
    assert completed.rating == 3


async def test_schedule_requires_interviewing_candidate_and_active_interviewer(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    job = seed_job(repo, status=JobStatus.OPEN)
    applied = seed_candidate(repo, job_id=job.id, status=CandidateStatus.APPLIED)
    repo.interviewers[(TENANT_A, EMPLOYEE)] = True
    repo.interviewers[(TENANT_A, OTHER_EMPLOYEE)] = False

    with pytest.raises(CandidateStageConflictError):
        await schedule_interview(service, applied.id)
    with pytest.raises(CandidateNotFoundError):
        await schedule_interview(service, MISSING)
    with pytest.raises(CandidateNotFoundError):
        await schedule_interview(
            service, applied.id, caller=identity(tenant_id=TENANT_B)
        )

    interviewing = seed_candidate(
        repo,
        job_id=job.id,
        status=CandidateStatus.INTERVIEWING,
        email="grace@example.test",
    )
    with pytest.raises(InterviewerNotFoundError):
        await schedule_interview(service, interviewing.id, interviewer_id=MISSING)
    with pytest.raises(InterviewerNotActiveError):
        await schedule_interview(
            service, interviewing.id, interviewer_id=OTHER_EMPLOYEE
        )


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.MANAGER, Role.FINANCE])
async def test_interview_mutations_deny_non_management_roles(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    role: Role,
) -> None:
    job, candidate = await _interviewing_candidate(repo)
    interview = seed_interview(repo, candidate_id=candidate.id, job_id=job.id)
    caller = identity(role=role)

    with pytest.raises(RecruitmentDeniedError):
        await schedule_interview(service, candidate.id, caller=caller)
    with pytest.raises(RecruitmentDeniedError):
        await service.update_interview(
            caller, interview.id, InterviewUpdate(notes="x"), NOW
        )
    with pytest.raises(RecruitmentDeniedError):
        await service.cancel_interview(caller, interview.id, CandidateDecision(), NOW)


async def test_complete_is_assigned_interviewer_only_and_does_not_move_stage(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    job, candidate = await _interviewing_candidate(repo)
    interview = await schedule_interview(service, candidate.id)
    audit.entries.clear()
    outbox.envelopes.clear()

    completed = await service.complete_interview(
        _manager(),
        interview.id,
        InterviewFeedback(rating=4, notes="Strong systems answer"),
        NOW,
    )

    assert completed.status is InterviewStatus.COMPLETED
    assert completed.rating == 4
    assert completed.feedback_notes == "Strong systems answer"
    assert completed.completed_at == NOW
    assert repo.candidates[(TENANT_A, candidate.id)].status is (
        CandidateStatus.INTERVIEWING
    )

    envelope = outbox.envelopes[-1]
    assert envelope["event_type"] == "interview.completed"
    assert envelope["actor_id"] == str(USER)
    assert envelope["data"] == {
        "interview_id": str(interview.id),
        "candidate_id": str(candidate.id),
        "job_id": str(job.id),
        "interviewer_id": str(EMPLOYEE),
        "completed_by": str(USER),
    }
    assert "Strong systems answer" not in str(envelope)

    entry = audit.entries[-1]
    assert entry.action == "interview.completed"
    assert entry.changes == {
        "status": {"old": "scheduled", "new": "completed"},
        "rating": {"old": None, "new": 4},
        "completed_at": {"old": None, "new": NOW.isoformat()},
        "feedback_notes": "changed",
    }
    assert "Strong systems answer" not in str(entry.changes)

    # A second completion is a wrong-state transition, never a second event.
    with pytest.raises(InterviewNotScheduledError):
        await service.complete_interview(
            _manager(),
            interview.id,
            InterviewFeedback(rating=5),
            NOW,
        )
    assert [row["event_type"] for row in outbox.envelopes].count(
        "interview.completed"
    ) == 1


async def test_complete_permission_ladder(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    job, candidate = await _interviewing_candidate(repo)
    interview = await schedule_interview(service, candidate.id)

    # HR with management standing but not the assignee: 403.
    with pytest.raises(RecruitmentDeniedError):
        await service.complete_interview(
            identity(role=Role.HR, employee_id=OTHER_EMPLOYEE),
            interview.id,
            InterviewFeedback(rating=4),
            NOW,
        )
    # A peer manager who is not the assignee: 404 (existence is not leaked).
    with pytest.raises(InterviewNotFoundError):
        await service.complete_interview(
            _manager(OTHER_EMPLOYEE),
            interview.id,
            InterviewFeedback(rating=4),
            NOW,
        )
    # Roles with no recruitment standing are refused even if they own an
    # assignment (the security matrix's "Run recruitment" row).
    for role in (Role.EMPLOYEE, Role.FINANCE):
        with pytest.raises(RecruitmentDeniedError):
            await service.complete_interview(
                identity(role=role, employee_id=EMPLOYEE),
                interview.id,
                InterviewFeedback(rating=4),
                NOW,
            )
    with pytest.raises(InterviewNotFoundError):
        await service.complete_interview(
            _manager(),
            MISSING,
            InterviewFeedback(rating=4),
            NOW,
        )


async def test_complete_detects_a_concurrent_reassignment(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    """The guarded update includes the assignment, not just the state."""
    job, candidate = await _interviewing_candidate(repo)
    interview = await schedule_interview(service, candidate.id)

    repo.reassign_next_complete_to = OTHER_EMPLOYEE
    with pytest.raises(InterviewNotFoundError):
        await service.complete_interview(
            _manager(),
            interview.id,
            InterviewFeedback(rating=4),
            NOW,
        )
    assert repo.interviews[(TENANT_A, interview.id)].interviewer_id == (OTHER_EMPLOYEE)
    assert repo.interviews[(TENANT_A, interview.id)].status is (
        InterviewStatus.SCHEDULED
    )

    # A management caller who was reassigned away gets the denied code, not a
    # blanket wrong-state conflict.
    second = seed_interview(repo, candidate_id=candidate.id, job_id=job.id)
    repo.reassign_next_complete_to = OTHER_EMPLOYEE
    with pytest.raises(RecruitmentDeniedError):
        await service.complete_interview(
            identity(),
            second.id,
            InterviewFeedback(rating=4),
            NOW,
        )


async def test_update_reschedules_reassigns_and_audits(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    job, candidate = await _interviewing_candidate(repo)
    interview = await schedule_interview(service, candidate.id)
    later = datetime(2026, 10, 27, 14, 30, tzinfo=INTERVIEW_AT.tzinfo)
    audit.entries.clear()
    event_count = len(outbox.envelopes)

    updated = await service.update_interview(
        identity(),
        interview.id,
        InterviewUpdate(
            interviewer_id=OTHER_EMPLOYEE,
            scheduled_at=later,
            notes="Moved to room 4",
        ),
        NOW,
    )

    assert updated.interviewer_id == OTHER_EMPLOYEE
    assert updated.scheduled_at == later
    assert updated.notes == "Moved to room 4"
    assert updated.status is InterviewStatus.SCHEDULED
    assert updated.updated_at == NOW

    entry = audit.entries[-1]
    assert entry.action == "interview.updated"
    changes = entry.changes or {}
    assert changes["interviewer_id"] == {
        "old": str(EMPLOYEE),
        "new": str(OTHER_EMPLOYEE),
    }
    assert changes["scheduled_at"] == {
        "old": INTERVIEW_AT.isoformat(),
        "new": later.isoformat(),
    }
    assert changes["notes"] == "changed"
    assert "Moved to room 4" not in str(changes)
    # The catalog has no `interview.updated` event: the audit is the trail.
    assert len(outbox.envelopes) == event_count

    # An empty update is a no-op (no audit, no repository write).
    before = len(audit.entries)
    assert (
        await service.update_interview(identity(), interview.id, InterviewUpdate(), NOW)
    ).id == interview.id
    assert len(audit.entries) == before

    # Re-assigning a terminated employee is refused.
    repo.interviewers[(TENANT_A, OTHER_EMPLOYEE)] = False
    with pytest.raises(InterviewerNotActiveError):
        await service.update_interview(
            identity(),
            interview.id,
            InterviewUpdate(interviewer_id=OTHER_EMPLOYEE),
            NOW,
        )
    repo.interviewers[(TENANT_A, OTHER_EMPLOYEE)] = True

    # A concurrent completion/cancellation turns the conditional update into
    # the pinned wrong-state error.
    repo.lose_next_interview_transition = True
    with pytest.raises(InterviewNotScheduledError):
        await service.update_interview(
            identity(), interview.id, InterviewUpdate(notes="late"), NOW
        )

    completed = seed_interview(
        repo,
        candidate_id=candidate.id,
        job_id=job.id,
        status=InterviewStatus.COMPLETED,
    )
    with pytest.raises(InterviewNotScheduledError):
        await service.update_interview(
            identity(), completed.id, InterviewUpdate(notes="x"), NOW
        )
    with pytest.raises(InterviewNotFoundError):
        await service.update_interview(
            identity(), MISSING, InterviewUpdate(notes="x"), NOW
        )


async def test_cancel_records_note_event_and_audit(
    service: RecruitmentService,
    repo: FakeRecruitmentRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    job, candidate = await _interviewing_candidate(repo)
    interview = await schedule_interview(service, candidate.id)
    audit.entries.clear()
    outbox.envelopes.clear()

    cancelled = await service.cancel_interview(
        identity(),
        interview.id,
        CandidateDecision(note="Candidate requested a move"),
        NOW,
    )

    assert cancelled.status is InterviewStatus.CANCELLED
    assert cancelled.cancelled_at == NOW
    assert cancelled.notes == "Candidate requested a move"

    envelope = outbox.envelopes[-1]
    assert envelope["event_type"] == "interview.cancelled"
    assert envelope["data"] == {
        "interview_id": str(interview.id),
        "candidate_id": str(candidate.id),
        "job_id": str(job.id),
        "cancelled_by": str(USER),
    }
    assert "Candidate requested" not in str(envelope)

    entry = audit.entries[-1]
    assert entry.action == "interview.cancelled"
    assert entry.changes == {
        "status": {"old": "scheduled", "new": "cancelled"},
        "cancelled_at": {"old": None, "new": NOW.isoformat()},
        "notes": "changed",
    }
    assert "Candidate requested" not in str(entry.changes)

    with pytest.raises(InterviewNotScheduledError):
        await service.cancel_interview(
            identity(), interview.id, CandidateDecision(), NOW
        )

    repo.interviewers[(TENANT_A, EMPLOYEE)] = True
    other = await schedule_interview(service, candidate.id, scheduled_at=INTERVIEW_AT)
    repo.lose_next_interview_transition = True
    with pytest.raises(InterviewNotScheduledError):
        await service.cancel_interview(identity(), other.id, CandidateDecision(), NOW)
    with pytest.raises(InterviewNotFoundError):
        await service.cancel_interview(identity(), MISSING, CandidateDecision(), NOW)


async def test_list_interviews_scope_filters_and_foreign_ids(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    job, candidate = await _interviewing_candidate(repo)
    other_job = seed_job(repo, status=JobStatus.OPEN, title="Designer")
    other_candidate = seed_candidate(
        repo,
        job_id=other_job.id,
        status=CandidateStatus.INTERVIEWING,
        email="grace@example.test",
    )
    first = seed_interview(
        repo,
        candidate_id=candidate.id,
        job_id=job.id,
        scheduled_at=datetime(2026, 10, 25, 9, 0, tzinfo=INTERVIEW_AT.tzinfo),
    )
    second = seed_interview(
        repo,
        candidate_id=other_candidate.id,
        job_id=other_job.id,
        interviewer_id=OTHER_EMPLOYEE,
        scheduled_at=datetime(2026, 10, 26, 9, 0, tzinfo=INTERVIEW_AT.tzinfo),
    )
    completed = seed_interview(
        repo,
        candidate_id=candidate.id,
        job_id=job.id,
        scheduled_at=datetime(2026, 10, 24, 9, 0, tzinfo=INTERVIEW_AT.tzinfo),
        status=InterviewStatus.COMPLETED,
        rating=5,
    )
    page = PageParams()

    everything = await service.list_interviews(
        identity(),
        candidate_id=None,
        job_id=None,
        status=None,
        mine=False,
        interviewer_id=None,
        scheduled_from=None,
        scheduled_to=None,
        page=page,
    )
    assert [item.id for item in everything.items] == [
        second.id,
        first.id,
        completed.id,
    ]

    by_candidate = await service.list_interviews(
        identity(),
        candidate_id=candidate.id,
        job_id=None,
        status=None,
        mine=False,
        interviewer_id=None,
        scheduled_from=None,
        scheduled_to=None,
        page=page,
    )
    assert [item.id for item in by_candidate.items] == [first.id, completed.id]

    by_job = await service.list_interviews(
        identity(),
        candidate_id=None,
        job_id=other_job.id,
        status=None,
        mine=False,
        interviewer_id=None,
        scheduled_from=None,
        scheduled_to=None,
        page=page,
    )
    assert [item.id for item in by_job.items] == [second.id]

    by_status = await service.list_interviews(
        identity(),
        candidate_id=None,
        job_id=None,
        status=InterviewStatus.COMPLETED,
        mine=False,
        interviewer_id=None,
        scheduled_from=None,
        scheduled_to=None,
        page=page,
    )
    assert [item.id for item in by_status.items] == [completed.id]

    by_interviewer = await service.list_interviews(
        identity(),
        candidate_id=None,
        job_id=None,
        status=None,
        mine=False,
        interviewer_id=OTHER_EMPLOYEE,
        scheduled_from=None,
        scheduled_to=None,
        page=page,
    )
    assert [item.id for item in by_interviewer.items] == [second.id]

    on_day = await service.list_interviews(
        identity(),
        candidate_id=None,
        job_id=None,
        status=None,
        mine=False,
        interviewer_id=None,
        scheduled_from=datetime(2026, 10, 25, tzinfo=INTERVIEW_AT.tzinfo).date(),
        scheduled_to=datetime(2026, 10, 25, tzinfo=INTERVIEW_AT.tzinfo).date(),
        page=page,
    )
    assert [item.id for item in on_day.items] == [first.id]

    # Managers are always forced to their own assignments and may not pass the
    # interviewer filter at all.
    own = await service.list_interviews(
        _manager(),
        candidate_id=None,
        job_id=None,
        status=None,
        mine=False,
        interviewer_id=None,
        scheduled_from=None,
        scheduled_to=None,
        page=page,
    )
    assert [item.id for item in own.items] == [first.id, completed.id]
    with pytest.raises(RecruitmentDeniedError):
        await service.list_interviews(
            _manager(),
            candidate_id=None,
            job_id=None,
            status=None,
            mine=False,
            interviewer_id=OTHER_EMPLOYEE,
            scheduled_from=None,
            scheduled_to=None,
            page=page,
        )

    # An account without an employee link has no "mine" slice (empty, not 403).
    no_link = await service.list_interviews(
        identity(role=Role.HR, employee_id=None),
        candidate_id=None,
        job_id=None,
        status=None,
        mine=True,
        interviewer_id=None,
        scheduled_from=None,
        scheduled_to=None,
        page=page,
    )
    assert no_link.items == []
    assert no_link.page.total_items == 0

    # Foreign/unknown filter ids are 404 with the entity's code.
    with pytest.raises(CandidateNotFoundError):
        await service.list_interviews(
            identity(),
            candidate_id=MISSING,
            job_id=None,
            status=None,
            mine=False,
            interviewer_id=None,
            scheduled_from=None,
            scheduled_to=None,
            page=page,
        )
    with pytest.raises(JobNotFoundError):
        await service.list_interviews(
            identity(),
            candidate_id=None,
            job_id=MISSING,
            status=None,
            mine=False,
            interviewer_id=None,
            scheduled_from=None,
            scheduled_to=None,
            page=page,
        )
    with pytest.raises(InterviewerNotFoundError):
        await service.list_interviews(
            identity(),
            candidate_id=None,
            job_id=None,
            status=None,
            mine=False,
            interviewer_id=MISSING,
            scheduled_from=None,
            scheduled_to=None,
            page=page,
        )

    for role in (Role.EMPLOYEE, Role.FINANCE):
        with pytest.raises(RecruitmentDeniedError):
            await service.list_interviews(
                identity(role=role),
                candidate_id=None,
                job_id=None,
                status=None,
                mine=False,
                interviewer_id=None,
                scheduled_from=None,
                scheduled_to=None,
                page=page,
            )

    paged = await service.list_interviews(
        identity(),
        candidate_id=None,
        job_id=None,
        status=None,
        mine=False,
        interviewer_id=None,
        scheduled_from=None,
        scheduled_to=None,
        page=PageParams(2, 2),
    )
    assert [item.id for item in paged.items] == [completed.id]
    assert paged.page.total_pages == 2


async def test_candidate_detail_interviews_are_scoped(
    service: RecruitmentService, repo: FakeRecruitmentRepo
) -> None:
    job, candidate = await _interviewing_candidate(repo)
    own = seed_interview(repo, candidate_id=candidate.id, job_id=job.id)
    peer = seed_interview(
        repo,
        candidate_id=candidate.id,
        job_id=job.id,
        interviewer_id=OTHER_EMPLOYEE,
    )
    repo.visible_candidates[(TENANT_A, EMPLOYEE)] = {candidate.id}

    management = await service.get_candidate(identity(), candidate.id)
    assert {item.id for item in management.interviews} == {own.id, peer.id}

    scoped = await service.get_candidate(_manager(), candidate.id)
    assert [item.id for item in scoped.interviews] == [own.id]

    # A manager with no interview on this candidate is out of scope. Use an
    # employee with no assignment (OTHER_EMPLOYEE is assigned `peer`).
    with pytest.raises(CandidateNotFoundError):
        await service.get_candidate(_manager(UNASSIGNED_EMPLOYEE), candidate.id)
