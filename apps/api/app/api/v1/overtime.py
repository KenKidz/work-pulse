"""Overtime routes: the seven routes pinned in `.ai/features/overtime.md`.

Thin by rule: identity → role policy (decide routes) → service; `now` is taken
at this edge only (`_now()` is the single wall-clock read, every service method
takes it as its last parameter). operationIds are pinned by T1 and drive the
orval-generated web hooks.

Creating, listing and withdrawing are own-only operations: the service resolves
the actor's employee link (`identity.employee_id`) and applies the visibility
matrix, so those routes carry no role guard. Approve and verify have a role
guard here *and* inside the service — a direct caller cannot bypass the step
matrix. Reject's step gate depends on the row's observed source state, so it
lives only in the service (from `requested` it is the manager step; from
`manager_approved`/`hr_verified` the HR step). Self-decision stays a
service-level `403` because only the service knows the request's owner.
"""

from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.dependencies import IdentityDep, OvertimeServiceDep
from app.domains.overtime.policies import ensure_can_approve, ensure_can_verify
from app.domains.overtime.schemas import (
    OvertimeDecision,
    OvertimeRequest,
    OvertimeRequestCreate,
    OvertimeRequestListPage,
    OvertimeRequestStatus,
)
from app.shared.pagination import PageParamsDep

router = APIRouter()


def _now() -> datetime:
    """The single place wall-clock time is read (time is a parameter below)."""
    return datetime.now(UTC)


@router.post(
    "/overtime/requests",
    response_model=OvertimeRequest,
    operation_id="createOvertimeRequest",
    status_code=201,
)
async def create_overtime_request(
    payload: OvertimeRequestCreate,
    identity: IdentityDep,
    service: OvertimeServiceDep,
) -> OvertimeRequest:
    """Submit a claim for the actor's employee link — always `requested`."""
    return await service.create_request(identity, payload, _now())


@router.get(
    "/overtime/requests",
    response_model=OvertimeRequestListPage,
    operation_id="listOvertimeRequests",
)
async def list_overtime_requests(
    identity: IdentityDep,
    service: OvertimeServiceDep,
    page: PageParamsDep,
    employee_id: UUID | None = Query(default=None),
    status: OvertimeRequestStatus | None = Query(default=None),
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
) -> OvertimeRequestListPage:
    """Requests scoped to the actor's visibility (## Roles & access)."""
    return await service.list_requests(
        identity,
        employee_id=employee_id,
        status=status,
        from_date=from_,
        to_date=to,
        page=page,
    )


@router.get(
    "/overtime/requests/{id}",
    response_model=OvertimeRequest,
    operation_id="getOvertimeRequest",
)
async def get_overtime_request(
    id: UUID,
    identity: IdentityDep,
    service: OvertimeServiceDep,
) -> OvertimeRequest:
    """Request detail; unknown, cross-tenant and out-of-scope ids are `404`."""
    return await service.get_request(identity, id)


@router.post(
    "/overtime/requests/{id}/approve",
    response_model=OvertimeRequest,
    operation_id="approveOvertimeRequest",
)
async def approve_overtime_request(
    id: UUID,
    payload: OvertimeDecision,
    identity: IdentityDep,
    service: OvertimeServiceDep,
) -> OvertimeRequest:
    """Manager step: `requested → manager_approved`."""
    ensure_can_approve(identity)
    return await service.approve(identity, id, payload, _now())


@router.post(
    "/overtime/requests/{id}/verify",
    response_model=OvertimeRequest,
    operation_id="verifyOvertimeRequest",
)
async def verify_overtime_request(
    id: UUID,
    payload: OvertimeDecision,
    identity: IdentityDep,
    service: OvertimeServiceDep,
) -> OvertimeRequest:
    """HR step: `manager_approved → hr_verified`, hours unchanged."""
    ensure_can_verify(identity)
    return await service.verify(identity, id, payload, _now())


@router.post(
    "/overtime/requests/{id}/reject",
    response_model=OvertimeRequest,
    operation_id="rejectOvertimeRequest",
)
async def reject_overtime_request(
    id: UUID,
    payload: OvertimeDecision,
    identity: IdentityDep,
    service: OvertimeServiceDep,
) -> OvertimeRequest:
    """Reject from any open state, recording the observed `rejected_from`."""
    return await service.reject(identity, id, payload, _now())


@router.post(
    "/overtime/requests/{id}/withdraw",
    response_model=OvertimeRequest,
    operation_id="withdrawOvertimeRequest",
)
async def withdraw_overtime_request(
    id: UUID,
    identity: IdentityDep,
    service: OvertimeServiceDep,
) -> OvertimeRequest:
    """Withdraw the actor's own open pre-verification request."""
    return await service.withdraw(identity, id, _now())
