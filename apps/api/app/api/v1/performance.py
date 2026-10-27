"""Performance routes: the goal (T5) and review/feedback (T6) operations pinned
in `.ai/features/performance.md`.

Thin by rule: identity → role policy → service; `now` is taken at this edge only
(`_now()` is the single wall-clock read, every service method takes it as its
last parameter). operationIds are pinned by the feature spec and drive the
orval-generated web hooks.

The service also applies the policies (a direct caller cannot bypass the role
gate); the router guards keep the HTTP edge failing closed before any read.
Scope (own/team/tenant) is resolved by the service through the `EmployeeScope`
port, so out-of-scope ids surface as `404`, never `403`. Review/feedback reads
have no router-level gate: the subject must reach the pinned
`feedback_access_denied` and a nominated provider must be able to see the shell,
so per-row standing is resolved by the service.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.dependencies import GoalServiceDep, IdentityDep, ReviewServiceDep
from app.domains.performance.policies import (
    ensure_can_manage_goals,
    ensure_can_run_reviews,
)
from app.domains.performance.schemas import (
    Goal,
    GoalCreate,
    GoalListPage,
    GoalStatus,
    GoalUpdate,
    Review,
    ReviewCreate,
    ReviewFeedback,
    ReviewFeedbackCreate,
    ReviewFeedbackList,
    ReviewListPage,
    ReviewStatus,
    ReviewUpdate,
)
from app.shared.pagination import PageParamsDep

router = APIRouter()


def _now() -> datetime:
    """The single place wall-clock time is read (time is a parameter below)."""
    return datetime.now(UTC)


@router.post(
    "/performance/goals",
    response_model=Goal,
    operation_id="createGoal",
    status_code=201,
)
async def create_goal(
    payload: GoalCreate,
    identity: IdentityDep,
    service: GoalServiceDep,
) -> Goal:
    """Create a goal in `draft` for the payload subject (default: self)."""
    ensure_can_manage_goals(identity)
    return await service.create_goal(identity, payload, _now())


@router.get(
    "/performance/goals",
    response_model=GoalListPage,
    operation_id="listGoals",
)
async def list_goals(
    identity: IdentityDep,
    service: GoalServiceDep,
    page: PageParamsDep,
    employee_id: UUID | None = Query(default=None),
    status: GoalStatus | None = Query(default=None),
) -> GoalListPage:
    """Goals scoped to the actor's visibility (## Roles & access)."""
    return await service.list_goals(
        identity,
        employee_id=employee_id,
        status=status,
        page=page,
    )


@router.get(
    "/performance/goals/{id}",
    response_model=Goal,
    operation_id="getGoal",
)
async def get_goal(
    id: UUID,
    identity: IdentityDep,
    service: GoalServiceDep,
) -> Goal:
    """Goal detail; unknown, cross-tenant and out-of-scope ids are `404`."""
    return await service.get_goal(identity, id)


@router.patch(
    "/performance/goals/{id}",
    response_model=Goal,
    operation_id="updateGoal",
)
async def update_goal(
    id: UUID,
    payload: GoalUpdate,
    identity: IdentityDep,
    service: GoalServiceDep,
) -> Goal:
    """Edit a `draft`/`active` goal; a terminal goal is `409 goal_terminal`."""
    ensure_can_manage_goals(identity)
    return await service.update_goal(identity, id, payload, _now())


@router.post(
    "/performance/goals/{id}/activate",
    response_model=Goal,
    operation_id="activateGoal",
)
async def activate_goal(
    id: UUID,
    identity: IdentityDep,
    service: GoalServiceDep,
) -> Goal:
    """`draft → active`; any other state is `409 goal_not_draft`."""
    ensure_can_manage_goals(identity)
    return await service.activate_goal(identity, id, _now())


@router.post(
    "/performance/goals/{id}/complete",
    response_model=Goal,
    operation_id="completeGoal",
)
async def complete_goal(
    id: UUID,
    identity: IdentityDep,
    service: GoalServiceDep,
) -> Goal:
    """`active → completed`; any other state is `409 goal_not_active`."""
    ensure_can_manage_goals(identity)
    return await service.complete_goal(identity, id, _now())


@router.post(
    "/performance/goals/{id}/cancel",
    response_model=Goal,
    operation_id="cancelGoal",
)
async def cancel_goal(
    id: UUID,
    identity: IdentityDep,
    service: GoalServiceDep,
) -> Goal:
    """`draft|active → cancelled`, terminal; a terminal goal is immutable."""
    ensure_can_manage_goals(identity)
    return await service.cancel_goal(identity, id, _now())


# --- reviews -----------------------------------------------------------------


@router.post(
    "/performance/reviews",
    response_model=Review,
    operation_id="createReview",
    status_code=201,
)
async def create_review(
    payload: ReviewCreate,
    identity: IdentityDep,
    service: ReviewServiceDep,
) -> Review:
    """Start a `draft` review for an active employee other than the actor."""
    ensure_can_run_reviews(identity, payload.employee_id)
    return await service.create_review(identity, payload, _now())


@router.get(
    "/performance/reviews",
    response_model=ReviewListPage,
    operation_id="listReviews",
)
async def list_reviews(
    identity: IdentityDep,
    service: ReviewServiceDep,
    page: PageParamsDep,
    employee_id: UUID | None = Query(default=None),
    status: ReviewStatus | None = Query(default=None),
    feedback_for_me: bool = Query(default=False),
) -> ReviewListPage:
    """Reviews scoped to the actor's visibility; `feedback_for_me` is the
    nominated-provider worklist."""
    return await service.list_reviews(
        identity,
        employee_id=employee_id,
        status=status,
        feedback_for_me=feedback_for_me,
        page=page,
    )


@router.get(
    "/performance/reviews/{id}",
    response_model=Review,
    operation_id="getReview",
)
async def get_review(
    id: UUID,
    identity: IdentityDep,
    service: ReviewServiceDep,
) -> Review:
    """Review detail through the visibility matrix (subject: completed only)."""
    return await service.get_review(identity, id)


@router.patch(
    "/performance/reviews/{id}",
    response_model=Review,
    operation_id="updateReview",
)
async def update_review(
    id: UUID,
    payload: ReviewUpdate,
    identity: IdentityDep,
    service: ReviewServiceDep,
) -> Review:
    """Edit a `draft` review and/or its nomination set."""
    ensure_can_run_reviews(identity)
    return await service.update_review(identity, id, payload, _now())


@router.post(
    "/performance/reviews/{id}/complete",
    response_model=Review,
    operation_id="completeReview",
)
async def complete_review(
    id: UUID,
    identity: IdentityDep,
    service: ReviewServiceDep,
) -> Review:
    """`draft → completed`; a missing rating is `409 review_incomplete`."""
    ensure_can_run_reviews(identity)
    return await service.complete_review(identity, id, _now())


@router.post(
    "/performance/reviews/{id}/cancel",
    response_model=Review,
    operation_id="cancelReview",
)
async def cancel_review(
    id: UUID,
    identity: IdentityDep,
    service: ReviewServiceDep,
) -> Review:
    """`draft → cancelled`, terminal."""
    ensure_can_run_reviews(identity)
    return await service.cancel_review(identity, id, _now())


@router.post(
    "/performance/reviews/{id}/feedback",
    response_model=ReviewFeedback,
    operation_id="submitReviewFeedback",
    status_code=201,
)
async def submit_review_feedback(
    id: UUID,
    payload: ReviewFeedbackCreate,
    identity: IdentityDep,
    service: ReviewServiceDep,
) -> ReviewFeedback:
    """A nominated provider files their one comment on a draft review."""
    return await service.submit_feedback(identity, id, payload, _now())


@router.get(
    "/performance/reviews/{id}/feedback",
    response_model=ReviewFeedbackList,
    operation_id="listReviewFeedback",
)
async def list_review_feedback(
    id: UUID,
    identity: IdentityDep,
    service: ReviewServiceDep,
) -> ReviewFeedbackList:
    """Feedback on the review: all for author/HR, the caller's own for a peer."""
    return await service.list_review_feedback(identity, id)
