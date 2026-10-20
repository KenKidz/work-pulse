"""Leave routes: the eight routes pinned in `.ai/features/leave.md`.

Thin by rule: identity → policy → service; `now` is taken at this edge only
(`_now()` is the single wall-clock read, every service method takes it as its
last parameter). operationIds are pinned and drive the orval-generated web
hooks.

Requesting, viewing and withdrawing are own-only operations: the service
resolves the actor's employee link (`identity.employee_id`) and applies the
visibility matrix, so there is no role guard on those routes. Deciding has a
role guard here (`ensure_can_decide`) *and* inside the service — a direct
caller cannot bypass it, and self-approval stays a service-level `403` because
only the service knows the request's owner.
"""

from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.dependencies import IdentityDep, LeaveServiceDep
from app.domains.leave.policies import ensure_can_decide
from app.domains.leave.schemas import (
    LeaveBalance,
    LeaveDecision,
    LeaveRequest,
    LeaveRequestCreate,
    LeaveRequestListPage,
    LeaveRequestStatus,
    LeaveType,
)
from app.shared.pagination import PageParamsDep

router = APIRouter()


def _now() -> datetime:
    """The single place wall-clock time is read (time is a parameter below)."""
    return datetime.now(UTC)


@router.post(
    "/leave/requests",
    response_model=LeaveRequest,
    operation_id="createLeaveRequest",
    status_code=201,
)
async def create_leave_request(
    payload: LeaveRequestCreate,
    identity: IdentityDep,
    service: LeaveServiceDep,
) -> LeaveRequest:
    """Submit a request for the actor's employee link — always `PENDING`."""
    return await service.create_request(identity, payload, _now())


@router.get(
    "/leave/requests",
    response_model=LeaveRequestListPage,
    operation_id="listLeaveRequests",
)
async def list_leave_requests(
    identity: IdentityDep,
    service: LeaveServiceDep,
    page: PageParamsDep,
    employee_id: UUID | None = Query(default=None),
    status: LeaveRequestStatus | None = Query(default=None),
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
) -> LeaveRequestListPage:
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
    "/leave/requests/{id}",
    response_model=LeaveRequest,
    operation_id="getLeaveRequest",
)
async def get_leave_request(
    id: UUID,
    identity: IdentityDep,
    service: LeaveServiceDep,
) -> LeaveRequest:
    """Request detail; unknown, cross-tenant and out-of-scope ids are `404`."""
    return await service.get_request(identity, id)


@router.post(
    "/leave/requests/{id}/approve",
    response_model=LeaveRequest,
    operation_id="approveLeaveRequest",
)
async def approve_leave_request(
    id: UUID,
    payload: LeaveDecision,
    identity: IdentityDep,
    service: LeaveServiceDep,
) -> LeaveRequest:
    """Approve a pending request, deducting its balance in one transaction."""
    ensure_can_decide(identity)
    return await service.approve(identity, id, payload, _now())


@router.post(
    "/leave/requests/{id}/reject",
    response_model=LeaveRequest,
    operation_id="rejectLeaveRequest",
)
async def reject_leave_request(
    id: UUID,
    payload: LeaveDecision,
    identity: IdentityDep,
    service: LeaveServiceDep,
) -> LeaveRequest:
    """Reject a pending request — the balance is never touched."""
    ensure_can_decide(identity)
    return await service.reject(identity, id, payload, _now())


@router.post(
    "/leave/requests/{id}/withdraw",
    response_model=LeaveRequest,
    operation_id="withdrawLeaveRequest",
)
async def withdraw_leave_request(
    id: UUID,
    identity: IdentityDep,
    service: LeaveServiceDep,
) -> LeaveRequest:
    """Withdraw the actor's own `PENDING` request — no balance to restore."""
    return await service.withdraw(identity, id, _now())


@router.get(
    "/leave/types",
    response_model=list[LeaveType],
    operation_id="listLeaveTypes",
)
async def list_leave_types(
    identity: IdentityDep,
    service: LeaveServiceDep,
) -> list[LeaveType]:
    """Seeded reference data, provisioned idempotently for the tenant."""
    return list(await service.list_types(identity))


@router.get(
    "/leave/balances",
    response_model=list[LeaveBalance],
    operation_id="listLeaveBalances",
)
async def list_leave_balances(
    identity: IdentityDep,
    service: LeaveServiceDep,
    employee_id: UUID | None = Query(default=None),
    year: int | None = Query(default=None),
) -> list[LeaveBalance]:
    """Balances in the actor's visibility scope, optionally by year."""
    return list(
        await service.list_balances(identity, employee_id=employee_id, year=year)
    )
