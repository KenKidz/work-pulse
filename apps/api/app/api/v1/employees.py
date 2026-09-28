"""Employees routes: the 8 routes pinned in `.ai/features/employees.md`.

Thin by rule: identity → policy → service; `now` is taken at this edge only.
operationIds are pinned and drive the orval-generated web hooks.
`/employees/me` is declared before `/employees/{id}` so "me" is never parsed as
a UUID path parameter.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.dependencies import EmployeeServiceDep
from app.domains.employees.policies import (
    ensure_can_manage_employees,
    ensure_can_read_employees,
    ensure_can_read_self,
)
from app.domains.employees.repository import EmployeeFilters
from app.domains.employees.schemas import (
    CreateEmployee,
    Employee,
    EmployeeDetail,
    EmployeeListPage,
    EmployeeStatus,
    Employment,
    TerminationRequest,
    TransferRequest,
    UpdateEmployee,
)
from app.shared.identity import IdentityDep
from app.shared.pagination import PageParamsDep

router = APIRouter()


def _now() -> datetime:
    """The single place wall-clock time is read (time is a parameter below)."""
    return datetime.now(UTC)


@router.get("/employees", response_model=EmployeeListPage, operation_id="listEmployees")
async def list_employees(
    identity: IdentityDep,
    service: EmployeeServiceDep,
    page: PageParamsDep,
    q: str | None = Query(default=None, max_length=200),
    department_id: UUID | None = Query(default=None),
    position_id: UUID | None = Query(default=None),
    location_id: UUID | None = Query(default=None),
    status: EmployeeStatus | None = Query(default=None),
    manager_id: UUID | None = Query(default=None),
) -> EmployeeListPage:
    ensure_can_read_employees(identity)
    filters = EmployeeFilters(
        q=q,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        status=status,
        manager_id=manager_id,
    )
    return await service.list(identity, filters, page)


@router.post(
    "/employees",
    response_model=Employee,
    operation_id="createEmployee",
    status_code=201,
)
async def create_employee(
    payload: CreateEmployee,
    identity: IdentityDep,
    service: EmployeeServiceDep,
) -> Employee:
    ensure_can_manage_employees(identity)
    return await service.create(identity, payload, _now())


@router.get("/employees/me", response_model=Employee, operation_id="getCurrentEmployee")
async def get_current_employee(
    identity: IdentityDep,
    service: EmployeeServiceDep,
) -> Employee:
    ensure_can_read_self(identity)
    return await service.me(identity)


@router.get(
    "/employees/{id}", response_model=EmployeeDetail, operation_id="getEmployee"
)
async def get_employee(
    id: UUID,
    identity: IdentityDep,
    service: EmployeeServiceDep,
) -> EmployeeDetail:
    ensure_can_read_employees(identity)
    return await service.get_detail(identity, id)


@router.patch("/employees/{id}", response_model=Employee, operation_id="updateEmployee")
async def update_employee(
    id: UUID,
    payload: UpdateEmployee,
    identity: IdentityDep,
    service: EmployeeServiceDep,
) -> Employee:
    ensure_can_manage_employees(identity)
    return await service.update(identity, id, payload, _now())


@router.post(
    "/employees/{id}/transfers",
    response_model=Employment,
    operation_id="transferEmployee",
)
async def transfer_employee(
    id: UUID,
    payload: TransferRequest,
    identity: IdentityDep,
    service: EmployeeServiceDep,
) -> Employment:
    ensure_can_manage_employees(identity)
    return await service.transfer(identity, id, payload, _now())


@router.post(
    "/employees/{id}/termination",
    response_model=Employee,
    operation_id="terminateEmployee",
)
async def terminate_employee(
    id: UUID,
    payload: TerminationRequest,
    identity: IdentityDep,
    service: EmployeeServiceDep,
) -> Employee:
    ensure_can_manage_employees(identity)
    return await service.terminate(identity, id, payload, _now())


@router.get(
    "/employees/{id}/employments",
    response_model=list[Employment],
    operation_id="listEmployments",
)
async def list_employments(
    id: UUID,
    identity: IdentityDep,
    service: EmployeeServiceDep,
) -> list[Employment]:
    ensure_can_read_employees(identity)
    return list(await service.list_employments(identity, id))
