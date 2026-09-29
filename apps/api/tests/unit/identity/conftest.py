"""Shared fixtures: AuthService/UserService wired to in-memory fakes (no DB)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from uuid_utils.compat import uuid7

from app.domains.identity.credentials import hash_password
from app.domains.identity.schemas import User
from app.domains.identity.service import AuthService, UserService
from app.shared.identity import DevIdentity, Role
from tests.unit.identity.fakes import (
    PLACEHOLDER_HASH,
    FakeEmployeeLinkPort,
    FakeSessionRepo,
    FakeUserRepo,
    RecordingAuditWriter,
)

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
ACTOR = UUID("00000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
SESSION_TTL = timedelta(days=7)
DEFAULT_PASSWORD = "correct horse battery"  # >= 10 chars (pinned minimum)


def identity(tenant_id: UUID = TENANT_A, role: Role = Role.ADMIN) -> DevIdentity:
    return DevIdentity(tenant_id=tenant_id, actor_id=ACTOR, role=role)


@pytest.fixture(scope="session")
async def default_password_hash() -> str:
    """One real argon2 hash for the whole session — hashing is CPU work."""
    return await hash_password(DEFAULT_PASSWORD)


@pytest.fixture
def users() -> FakeUserRepo:
    return FakeUserRepo()


@pytest.fixture
def sessions() -> FakeSessionRepo:
    return FakeSessionRepo()


@pytest.fixture
def links() -> FakeEmployeeLinkPort:
    return FakeEmployeeLinkPort()


@pytest.fixture
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def auth(
    users: FakeUserRepo,
    sessions: FakeSessionRepo,
    audit: RecordingAuditWriter,
) -> AuthService:
    return AuthService(
        users=users, sessions=sessions, audit=audit, session_ttl=SESSION_TTL
    )


@pytest.fixture
def service(
    users: FakeUserRepo,
    sessions: FakeSessionRepo,
    links: FakeEmployeeLinkPort,
    audit: RecordingAuditWriter,
) -> UserService:
    return UserService(users=users, sessions=sessions, links=links, audit=audit)


async def seed_user(
    repo: FakeUserRepo,
    *,
    tenant_id: UUID = TENANT_A,
    email: str = "ada@workpulse.test",
    role: Role = Role.EMPLOYEE,
    employee_id: UUID | None = None,
    active: bool = True,
    password_hash: str = PLACEHOLDER_HASH,
    user_id: UUID | None = None,
) -> User:
    """Seed one credential row; callers that log in pass a real hash."""
    return await repo.add(
        tenant_id,
        user_id or uuid7(),
        email=email,
        role=role,
        employee_id=employee_id,
        password_hash=password_hash,
        active=active,
    )
