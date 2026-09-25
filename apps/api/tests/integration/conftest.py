"""Integration test harness — real PostgreSQL (`postgres-test`, :5433).

`tests/conftest.py` sets `DATABASE_URL` from `DATABASE_TEST_URL` before any
`app.*` import; this module upgrades the schema once per session via Alembic
and truncates all tables between tests. Fixtures: a shared `session`,
fully-wired domain services, and `client_factory(headers)` for in-process
HTTP calls.

Loop scope is session-wide (pyproject `asyncio_default_*_loop_scope`) so the
global engine/pool is created and used inside one event loop.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from uuid import UUID

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.domains.organizations.repository import NullDepartmentUsage
from app.domains.organizations.service import (
    DepartmentService,
    LocationService,
    PositionService,
)
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.engine import (
    dispose_engine,
    get_engine,
    get_session_factory,
)
from app.infrastructure.postgres.repositories.organizations import (
    PostgresDepartmentRepository,
    PostgresLocationRepository,
    PostgresPositionRepository,
)
from app.infrastructure.postgres.session import get_session
from app.main import app
from app.shared.identity import DevIdentity, Role
from tests.conftest import TEST_DATABASE_URL

API_DIR = Path(__file__).resolve().parents[2]

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
ACTOR = UUID("00000000-0000-4000-8000-000000000002")

ALL_TABLES = "audit_entries, departments, positions, locations"

ClientFactory = Callable[..., AsyncClient]


def identity_headers(
    tenant: UUID = TENANT_A, actor: UUID = ACTOR, role: str = "admin"
) -> dict[str, str]:
    return {"X-Tenant-Id": str(tenant), "X-Actor-Id": str(actor), "X-Role": role}


def dev_identity(tenant: UUID = TENANT_A, role: Role = Role.ADMIN) -> DevIdentity:
    return DevIdentity(tenant_id=tenant, actor_id=ACTOR, role=role)


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
        usage=NullDepartmentUsage(),
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
async def client_factory(session: AsyncSession) -> AsyncIterator[ClientFactory]:
    """Build in-process HTTP clients sharing the test session.

    Omit `headers` for the tenant-A admin dev identity; pass an explicit dict
    (possibly empty) to control identity headers exactly.
    """

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override_get_session

    def factory(headers: dict[str, str] | None = None) -> AsyncClient:
        resolved = identity_headers() if headers is None else headers
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers=resolved,
        )

    try:
        yield factory
    finally:
        app.dependency_overrides.clear()
