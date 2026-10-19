"""Shared FastAPI dependencies.

Providers for request-scoped resources live here. Routers receive them through
their signatures; domains never construct infrastructure clients themselves.
This module is the composition root: session → repositories → audit writer →
usage port → outbox writer → domain services, plus the session-resolved
identity dependency (`get_identity` / `IdentityDep`, ADR 0004's one validation
point).

    from app.dependencies import DepartmentServiceDep, EmployeeServiceDep,
        IdentityDep, SessionDep, SettingsDep
"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.domains.attendance.service import (
    AttendanceService,
    BoardDetail,
    ShiftService,
)
from app.domains.employees.service import EmployeeService
from app.domains.identity.credentials import SESSION_COOKIE_NAME
from app.domains.identity.schemas import User
from app.domains.identity.service import AuthService, UserService
from app.domains.organizations.service import (
    DepartmentService,
    LocationService,
    PositionService,
)
from app.domains.realtime.service import RealtimeService
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.employee_link import PostgresEmployeeLink
from app.infrastructure.postgres.employee_usage import PostgresEmployeeUsage
from app.infrastructure.postgres.engine import get_session_factory
from app.infrastructure.postgres.outbox import PostgresEventOutbox
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
from app.infrastructure.redis.client import get_redis
from app.infrastructure.redis.streams import RedisPresenceStream
from app.shared.errors import UnauthenticatedError
from app.shared.identity import Identity

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SessionFactoryDep = Annotated[
    async_sessionmaker[AsyncSession], Depends(get_session_factory)
]


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
        # The transactional outbox (ADR 0012): the same session, so the row,
        # its audit entry and its event envelope commit together — or not at
        # all. The relay worker publishes committed rows to Kafka.
        outbox=PostgresEventOutbox(session, clock=_utc_now),
    )


def _utc_now() -> datetime:
    """The outbox writer's injected clock (time is a parameter, conventions)."""
    return datetime.now(UTC)


def get_attendance_service(session: SessionDep) -> AttendanceService:
    return AttendanceService(
        repo=PostgresAttendanceRepository(session),
        # Shift resolution for lateness and `absent` reads through the same
        # repository the ShiftService uses (one adapter, one tenant scope).
        shifts=PostgresShiftRepository(session),
        audit=PostgresAuditWriter(session),
        # The transactional outbox (ADR 0012): the same session, so the record,
        # its audit entry and its event envelopes commit together — or not at
        # all. The relay worker publishes committed rows to Kafka; the realtime
        # consumer projects `attendance.*` back onto the wall (ADR 0013) — the
        # write path itself stays bus-free.
        outbox=PostgresEventOutbox(session, clock=_utc_now),
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


class ShortLivedAttendanceBoard:
    """The WS stream's board reads, one session per read.

    A WebSocket handler keeps its dependencies until the socket closes, so a
    request-scoped session here would pin the pooled PostgreSQL connection its
    first query checked out for the viewer's whole session — enough viewers
    would exhaust the pool and stall every other endpoint. Each read opens,
    uses and releases its own session instead (read-only: closing rolls the
    implicit transaction back).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def board_detail(
        self, identity: Identity, board_date: date, now: datetime
    ) -> BoardDetail:
        async with self._session_factory() as session:
            return await get_attendance_service(session).board_detail(
                identity, board_date, now
            )

    async def visible_employee_scope(
        self, identity: Identity
    ) -> frozenset[UUID] | None:
        async with self._session_factory() as session:
            return await get_attendance_service(session).visible_employee_scope(
                identity
            )


def get_realtime_service(session: SessionDep) -> RealtimeService:
    """REST provider — the request-scoped session is released with the response."""
    return RealtimeService(
        # The snapshot IS the attendance board projection (T3): compose the
        # service instead of re-implementing board semantics.
        attendance=get_attendance_service(session),
        # One subscription per connection to the tenant channel — every API
        # process reaches every viewer through Redis, never process memory.
        stream=RedisPresenceStream(get_redis()),
    )


def get_realtime_stream_service(
    session_factory: SessionFactoryDep,
) -> RealtimeService:
    """WS stream provider — short-lived board reads, no held pool connection."""
    return RealtimeService(
        attendance=ShortLivedAttendanceBoard(session_factory),
        stream=RedisPresenceStream(get_redis()),
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
RealtimeServiceDep = Annotated[RealtimeService, Depends(get_realtime_service)]
RealtimeStreamServiceDep = Annotated[
    RealtimeService, Depends(get_realtime_stream_service)
]
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


def _identity_of(user: User) -> Identity:
    """The one session-user → `Identity` mapping (ADR 0010)."""
    return Identity(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        role=user.role,
        employee_id=user.employee_id,
    )


async def get_identity(user: CurrentUserDep) -> Identity:
    """Session cookie → the request identity (ADR 0004's one validation point).

    `actor_id` is the authenticated **user** id; `employee_id` carries the
    optional employee link (employee-scoped flows fail closed on `None`). A
    missing/unknown/expired session or a deactivated account is always
    `401 unauthenticated` — resolved by `AuthService.me` above.
    """
    return _identity_of(user)


IdentityDep = Annotated[Identity, Depends(get_identity)]


async def get_socket_identity(
    settings: SettingsDep,
    session_factory: SessionFactoryDep,
    token: SessionTokenDep = None,
) -> Identity | None:
    """Resolve the WS handshake identity without raising (`None` = denied).

    The WebSocket API has no channel for an upgrade status — browser JS
    surfaces only error/close — so a missing, unknown or expired session
    cannot be a JSON `401` here: it resolves to `None` and the router closes
    before accepting, the same close-before-accept denial as an insufficient
    role. Resolution is the HTTP path's (`get_current_user` + `_identity_of`),
    so REST and WS can never disagree about who is signed in (ADR 0010).

    It runs on its own short-lived session: a WebSocket handler keeps its
    request-scoped dependencies until the socket closes, and holding one here
    would pin a pooled PostgreSQL connection per viewer (the stream's board
    reads use the same short-lived pattern).
    """
    if token is None:
        return None
    async with session_factory() as session:
        try:
            user = await get_current_user(get_auth_service(session, settings), token)
        except UnauthenticatedError:
            return None
        return _identity_of(user)


SocketIdentityDep = Annotated[Identity | None, Depends(get_socket_identity)]
