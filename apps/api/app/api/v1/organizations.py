"""Organizations routes: departments, positions, locations (18 routes).

Thin by rule: identity → policy → service; `now` is taken at this edge only.
operationIds are pinned in `.ai/features/organizations.md` and drive the
orval-generated web hooks.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.dependencies import (
    DepartmentServiceDep,
    LocationServiceDep,
    PositionServiceDep,
)
from app.domains.organizations.policies import ensure_can_manage, ensure_can_read
from app.domains.organizations.repository import (
    DepartmentFilters,
    LocationFilters,
    PositionFilters,
)
from app.domains.organizations.schemas import (
    ArchiveRequest,
    Department,
    DepartmentCreate,
    DepartmentListPage,
    DepartmentUpdate,
    Location,
    LocationCreate,
    LocationListPage,
    LocationUpdate,
    Position,
    PositionCreate,
    PositionListPage,
    PositionUpdate,
)
from app.shared.identity import IdentityDep
from app.shared.pagination import PageParamsDep

router = APIRouter()


def _now() -> datetime:
    """The single place wall-clock time is read (time is a parameter below)."""
    return datetime.now(UTC)


# --- Departments -------------------------------------------------------------


@router.get(
    "/departments", response_model=DepartmentListPage, operation_id="listDepartments"
)
async def list_departments(
    identity: IdentityDep,
    service: DepartmentServiceDep,
    page: PageParamsDep,
    q: str | None = Query(default=None, max_length=200),
    parent_id: UUID | None = Query(default=None),
    include_archived: bool = Query(default=False),
) -> DepartmentListPage:
    ensure_can_read(identity)
    filters = DepartmentFilters(
        q=q, parent_id=parent_id, include_archived=include_archived
    )
    return await service.list(identity, filters, page)


@router.post(
    "/departments",
    response_model=Department,
    operation_id="createDepartment",
    status_code=201,
)
async def create_department(
    payload: DepartmentCreate,
    identity: IdentityDep,
    service: DepartmentServiceDep,
) -> Department:
    ensure_can_manage(identity)
    return await service.create(identity, payload, _now())


@router.get(
    "/departments/{id}",
    response_model=Department,
    operation_id="getDepartment",
)
async def get_department(
    id: UUID,
    identity: IdentityDep,
    service: DepartmentServiceDep,
) -> Department:
    ensure_can_read(identity)
    return await service.get(identity, id)


@router.patch(
    "/departments/{id}",
    response_model=Department,
    operation_id="updateDepartment",
)
async def update_department(
    id: UUID,
    payload: DepartmentUpdate,
    identity: IdentityDep,
    service: DepartmentServiceDep,
) -> Department:
    ensure_can_manage(identity)
    return await service.update(identity, id, payload, _now())


@router.post(
    "/departments/{id}/archive",
    response_model=Department,
    operation_id="archiveDepartment",
)
async def archive_department(
    id: UUID,
    identity: IdentityDep,
    service: DepartmentServiceDep,
    payload: ArchiveRequest | None = None,
) -> Department:
    ensure_can_manage(identity)
    return await service.archive(identity, id, payload, _now())


@router.post(
    "/departments/{id}/restore",
    response_model=Department,
    operation_id="restoreDepartment",
)
async def restore_department(
    id: UUID,
    identity: IdentityDep,
    service: DepartmentServiceDep,
) -> Department:
    ensure_can_manage(identity)
    return await service.restore(identity, id, _now())


# --- Positions ---------------------------------------------------------------


@router.get("/positions", response_model=PositionListPage, operation_id="listPositions")
async def list_positions(
    identity: IdentityDep,
    service: PositionServiceDep,
    page: PageParamsDep,
    q: str | None = Query(default=None, max_length=200),
    include_archived: bool = Query(default=False),
) -> PositionListPage:
    ensure_can_read(identity)
    filters = PositionFilters(q=q, include_archived=include_archived)
    return await service.list(identity, filters, page)


@router.post(
    "/positions",
    response_model=Position,
    operation_id="createPosition",
    status_code=201,
)
async def create_position(
    payload: PositionCreate,
    identity: IdentityDep,
    service: PositionServiceDep,
) -> Position:
    ensure_can_manage(identity)
    return await service.create(identity, payload, _now())


@router.get("/positions/{id}", response_model=Position, operation_id="getPosition")
async def get_position(
    id: UUID,
    identity: IdentityDep,
    service: PositionServiceDep,
) -> Position:
    ensure_can_read(identity)
    return await service.get(identity, id)


@router.patch("/positions/{id}", response_model=Position, operation_id="updatePosition")
async def update_position(
    id: UUID,
    payload: PositionUpdate,
    identity: IdentityDep,
    service: PositionServiceDep,
) -> Position:
    ensure_can_manage(identity)
    return await service.update(identity, id, payload, _now())


@router.post(
    "/positions/{id}/archive",
    response_model=Position,
    operation_id="archivePosition",
)
async def archive_position(
    id: UUID,
    identity: IdentityDep,
    service: PositionServiceDep,
    payload: ArchiveRequest | None = None,
) -> Position:
    ensure_can_manage(identity)
    return await service.archive(identity, id, payload, _now())


@router.post(
    "/positions/{id}/restore",
    response_model=Position,
    operation_id="restorePosition",
)
async def restore_position(
    id: UUID,
    identity: IdentityDep,
    service: PositionServiceDep,
) -> Position:
    ensure_can_manage(identity)
    return await service.restore(identity, id, _now())


# --- Locations ---------------------------------------------------------------


@router.get("/locations", response_model=LocationListPage, operation_id="listLocations")
async def list_locations(
    identity: IdentityDep,
    service: LocationServiceDep,
    page: PageParamsDep,
    q: str | None = Query(default=None, max_length=200),
    include_archived: bool = Query(default=False),
) -> LocationListPage:
    ensure_can_read(identity)
    filters = LocationFilters(q=q, include_archived=include_archived)
    return await service.list(identity, filters, page)


@router.post(
    "/locations",
    response_model=Location,
    operation_id="createLocation",
    status_code=201,
)
async def create_location(
    payload: LocationCreate,
    identity: IdentityDep,
    service: LocationServiceDep,
) -> Location:
    ensure_can_manage(identity)
    return await service.create(identity, payload, _now())


@router.get("/locations/{id}", response_model=Location, operation_id="getLocation")
async def get_location(
    id: UUID,
    identity: IdentityDep,
    service: LocationServiceDep,
) -> Location:
    ensure_can_read(identity)
    return await service.get(identity, id)


@router.patch("/locations/{id}", response_model=Location, operation_id="updateLocation")
async def update_location(
    id: UUID,
    payload: LocationUpdate,
    identity: IdentityDep,
    service: LocationServiceDep,
) -> Location:
    ensure_can_manage(identity)
    return await service.update(identity, id, payload, _now())


@router.post(
    "/locations/{id}/archive",
    response_model=Location,
    operation_id="archiveLocation",
)
async def archive_location(
    id: UUID,
    identity: IdentityDep,
    service: LocationServiceDep,
    payload: ArchiveRequest | None = None,
) -> Location:
    ensure_can_manage(identity)
    return await service.archive(identity, id, payload, _now())


@router.post(
    "/locations/{id}/restore",
    response_model=Location,
    operation_id="restoreLocation",
)
async def restore_location(
    id: UUID,
    identity: IdentityDep,
    service: LocationServiceDep,
) -> Location:
    ensure_can_manage(identity)
    return await service.restore(identity, id, _now())
