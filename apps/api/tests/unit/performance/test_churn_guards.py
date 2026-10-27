"""Unit tests for the churn backstops shared by the performance fakes (T4).

The Postgres adapter enforces the same rules under the review row lock (T6
integration covers those queries); these tests keep the in-memory fake from
masking a service bug during T5–T7 unit work.
"""

from uuid import UUID

import pytest
from uuid_utils.compat import uuid7

from app.domains.performance.exceptions import (
    FeedbackAlreadySubmittedError,
    PerformanceDeniedError,
)
from app.domains.performance.schemas import Review
from tests.unit.performance.conftest import (
    EMPLOYEE,
    NOW,
    OTHER,
    PEER,
    TENANT_A,
    USER,
)
from tests.unit.performance.fakes import FakePerformanceRepo


async def _review(
    repo: FakePerformanceRepo,
    *,
    providers: tuple[UUID, ...] = (PEER,),
    subject: UUID = EMPLOYEE,
) -> Review:
    return await repo.create_review(
        TENANT_A,
        uuid7(),
        employee_id=subject,
        author_id=USER,
        period=None,
        rating=None,
        strengths=None,
        improvements=None,
        feedback_provider_ids=list(providers),
    )


async def test_nominated_provider_can_file_feedback(
    repo: FakePerformanceRepo,
) -> None:
    review = await _review(repo)

    feedback = await repo.create_feedback(
        TENANT_A,
        uuid7(),
        review_id=review.id,
        author_id=PEER,
        comment="helpful peer input",
        now=NOW,
    )

    assert feedback is not None
    assert feedback.author_id == PEER


async def test_non_nominated_employee_cannot_file_feedback(
    repo: FakePerformanceRepo,
) -> None:
    review = await _review(repo)

    with pytest.raises(PerformanceDeniedError):
        await repo.create_feedback(
            TENANT_A,
            uuid7(),
            review_id=review.id,
            author_id=OTHER,
            comment="not nominated",
            now=NOW,
        )


async def test_subject_cannot_file_feedback_even_if_nominated(
    repo: FakePerformanceRepo,
) -> None:
    """The service rejects this nomination; the fake backstops the submission."""
    review = await _review(repo, providers=(EMPLOYEE,))

    with pytest.raises(PerformanceDeniedError):
        await repo.create_feedback(
            TENANT_A,
            uuid7(),
            review_id=review.id,
            author_id=EMPLOYEE,
            comment="self feedback",
            now=NOW,
        )


async def test_removing_a_provider_who_filed_feedback_is_rejected(
    repo: FakePerformanceRepo,
) -> None:
    review = await _review(repo)
    assert (
        await repo.create_feedback(
            TENANT_A,
            uuid7(),
            review_id=review.id,
            author_id=PEER,
            comment="peer input",
            now=NOW,
        )
        is not None
    )

    with pytest.raises(FeedbackAlreadySubmittedError):
        await repo.replace_review_providers(
            TENANT_A, review.id, employee_ids=[], now=NOW
        )

    stored = await repo.get_review(TENANT_A, review.id)
    assert stored is not None
    assert stored.feedback_provider_ids == [PEER]


async def test_removing_a_provider_without_feedback_is_allowed(
    repo: FakePerformanceRepo,
) -> None:
    review = await _review(repo, providers=(PEER, OTHER))

    assert await repo.replace_review_providers(
        TENANT_A, review.id, employee_ids=[OTHER], now=NOW
    )

    stored = await repo.get_review(TENANT_A, review.id)
    assert stored is not None
    assert stored.feedback_provider_ids == [OTHER]


async def test_update_review_rejects_provider_churn_field(
    repo: FakePerformanceRepo,
) -> None:
    """Provider changes must use `replace_review_providers`, like the adapter."""
    review = await _review(repo)

    with pytest.raises(ValueError, match="replace_review_providers"):
        await repo.update_review(
            TENANT_A,
            review.id,
            now=NOW,
            feedback_provider_ids=[],
        )
