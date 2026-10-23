"""Integration test harness — real PostgreSQL (`postgres-test`, :5433).

`tests/conftest.py` sets `DATABASE_URL` from `DATABASE_TEST_URL` before any
`app.*` import; this module upgrades the schema once per session via Alembic
and truncates all tables between tests. Fixtures: a shared `session`,
fully-wired domain services, and `client_factory(identity)` for in-process
HTTP calls.

Identity is injected through the `get_identity` dependency override — the dev
headers are deleted, not flagged (`.ai/features/identity-access.md`,
decision 5). `client_factory()` is the tenant-A admin; pass an explicit
`identity(...)` to switch tenant/role/employee link, or `client_factory(None)`
to leave the real session resolver in place (no cookie → `401`).

Loop scope is session-wide (pyproject `asyncio_default_*_loop_scope`) so the
global engine/pool is created and used inside one event loop.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.dependencies import (
    get_attendance_hours_port,
    get_identity,
    get_leave_days_port,
    get_overtime_hours_port,
)
from app.domains.employees.service import EmployeeService
from app.domains.notifications.service import NotificationService
from app.domains.organizations.service import (
    DepartmentService,
    LocationService,
    PositionService,
)
from app.domains.payroll.service import PayrollService
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.employee_usage import PostgresEmployeeUsage
from app.infrastructure.postgres.engine import (
    dispose_engine,
    get_engine,
    get_session_factory,
)
from app.infrastructure.postgres.outbox import PostgresEventOutbox
from app.infrastructure.postgres.recipient_directory import (
    PostgresRecipientDirectory,
)
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
    PostgresEmploymentRepository,
)
from app.infrastructure.postgres.repositories.notifications import (
    PostgresNotificationRepository,
)
from app.infrastructure.postgres.repositories.organizations import (
    PostgresDepartmentRepository,
    PostgresLocationRepository,
    PostgresPositionRepository,
)
from app.infrastructure.postgres.repositories.payroll import (
    PostgresPayrollRepository,
)
from app.infrastructure.postgres.session import get_session
from app.infrastructure.redis.client import dispose_redis
from app.main import app
from app.shared.identity import Identity, Role
from tests.conftest import TEST_DATABASE_URL
from tests.unit.notifications.fakes import (
    RecordingEmailSender,
    RecordingSlackSender,
    RecordingSleeper,
)
from tests.unit.payroll.fakes import (
    FakeAttendanceHoursPort,
    FakeLeaveDaysPort,
    FakeOvertimeHoursPort,
)

API_DIR = Path(__file__).resolve().parents[2]

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
USER = UUID("00000000-0000-4000-8000-000000000031")  # the acting user id
ACTOR = UUID("00000000-0000-4000-8000-000000000002")  # its default employee link

# The pinned wire `User` key set (`.ai/features/identity-access.md` → API
# contract) — shared so the auth and users suites assert one vocabulary.
PINNED_USER_KEYS = {
    "id",
    "tenant_id",
    "email",
    "role",
    "employee_id",
    "active",
    "created_at",
    "updated_at",
}

ALL_TABLES = (
    "payslip_deduction_lines, payslips, payroll_timesheets, payroll_bonuses, "
    "payroll_runs, salary_records, payroll_deduction_lines, payroll_configs, "
    "notification_deliveries, notifications, "
    "leave_balances, leave_requests, leave_types, overtime_requests, "
    "interviews, candidates, jobs, "
    "audit_entries, departments, positions, locations, employees, employments, "
    "shifts, attendance_records, users, sessions, event_outbox"
)

ClientFactory = Callable[..., AsyncClient]


def identity(
    tenant: UUID = TENANT_A,
    role: Role = Role.ADMIN,
    *,
    user_id: UUID = USER,
    employee_id: UUID | None = ACTOR,
) -> Identity:
    """An explicit request identity: acting user id + optional employee link.

    The user id and the employee id are deliberately different values. The
    default acting user is linked to employee `ACTOR`; pass
    `employee_id=None` for an account without an employee link.
    """
    return Identity(
        tenant_id=tenant,
        actor_id=user_id,
        role=role,
        employee_id=employee_id,
    )


DEFAULT_IDENTITY = identity()


def _utc_now() -> datetime:
    """The outbox writer's injected clock (mirrors `app.dependencies`)."""
    return datetime.now(UTC)


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


@pytest.fixture(scope="session", autouse=True)
def _migrate_schema() -> Iterator[None]:
    """Run `alembic upgrade head` once per session against the test database."""
    command.upgrade(alembic_config(TEST_DATABASE_URL), "head")
    yield


@pytest.fixture(scope="session", autouse=True)
async def _engine_lifecycle() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest.fixture(scope="session", autouse=True)
async def _redis_lifecycle() -> AsyncIterator[None]:
    """Close the lazily-created Redis client when the session ends."""
    yield
    await dispose_redis()


@pytest.fixture(autouse=True)
async def _truncate_tables() -> AsyncIterator[None]:
    """Guarantee an empty database for every test (committed rows included)."""
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {ALL_TABLES} CASCADE"))


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as test_session:
        yield test_session
        await test_session.rollback()


@pytest.fixture
async def department_service(session: AsyncSession) -> DepartmentService:
    return DepartmentService(
        repo=PostgresDepartmentRepository(session),
        audit=PostgresAuditWriter(session),
        usage=PostgresEmployeeUsage(session),
    )


@pytest.fixture
async def position_service(session: AsyncSession) -> PositionService:
    return PositionService(
        repo=PostgresPositionRepository(session),
        audit=PostgresAuditWriter(session),
    )


@pytest.fixture
async def location_service(session: AsyncSession) -> LocationService:
    return LocationService(
        repo=PostgresLocationRepository(session),
        audit=PostgresAuditWriter(session),
    )


@pytest.fixture
async def employee_service(session: AsyncSession) -> EmployeeService:
    audit = PostgresAuditWriter(session)
    return EmployeeService(
        repo=PostgresEmployeeRepository(session),
        employments=PostgresEmploymentRepository(session),
        departments=DepartmentService(
            repo=PostgresDepartmentRepository(session),
            audit=audit,
            usage=PostgresEmployeeUsage(session),
        ),
        positions=PositionService(
            repo=PostgresPositionRepository(session), audit=audit
        ),
        locations=LocationService(
            repo=PostgresLocationRepository(session), audit=audit
        ),
        audit=audit,
        outbox=PostgresEventOutbox(session, clock=_utc_now),
    )


@pytest.fixture
async def notification_repository(
    session: AsyncSession,
) -> PostgresNotificationRepository:
    return PostgresNotificationRepository(session)


@pytest.fixture
async def recipient_directory(session: AsyncSession) -> PostgresRecipientDirectory:
    return PostgresRecipientDirectory(session)


@pytest.fixture
def email_sender() -> RecordingEmailSender:
    """Deterministic email fake — providers are never hit in tests."""
    return RecordingEmailSender()


@pytest.fixture
def slack_sender() -> RecordingSlackSender:
    """Deterministic Slack fake — providers are never hit in tests."""
    return RecordingSlackSender()


@pytest.fixture
async def notification_service(
    session: AsyncSession,
    notification_repository: PostgresNotificationRepository,
    email_sender: RecordingEmailSender,
    slack_sender: RecordingSlackSender,
) -> NotificationService:
    """The real delivery service over Postgres, with fake channels and no waits."""
    return NotificationService(
        repo=notification_repository,
        email_sender=email_sender,
        slack_sender=slack_sender,
        commit=session.commit,
        sleep=RecordingSleeper(),
        backoff_base_seconds=0.0,
    )


@pytest.fixture
def payroll_overtime() -> FakeOvertimeHoursPort:
    """Overtime seam fake — payroll is tested before `overtime.md` (P0)."""
    return FakeOvertimeHoursPort()


@pytest.fixture
def payroll_attendance() -> FakeAttendanceHoursPort:
    return FakeAttendanceHoursPort()


@pytest.fixture
def payroll_leave_days() -> FakeLeaveDaysPort:
    return FakeLeaveDaysPort()


@pytest.fixture
def payroll_seams(
    payroll_overtime: FakeOvertimeHoursPort,
    payroll_attendance: FakeAttendanceHoursPort,
    payroll_leave_days: FakeLeaveDaysPort,
) -> Iterator[
    tuple[
        FakeOvertimeHoursPort,
        FakeAttendanceHoursPort,
        FakeLeaveDaysPort,
    ]
]:
    """Override the API's payroll seam ports with deterministic fakes.

    The real attendance/leave adapters are exercised by their own suites; T6/T7
    need controlled time facts and no overtime table yet (ADR 0018 decision 7).
    """
    app.dependency_overrides[get_overtime_hours_port] = lambda: payroll_overtime
    app.dependency_overrides[get_attendance_hours_port] = lambda: payroll_attendance
    app.dependency_overrides[get_leave_days_port] = lambda: payroll_leave_days
    yield payroll_overtime, payroll_attendance, payroll_leave_days
    app.dependency_overrides.pop(get_overtime_hours_port, None)
    app.dependency_overrides.pop(get_attendance_hours_port, None)
    app.dependency_overrides.pop(get_leave_days_port, None)


@pytest.fixture
async def payroll_service(
    session: AsyncSession,
    payroll_overtime: FakeOvertimeHoursPort,
    payroll_attendance: FakeAttendanceHoursPort,
    payroll_leave_days: FakeLeaveDaysPort,
) -> PayrollService:
    """The real service over the test Postgres session, with fake seam ports."""
    return PayrollService(
        repo=PostgresPayrollRepository(session),
        audit=PostgresAuditWriter(session),
        outbox=PostgresEventOutbox(session, clock=_utc_now),
        overtime=payroll_overtime,
        attendance=payroll_attendance,
        leave_days=payroll_leave_days,
    )


async def create_org_refs(
    client: AsyncClient, *, suffix: str = ""
) -> tuple[str, str, str]:
    """Create one department/position/location via HTTP; return their ids."""
    department = (
        await client.post("/api/v1/departments", json={"name": f"Engineering{suffix}"})
    ).json()
    position = (
        await client.post("/api/v1/positions", json={"title": f"Engineer{suffix}"})
    ).json()
    location = (
        await client.post(
            "/api/v1/locations", json={"name": f"HQ{suffix}", "timezone": "UTC"}
        )
    ).json()
    return department["id"], position["id"], location["id"]


def employee_payload(
    refs: tuple[str, str, str],
    *,
    first_name: str = "Ada",
    last_name: str = "Lovelace",
    email: str = "ada@example.test",
    manager_id: str | None = None,
    employee_code: str | None = None,
    hired_at: str = "2026-01-05",
    **extra: object,
) -> dict[str, object]:
    department_id, position_id, location_id = refs
    payload: dict[str, object] = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "department_id": department_id,
        "position_id": position_id,
        "location_id": location_id,
        "employment_type": "full_time",
        "hired_at": hired_at,
    }
    if manager_id is not None:
        payload["manager_id"] = manager_id
    if employee_code is not None:
        payload["employee_code"] = employee_code
    payload.update(extra)
    return payload


@pytest.fixture
async def client_factory(session: AsyncSession) -> AsyncIterator[ClientFactory]:
    """Build in-process HTTP clients sharing the test session.

    Each client injects its identity through the `get_identity` dependency
    override (the dev headers no longer exist). Omit the argument for the
    tenant-A admin; pass `None` to leave the real session resolver in place
    (a request without a valid `wp_session` cookie is then `401`).
    """

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override_get_session

    def factory(identity: Identity | None = DEFAULT_IDENTITY) -> AsyncClient:
        if identity is None:
            app.dependency_overrides.pop(get_identity, None)
        else:
            app.dependency_overrides[get_identity] = lambda: identity
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        )

    try:
        yield factory
    finally:
        app.dependency_overrides.clear()
