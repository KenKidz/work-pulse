"""Performance routes: the goal routes pinned in `.ai/features/performance.md`.

Thin by rule: identity → role policy → service; `now` is taken at this edge only
(`_now()` is the single wall-clock read, every service method takes it as its
last parameter). operationIds are pinned by the feature spec and drive the
orval-generated web hooks.

The service also applies the policies (a direct caller cannot bypass the role
gate); the router guards keep the HTTP edge failing closed before any read.
Scope (own/team/tenant) is resolved by the service through the `EmployeeScope`
port, so out-of-scope ids surface as `404`, never `403`.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.dependencies import GoalServiceDep, IdentityDep
from app.domains.performance.policies import ensure_can_manage_goals
from app.domains.performance.schemas import (
    Goal,
    GoalCreate,
    GoalListPage,
    GoalStatus,
    GoalUpdate,
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
