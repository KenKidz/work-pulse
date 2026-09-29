"""Shared FastAPI dependencies.

Providers for request-scoped resources live here. Routers receive them through
their signatures; domains never construct infrastructure clients themselves.
This module is the composition root: session → repositories → audit writer →
usage port → domain services.

    from app.dependencies import DepartmentServiceDep, EmployeeServiceDep,
        SessionDep, SettingsDep
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Cookie, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.domains.attendance.service import AttendanceService, ShiftService
from app.domains.employees.service import EmployeeService
from app.domains.identity.credentials import SESSION_COOKIE_NAME
from app.domains.identity.schemas import User
from app.domains.identity.service import AuthService, UserService
from app.domains.organizations.service import (
    DepartmentService,
    LocationService,
    PositionService,
)
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.employee_link import PostgresEmployeeLink
from app.infrastructure.postgres.employee_usage import PostgresEmployeeUsage
from app.infrastructure.postgres.repositories.attendance import (
    PostgresAttendanceRepository,
    PostgresShiftRepository,
)
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
    PostgresEmploymentRepository,
)
from app.infrastructure.postgres.repositories.identity import (
    PostgresSessionRepository,
    PostgresUserRepository,
)
from app.infrastructure.postgres.repositories.organizations import (
    PostgresDepartmentRepository,
    PostgresLocationRepository,
    PostgresPositionRepository,
)
from app.infrastructure.postgres.session import get_session

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _department_service(session: AsyncSession) -> DepartmentService:
    return DepartmentService(
        repo=PostgresDepartmentRepository(session),
        audit=PostgresAuditWriter(session),
        # The real check replaced NullDepartmentUsage with Employees (T6).
        usage=PostgresEmployeeUsage(session),
    )


def get_department_service(session: SessionDep) -> DepartmentService:
    return _department_service(session)


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


def get_employee_service(session: SessionDep) -> EmployeeService:
    audit = PostgresAuditWriter(session)
    return EmployeeService(
        repo=PostgresEmployeeRepository(session),
        employments=PostgresEmploymentRepository(session),
        # Assignment refs are validated through the Organizations services
        # (conventions: never another domain's internals).
        departments=_department_service(session),
        positions=PositionService(
            repo=PostgresPositionRepository(session), audit=audit
        ),
        locations=LocationService(
            repo=PostgresLocationRepository(session), audit=audit
        ),
        audit=audit,
    )


def get_attendance_service(session: SessionDep) -> AttendanceService:
    return AttendanceService(
        repo=PostgresAttendanceRepository(session),
        # Shift resolution for lateness and `absent` reads through the same
        # repository the ShiftService uses (one adapter, one tenant scope).
        shifts=PostgresShiftRepository(session),
        audit=PostgresAuditWriter(session),
    )


def get_shift_service(session: SessionDep) -> ShiftService:
    audit = PostgresAuditWriter(session)
    return ShiftService(
        repo=PostgresShiftRepository(session),
        # `location_id` references are validated through the Organizations
        # service (conventions: never another domain's internals).
        locations=LocationService(
            repo=PostgresLocationRepository(session), audit=audit
        ),
        audit=audit,
    )


def get_auth_service(session: SessionDep, settings: SettingsDep) -> AuthService:
    return AuthService(
        users=PostgresUserRepository(session),
        sessions=PostgresSessionRepository(session),
        audit=PostgresAuditWriter(session),
        session_ttl=timedelta(days=settings.session_ttl_days),
    )


def get_user_service(session: SessionDep) -> UserService:
    return UserService(
        users=PostgresUserRepository(session),
        sessions=PostgresSessionRepository(session),
        links=PostgresEmployeeLink(session),
        audit=PostgresAuditWriter(session),
    )


DepartmentServiceDep = Annotated[DepartmentService, Depends(get_department_service)]
PositionServiceDep = Annotated[PositionService, Depends(get_position_service)]
LocationServiceDep = Annotated[LocationService, Depends(get_location_service)]
EmployeeServiceDep = Annotated[EmployeeService, Depends(get_employee_service)]
AttendanceServiceDep = Annotated[AttendanceService, Depends(get_attendance_service)]
ShiftServiceDep = Annotated[ShiftService, Depends(get_shift_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]

# The raw session token from the `wp_session` cookie — `None` when absent.
# Resolution and the 401 live in `AuthService` (one failure path).
SessionTokenDep = Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)]


async def get_current_user(
    service: AuthServiceDep, token: SessionTokenDep = None
) -> User:
    """Session cookie → the authenticated user; fail closed (401)."""
    return await service.me(token, datetime.now(UTC))


CurrentUserDep = Annotated[User, Depends(get_current_user)]
