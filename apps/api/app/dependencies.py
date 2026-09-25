"""Shared FastAPI dependencies.

Providers for request-scoped resources live here. Routers receive them through
their signatures; domains never construct infrastructure clients themselves.
This module is the composition root: session → repositories → audit writer →
usage port → domain services.

    from app.dependencies import DepartmentServiceDep, SessionDep, SettingsDep
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.domains.organizations.repository import NullDepartmentUsage
from app.domains.organizations.service import (
    DepartmentService,
    LocationService,
    PositionService,
)
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.repositories.organizations import (
    PostgresDepartmentRepository,
    PostgresLocationRepository,
    PostgresPositionRepository,
)
from app.infrastructure.postgres.session import get_session

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_department_service(session: SessionDep) -> DepartmentService:
    return DepartmentService(
        repo=PostgresDepartmentRepository(session),
        audit=PostgresAuditWriter(session),
        # Replaced by the real usage check when Employees lands (spec Risk 1).
        usage=NullDepartmentUsage(),
    )


def get_position_service(session: SessionDep) -> PositionService:
    return PositionService(
        repo=PostgresPositionRepository(session),
        audit=PostgresAuditWriter(session),
    )


def get_location_service(session: SessionDep) -> LocationService:
    return LocationService(
        repo=PostgresLocationRepository(session),
        audit=PostgresAuditWriter(session),
    )


DepartmentServiceDep = Annotated[DepartmentService, Depends(get_department_service)]
PositionServiceDep = Annotated[PositionService, Depends(get_position_service)]
LocationServiceDep = Annotated[LocationService, Depends(get_location_service)]
