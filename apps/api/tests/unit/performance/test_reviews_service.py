"""Unit tests for `ReviewService` — the pinned review/feedback rules (T6).

Every call passes an explicit `NOW`; the service never reads the wall clock.
The fake repository enforces the same tenant scope, list filters, conditional
transitions, nomination churn and unique constraints as the Postgres adapter,
so these tests exercise business logic, not I/O.

Covered here (`.ai/features/performance.md` → T6, acceptance criteria 3–5):
the review lifecycle and its pinned failures, role/scope checks, the never-self
rule, provider nomination/validation, one-shot feedback, the visibility matrix
(subject completed-only, draft 404, provider shell, `feedback_access_denied`),
tenant isolation and the audit/event split (field edits emit no event; PII is
recorded as a marker only).
"""

import json
from uuid import UUID

import pytest

from app.domains.performance.exceptions import (
    EmployeeNotActiveError,
    EmployeeNotFoundError,
    FeedbackAccessDeniedError,
    FeedbackAlreadySubmittedError,
    FeedbackProviderInvalidError,
    PerformanceDeniedError,
    PerformanceValidationError,
    ReviewIncompleteError,
    ReviewNotDraftError,
    ReviewNotFoundError,
)
from app.domains.performance.schemas import (
    Review,
    ReviewCreate,
    ReviewFeedbackCreate,
    ReviewStatus,
    ReviewUpdate,
)
from app.domains.performance.service import ReviewService
from app.shared.identity import Identity, Role
from app.shared.pagination import PageParams
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox
from tests.unit.performance.conftest import (
    EMPLOYEE,
    MANAGER,
    NOW,
    OTHER,
    PEER,
    REPORT,
    TENANT_A,
    TENANT_B,
    USER,
    identity,
    seed_employee,
)
from tests.unit.performance.fakes import FakeEmployeeScope, FakePerformanceRepo

PII_PERIOD = "2026-H1 secret period — pii-review-period"
PII_STRENGTHS = "Great secret strength — pii-review-strengths"
PII_IMPROVEMENTS = "Secret improvement — pii-review-improvements"
PII_COMMENT = "Private peer comment — pii-feedback-comment"

HR_USER = UUID("00000000-0000-4000-8000-000000000032")
ADMIN_USER = UUID("00000000-0000-4000-8000-000000000033")
PEER_USER = UUID("00000000-0000-4000-8000-000000000034")
REPORT_USER = UUID("00000000-0000-4000-8000-000000000035")
EMPLOYEE_USER = UUID("00000000-0000-4000-8000-000000000036")
OTHER_USER = UUID("00000000-0000-4000-8000-000000000037")


@pytest.fixture
def service(
    repo: FakePerformanceRepo,
    scope: FakeEmployeeScope,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> ReviewService:
    return ReviewService(repo=repo, scope=scope, audit=audit, outbox=outbox)


def _world(repo: FakePerformanceRepo) -> None:
    """Five active employees: actor, manager, report, peer, other + author link."""
    seed_employee(repo, employee_id=EMPLOYEE)
    seed_employee(repo, employee_id=MANAGER)
    seed_employee(repo, employee_id=REPORT, manager_id=MANAGER)
    seed_employee(repo, employee_id=PEER)
    seed_employee(repo, employee_id=OTHER)


def _hr() -> Identity:
    """HR author without the subject/author employee collision (link: OTHER)."""
    return identity(role=Role.HR, employee_id=OTHER, actor_id=HR_USER)


def _manager() -> Identity:
    return identity(role=Role.MANAGER, employee_id=MANAGER)


async def _create(
    service: ReviewService,
    *,
    caller: Identity | None = None,
    subject: UUID = REPORT,
    period: str | None = None,
    providers: tuple[UUID, ...] = (),
) -> Review:
    return await service.create_review(
        caller if caller is not None else _hr(),
        ReviewCreate(
            employee_id=subject,
            period=period,
            feedback_provider_ids=list(providers),
        ),
        NOW,
    )


async def _drafted(
    service: ReviewService,
    *,
    caller: Identity | None = None,
    subject: UUID = REPORT,
    providers: tuple[UUID, ...] = (),
    rating: int | None = 4,
) -> Review:
    """Create + (optionally) fill the rating in, matching AC 3's flow."""
    review = await _create(service, caller=caller, subject=subject, providers=providers)
    if rating is not None:
        review = await service.update_review(
            caller if caller is not None else _hr(),
            review.id,
            ReviewUpdate(rating=rating),
            NOW,
        )
    return review


# --- create ------------------------------------------------------------------


async def test_create_is_draft_with_event_and_audit(
    service: ReviewService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    _world(repo)
    manager = _manager()

    review = await service.create_review(
        manager,
        ReviewCreate(
            employee_id=REPORT,
            period=PII_PERIOD,
            feedback_provider_ids=[PEER],
        ),
        NOW,
    )

    assert review.employee_id == REPORT
    assert review.status is ReviewStatus.DRAFT
    assert review.rating is None
    assert review.feedback_provider_ids == [PEER]

    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "performance.review_started"
    assert envelope["tenant_id"] == str(TENANT_A)
    assert envelope["actor_id"] == str(USER)
    assert envelope["data"] == {
        "review_id": str(review.id),
        "employee_id": str(REPORT),
        "status": "draft",
    }
    assert PII_PERIOD not in json.dumps(envelope)

    [entry] = audit.entries
    assert entry.action == "review.started"
    assert entry.entity_type == "review"
    assert entry.entity_id == review.id
    assert entry.changes is not None
    assert entry.changes["employee_id"] == {"old": None, "new": str(REPORT)}
    assert entry.changes["status"] == {"old": None, "new": "draft"}
    assert entry.changes["feedback_provider_ids"] == {
        "old": [],
        "new": [str(PEER)],
    }
    assert entry.changes["period"] == "changed"  # PII: marker only
    assert PII_PERIOD not in json.dumps(entry.changes)


async def test_create_is_never_self_and_never_for_employee_or_finance(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    _world(repo)

    with pytest.raises(PerformanceDeniedError):
        await _create(service, caller=_manager(), subject=MANAGER)
    with pytest.raises(PerformanceDeniedError):
        await _create(
            service,
            caller=identity(role=Role.HR, employee_id=REPORT),
            subject=REPORT,
        )
    with pytest.raises(PerformanceDeniedError):
        await _create(service, caller=identity(role=Role.EMPLOYEE), subject=REPORT)
    with pytest.raises(PerformanceDeniedError):
        await _create(service, caller=identity(role=Role.FINANCE), subject=REPORT)


async def test_create_subject_scope_active_and_foreign_ids(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    _world(repo)

    report_review = await _create(service, caller=_manager())
    assert report_review.employee_id == REPORT

    with pytest.raises(EmployeeNotFoundError):
        await _create(service, caller=_manager(), subject=PEER)
    with pytest.raises(EmployeeNotFoundError):
        await _create(service, subject=UUID(int=77))
    with pytest.raises(EmployeeNotFoundError):
        await _create(
            service,
            caller=identity(tenant_id=TENANT_B, role=Role.HR, employee_id=None),
            subject=REPORT,
        )

    seed_employee(repo, employee_id=PEER, active=False)
    with pytest.raises(EmployeeNotActiveError):
        await _create(service, subject=PEER)


async def test_create_provider_validation_and_dedupe(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    _world(repo)

    with pytest.raises(FeedbackProviderInvalidError):
        await _create(service, providers=(REPORT,))  # the subject
    with pytest.raises(FeedbackProviderInvalidError):
        await _create(service, providers=(OTHER,))  # the author's employee link
    with pytest.raises(EmployeeNotFoundError):
        await _create(service, providers=(UUID(int=99),))  # unknown/foreign

    seed_employee(repo, employee_id=PEER, active=False)
    with pytest.raises(FeedbackProviderInvalidError):
        await _create(service, providers=(PEER,))

    seed_employee(repo, employee_id=PEER)
    review = await _create(service, providers=(PEER, MANAGER, PEER))
    assert review.feedback_provider_ids == [PEER, MANAGER]

    empty = await _create(service, subject=EMPLOYEE)
    assert empty.feedback_provider_ids == []


# --- update ------------------------------------------------------------------


async def test_update_audits_pii_as_markers_only(
    service: ReviewService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    _world(repo)
    review = await _create(service)
    outbox_before = len(outbox.envelopes)

    updated = await service.update_review(
        _hr(),
        review.id,
        ReviewUpdate(
            period=PII_PERIOD,
            rating=5,
            strengths=PII_STRENGTHS,
            improvements=PII_IMPROVEMENTS,
        ),
        NOW,
    )

    assert updated.period == PII_PERIOD
    assert updated.rating == 5
    assert updated.strengths == PII_STRENGTHS
    assert updated.improvements == PII_IMPROVEMENTS
    # Field edits are not transitions: no event envelope.
    assert len(outbox.envelopes) == outbox_before

    [entry] = audit.entries[1:]
    assert entry.action == "review.updated"
    assert entry.changes is not None
    assert set(entry.changes) == {"period", "rating", "strengths", "improvements"}
    assert set(entry.changes.values()) == {"changed"}
    encoded = json.dumps(entry.changes)
    for pii in (PII_PERIOD, PII_STRENGTHS, PII_IMPROVEMENTS):
        assert pii not in encoded


async def test_update_noop_writes_nothing(
    service: ReviewService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    _world(repo)
    review = await _create(service, providers=(PEER,))
    audit_before = len(audit.entries)
    outbox_before = len(outbox.envelopes)

    unchanged = await service.update_review(
        _hr(),
        review.id,
        ReviewUpdate(feedback_provider_ids=[PEER]),
        NOW,
    )

    assert unchanged == review
    assert len(audit.entries) == audit_before
    assert len(outbox.envelopes) == outbox_before


async def test_update_is_author_or_management_and_draft_only(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    _world(repo)
    review = await _drafted(service)

    # The report's manager did not author this review (HR did): no standing.
    with pytest.raises(PerformanceDeniedError):
        await service.update_review(
            _manager(), review.id, ReviewUpdate(period="Q4"), NOW
        )
    with pytest.raises(PerformanceDeniedError):
        await service.update_review(
            identity(role=Role.EMPLOYEE), review.id, ReviewUpdate(period="Q4"), NOW
        )
    with pytest.raises(PerformanceDeniedError):
        await service.update_review(
            identity(role=Role.FINANCE), review.id, ReviewUpdate(period="Q4"), NOW
        )

    completed = await service.complete_review(_hr(), review.id, NOW)
    assert completed.status is ReviewStatus.COMPLETED
    with pytest.raises(ReviewNotDraftError):
        await service.update_review(_hr(), review.id, ReviewUpdate(rating=2), NOW)
    with pytest.raises(ReviewNotDraftError):
        await service.cancel_review(_hr(), review.id, NOW)


async def test_update_cannot_nominate_the_review_author(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    """An HR editor must not slip the original author into the peer set."""
    _world(repo)
    manager = _manager()
    review = await _create(service, caller=manager)
    repo.user_employees[(TENANT_A, USER)] = MANAGER

    with pytest.raises(FeedbackProviderInvalidError):
        await service.update_review(
            _hr(), review.id, ReviewUpdate(feedback_provider_ids=[MANAGER]), NOW
        )

    # Even a stale nomination can never be used by the author themselves.
    repo.reviews[(TENANT_A, review.id)] = review.model_copy(
        update={"feedback_provider_ids": [MANAGER]}
    )
    with pytest.raises(PerformanceDeniedError):
        await service.submit_feedback(
            manager, review.id, ReviewFeedbackCreate(comment="author input"), NOW
        )


async def test_provider_churn_blocks_removing_a_provider_who_filed(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    _world(repo)
    review = await _create(service, providers=(PEER, MANAGER))

    replaced = await service.update_review(
        _hr(), review.id, ReviewUpdate(feedback_provider_ids=[MANAGER]), NOW
    )
    assert replaced.feedback_provider_ids == [MANAGER]

    await service.update_review(
        _hr(), review.id, ReviewUpdate(feedback_provider_ids=[PEER, MANAGER]), NOW
    )
    await service.submit_feedback(
        identity(role=Role.EMPLOYEE, employee_id=PEER),
        review.id,
        ReviewFeedbackCreate(comment=PII_COMMENT),
        NOW,
    )

    with pytest.raises(FeedbackAlreadySubmittedError):
        await service.update_review(
            _hr(), review.id, ReviewUpdate(feedback_provider_ids=[MANAGER]), NOW
        )
    with pytest.raises(FeedbackAlreadySubmittedError):
        await service.update_review(
            _hr(), review.id, ReviewUpdate(feedback_provider_ids=[]), NOW
        )


async def test_lost_race_update_and_churn_map_to_review_not_draft(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    _world(repo)
    review = await _create(service)

    repo.lose_next_transition = True
    with pytest.raises(ReviewNotDraftError):
        await service.update_review(_hr(), review.id, ReviewUpdate(rating=3), NOW)

    repo.lose_next_transition = True
    with pytest.raises(ReviewNotDraftError):
        await service.update_review(
            _hr(), review.id, ReviewUpdate(feedback_provider_ids=[PEER]), NOW
        )


# --- completion / cancellation ----------------------------------------------


async def test_complete_requires_rating_and_emits_event_and_audit(
    service: ReviewService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    _world(repo)
    review = await _create(service)

    with pytest.raises(ReviewIncompleteError):
        await service.complete_review(_hr(), review.id, NOW)

    await service.update_review(_hr(), review.id, ReviewUpdate(rating=4), NOW)
    completed = await service.complete_review(_hr(), review.id, NOW)

    assert completed.status is ReviewStatus.COMPLETED
    assert completed.completed_at == NOW
    assert outbox.envelopes[-1]["event_type"] == "performance.review_completed"
    assert outbox.envelopes[-1]["data"] == {
        "review_id": str(review.id),
        "employee_id": str(REPORT),
        "status": "completed",
    }
    assert audit.entries[-1].action == "review.completed"
    assert audit.entries[-1].changes == {"status": {"old": "draft", "new": "completed"}}

    with pytest.raises(ReviewNotDraftError):
        await service.complete_review(_hr(), review.id, NOW)


async def test_cancel_is_draft_only_and_emits_event(
    service: ReviewService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    _world(repo)
    review = await _create(service)

    cancelled = await service.cancel_review(_hr(), review.id, NOW)

    assert cancelled.status is ReviewStatus.CANCELLED
    assert outbox.envelopes[-1]["event_type"] == "performance.review_cancelled"
    assert audit.entries[-1].action == "review.cancelled"
    assert audit.entries[-1].changes == {"status": {"old": "draft", "new": "cancelled"}}
    with pytest.raises(ReviewNotDraftError):
        await service.cancel_review(_hr(), review.id, NOW)


async def test_lost_race_complete_maps_to_the_pinned_state(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    _world(repo)
    review = await _drafted(service)

    repo.lose_next_transition = True
    with pytest.raises(ReviewNotDraftError):
        await service.complete_review(_hr(), review.id, NOW)

    repo.lose_next_transition = True
    with pytest.raises(ReviewNotDraftError):
        await service.cancel_review(_hr(), review.id, NOW)


# --- feedback ----------------------------------------------------------------


async def test_feedback_is_one_immutable_submission_per_provider(
    service: ReviewService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    _world(repo)
    review = await _create(service, providers=(PEER, MANAGER))
    peer = identity(role=Role.EMPLOYEE, employee_id=PEER)

    feedback = await service.submit_feedback(
        peer, review.id, ReviewFeedbackCreate(comment=PII_COMMENT), NOW
    )

    assert feedback.review_id == review.id
    assert feedback.author_id == PEER
    assert feedback.comment == PII_COMMENT
    assert outbox.envelopes[-1]["event_type"] == "performance.feedback_submitted"
    assert outbox.envelopes[-1]["data"] == {
        "feedback_id": str(feedback.id),
        "review_id": str(review.id),
        "employee_id": str(REPORT),
    }
    assert PII_COMMENT not in json.dumps(outbox.envelopes[-1])

    [entry] = audit.entries[-1:]
    assert entry.action == "feedback.submitted"
    assert entry.entity_type == "feedback"
    assert entry.entity_id == feedback.id
    assert entry.changes is not None
    assert entry.changes["comment"] == "changed"  # PII: marker only
    assert PII_COMMENT not in json.dumps(entry.changes)

    with pytest.raises(FeedbackAlreadySubmittedError):
        await service.submit_feedback(
            peer, review.id, ReviewFeedbackCreate(comment="second"), NOW
        )
    with pytest.raises(PerformanceDeniedError):
        await service.submit_feedback(
            identity(role=Role.EMPLOYEE, employee_id=OTHER),
            review.id,
            ReviewFeedbackCreate(comment="not nominated"),
            NOW,
        )
    with pytest.raises(PerformanceDeniedError):
        await service.submit_feedback(
            identity(role=Role.EMPLOYEE, employee_id=REPORT),  # the subject
            review.id,
            ReviewFeedbackCreate(comment="self"),
            NOW,
        )
    with pytest.raises(PerformanceDeniedError):
        await service.submit_feedback(
            identity(role=Role.HR, employee_id=None),  # no employee link
            review.id,
            ReviewFeedbackCreate(comment="unlinked"),
            NOW,
        )


async def test_feedback_only_while_draft_and_stays_readable_after_cancel(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    _world(repo)
    review = await _create(service, providers=(PEER, MANAGER))
    await service.submit_feedback(
        identity(role=Role.EMPLOYEE, employee_id=PEER),
        review.id,
        ReviewFeedbackCreate(comment=PII_COMMENT),
        NOW,
    )
    await service.cancel_review(_hr(), review.id, NOW)

    with pytest.raises(ReviewNotDraftError):
        await service.submit_feedback(
            _manager(),
            review.id,
            ReviewFeedbackCreate(comment="late"),
            NOW,
        )

    listing = await service.list_review_feedback(_hr(), review.id)
    assert [item.comment for item in listing.items] == [PII_COMMENT]


# --- visibility --------------------------------------------------------------


async def test_get_review_visibility_matrix(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    _world(repo)
    manager = _manager()
    review = await _create(service, caller=manager, providers=(PEER,))
    await service.update_review(
        manager,
        review.id,
        ReviewUpdate(
            rating=5,
            strengths=PII_STRENGTHS,
            improvements=PII_IMPROVEMENTS,
        ),
        NOW,
    )

    # Draft: the subject never sees it; the provider sees only the shell.
    with pytest.raises(ReviewNotFoundError):
        await service.get_review(
            identity(role=Role.EMPLOYEE, employee_id=REPORT, actor_id=REPORT_USER),
            review.id,
        )

    shell = await service.get_review(
        identity(role=Role.EMPLOYEE, employee_id=PEER, actor_id=PEER_USER), review.id
    )
    assert shell.id == review.id
    assert shell.status is ReviewStatus.DRAFT
    assert shell.rating is None
    assert shell.period is None
    assert shell.strengths is None
    assert shell.improvements is None
    assert shell.feedback_provider_ids is None

    # The author and HR/Admin see it in full.
    author_view = await service.get_review(manager, review.id)
    assert author_view.rating == 5
    assert author_view.strengths == PII_STRENGTHS
    assert author_view.feedback_provider_ids == [PEER]
    assert (
        await service.get_review(
            identity(role=Role.ADMIN, actor_id=ADMIN_USER), review.id
        )
    ).rating == 5

    # An unrelated manager and an unrelated employee learn nothing.
    with pytest.raises(ReviewNotFoundError):
        await service.get_review(
            identity(role=Role.MANAGER, employee_id=OTHER, actor_id=OTHER_USER),
            review.id,
        )
    with pytest.raises(ReviewNotFoundError):
        await service.get_review(
            identity(role=Role.EMPLOYEE, employee_id=OTHER, actor_id=OTHER_USER),
            review.id,
        )
    with pytest.raises(ReviewNotFoundError):
        await service.get_review(
            identity(tenant_id=TENANT_B, role=Role.HR, employee_id=None), review.id
        )

    await service.complete_review(manager, review.id, NOW)

    subject_view = await service.get_review(
        identity(role=Role.EMPLOYEE, employee_id=REPORT, actor_id=REPORT_USER),
        review.id,
    )
    assert subject_view.status is ReviewStatus.COMPLETED
    assert subject_view.rating == 5
    assert subject_view.strengths == PII_STRENGTHS
    assert subject_view.feedback_provider_ids is None  # subject-facing

    # Feedback filing is over; the peer no longer needs the shell.
    with pytest.raises(ReviewNotFoundError):
        await service.get_review(
            identity(role=Role.EMPLOYEE, employee_id=PEER, actor_id=PEER_USER),
            review.id,
        )


async def test_list_reviews_scope_and_subject_narrowing(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    _world(repo)
    page = PageParams()
    hr = _hr()
    manager = _manager()

    # Two reviews for the report (one completed), one for the manager, peer,
    # the acting employee and the HR link; the employee has a draft too.
    report_draft = await _drafted(service, caller=hr, rating=None)
    report_done = await _drafted(service, caller=hr)
    await service.complete_review(hr, report_done.id, NOW)
    manager_draft = await _drafted(service, caller=hr, subject=MANAGER, rating=None)
    manager_review = await _drafted(service, caller=hr, subject=MANAGER)
    await service.complete_review(hr, manager_review.id, NOW)
    peer_review = await _drafted(service, caller=hr, subject=PEER, rating=None)
    employee_draft = await _drafted(service, caller=hr, subject=EMPLOYEE, rating=None)
    employee_done = await _drafted(service, caller=hr, subject=EMPLOYEE)
    await service.complete_review(hr, employee_done.id, NOW)

    # Employee sees only their own completed rows, even when asking for drafts.
    employee_list = await service.list_reviews(
        identity(role=Role.EMPLOYEE), status=ReviewStatus.DRAFT, page=page
    )
    assert [item.id for item in employee_list.items] == [employee_done.id]
    assert employee_list.items[0].feedback_provider_ids is None

    finance_list = await service.list_reviews(identity(role=Role.FINANCE), page=page)
    assert [item.id for item in finance_list.items] == [employee_done.id]

    # Manager: direct report in every state + their own completed row only.
    manager_list = await service.list_reviews(manager, page=page)
    assert {item.id for item in manager_list.items} == {
        report_draft.id,
        report_done.id,
        manager_review.id,
    }
    manager_self_draft = await service.list_reviews(
        manager, employee_id=MANAGER, status=ReviewStatus.DRAFT, page=page
    )
    assert manager_self_draft.items == []
    assert manager_draft.id not in {item.id for item in manager_list.items}
    with pytest.raises(ReviewNotFoundError):
        await service.get_review(manager, manager_draft.id)

    # Management sees everything.
    hr_list = await service.list_reviews(hr, page=page)
    assert {item.id for item in hr_list.items} == {
        report_draft.id,
        report_done.id,
        manager_draft.id,
        manager_review.id,
        peer_review.id,
        employee_draft.id,
        employee_done.id,
    }

    with pytest.raises(EmployeeNotFoundError):
        await service.list_reviews(
            identity(role=Role.EMPLOYEE), employee_id=REPORT, page=page
        )
    with pytest.raises(EmployeeNotFoundError):
        await service.list_reviews(manager, employee_id=PEER, page=page)
    with pytest.raises(EmployeeNotFoundError):
        await service.list_reviews(
            identity(tenant_id=TENANT_B, role=Role.HR, employee_id=None),
            employee_id=REPORT,
            page=page,
        )


async def test_management_subject_never_sees_own_non_completed_rows(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    """Management visibility never overrides the subject's draft invisibility."""
    _world(repo)
    admin = identity(role=Role.ADMIN, employee_id=None, actor_id=ADMIN_USER)
    own_draft = await _create(service, caller=admin, subject=OTHER)
    peer_draft = await _create(service, caller=admin, subject=PEER)
    hr_subject = _hr()  # management, employee link = OTHER

    listed = await service.list_reviews(hr_subject, page=PageParams())
    assert {item.id for item in listed.items} == {peer_draft.id}
    own_filter = await service.list_reviews(
        hr_subject, employee_id=OTHER, page=PageParams()
    )
    assert own_filter.items == []
    with pytest.raises(ReviewNotFoundError):
        await service.get_review(hr_subject, own_draft.id)

    own_completed = await _drafted(service, caller=admin, subject=OTHER)
    completed = await service.complete_review(admin, own_completed.id, NOW)

    listed_after = await service.list_reviews(hr_subject, page=PageParams())
    assert {item.id for item in listed_after.items} == {
        peer_draft.id,
        completed.id,
    }
    subject_view = await service.get_review(hr_subject, completed.id)
    assert subject_view.rating == 4
    assert subject_view.feedback_provider_ids is None


async def test_author_keeps_their_review_after_the_subject_leaves_the_team(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    """The author grant is independent of the current manager scope."""
    _world(repo)
    review = await _drafted(service, caller=_manager())

    # The report moves to another manager: no longer in MANAGER's team scope.
    seed_employee(repo, employee_id=REPORT, manager_id=OTHER)

    listed = await service.list_reviews(_manager(), page=PageParams())
    assert [item.id for item in listed.items] == [review.id]


async def test_feedback_worklist_is_draft_only_and_shells_unprivileged_peers(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    _world(repo)
    page = PageParams()
    review = await _drafted(service, providers=(PEER,), rating=5)
    peer = identity(role=Role.EMPLOYEE, employee_id=PEER, actor_id=PEER_USER)

    worklist = await service.list_reviews(peer, feedback_for_me=True, page=page)
    assert [item.id for item in worklist.items] == [review.id]
    [shell] = worklist.items
    assert shell.rating is None
    assert shell.period is None
    assert shell.feedback_provider_ids is None

    await service.complete_review(_hr(), review.id, NOW)
    done = await service.list_reviews(peer, feedback_for_me=True, page=page)
    assert done.items == []

    with pytest.raises(PerformanceValidationError) as exc:
        await service.list_reviews(
            peer,
            feedback_for_me=True,
            status=ReviewStatus.COMPLETED,
            page=page,
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "validation_error"

    # An employee without a link has no worklist rather than an error.
    unlinked = await service.list_reviews(
        identity(role=Role.EMPLOYEE, employee_id=None),
        feedback_for_me=True,
        page=page,
    )
    assert unlinked.items == []


async def test_feedback_worklist_gives_a_manager_full_rows_for_their_team(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    _world(repo)
    review = await _drafted(service, providers=(MANAGER,), rating=5)

    worklist = await service.list_reviews(
        _manager(), feedback_for_me=True, page=PageParams()
    )

    [row] = worklist.items
    assert row.id == review.id
    assert row.rating == 5


async def test_feedback_visibility_matrix(
    service: ReviewService, repo: FakePerformanceRepo
) -> None:
    _world(repo)
    review = await _create(service, providers=(PEER, EMPLOYEE))
    peer = identity(role=Role.EMPLOYEE, employee_id=PEER, actor_id=PEER_USER)
    employee = identity(role=Role.EMPLOYEE, actor_id=EMPLOYEE_USER)
    await service.submit_feedback(
        peer, review.id, ReviewFeedbackCreate(comment=PII_COMMENT), NOW
    )
    await service.submit_feedback(
        employee, review.id, ReviewFeedbackCreate(comment="second"), NOW
    )

    author_view = await service.list_review_feedback(_hr(), review.id)
    assert len(author_view.items) == 2
    admin_view = await service.list_review_feedback(
        identity(role=Role.ADMIN, actor_id=ADMIN_USER), review.id
    )
    assert len(admin_view.items) == 2

    peer_view = await service.list_review_feedback(peer, review.id)
    assert [item.author_id for item in peer_view.items] == [PEER]

    with pytest.raises(FeedbackAccessDeniedError):
        await service.list_review_feedback(
            identity(role=Role.EMPLOYEE, employee_id=REPORT, actor_id=REPORT_USER),
            review.id,
        )  # the subject
    with pytest.raises(FeedbackAccessDeniedError):
        await service.list_review_feedback(
            identity(role=Role.ADMIN, employee_id=REPORT), review.id
        )  # management as the subject still loses
    with pytest.raises(PerformanceDeniedError):
        await service.list_review_feedback(_manager(), review.id)
    with pytest.raises(PerformanceDeniedError):
        await service.list_review_feedback(
            identity(role=Role.EMPLOYEE, employee_id=OTHER, actor_id=OTHER_USER),
            review.id,
        )
    with pytest.raises(ReviewNotFoundError):
        await service.list_review_feedback(
            identity(tenant_id=TENANT_B, role=Role.HR, employee_id=None), review.id
        )
