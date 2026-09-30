"""Unit tests for the session-resolved request identity (ADR 0004's swap).

`get_identity` turns the authenticated user into the `Identity` routers and
domains consume; `AuthService.me` owns session resolution itself (its rules are
covered in `tests/unit/identity/`). Here we pin the dependency-level mapping
and that every unusable session fails closed with `401 unauthenticated` before
any `Identity` is built.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from uuid_utils.compat import uuid7

from app.dependencies import get_current_user, get_identity
from app.domains.identity.credentials import session_token_hash
from app.domains.identity.schemas import User
from app.domains.identity.service import AuthService
from app.shared.errors import UnauthenticatedError
from app.shared.identity import Identity, Role
from tests.unit.identity.conftest import SESSION_TTL, seed_user
from tests.unit.identity.fakes import (
    FakeSessionRepo,
    FakeUserRepo,
    RecordingAuditWriter,
)

TENANT = UUID("00000000-0000-4000-8000-000000000001")
USER = UUID("00000000-0000-4000-8000-000000000011")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000021")
TOKEN = "session-token-value"
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
# Session TTLs relative to the real wall clock `get_current_user` reads: far
# enough each way that the assertions never depend on the machine's date.
EXPIRED_AT = datetime(2020, 1, 1, tzinfo=UTC)
VALID_UNTIL = datetime(2099, 1, 1, tzinfo=UTC)


def make_service(users: FakeUserRepo, sessions: FakeSessionRepo) -> AuthService:
    return AuthService(
        users=users,
        sessions=sessions,
        audit=RecordingAuditWriter(),
        session_ttl=SESSION_TTL,
    )


def make_user(*, role: Role = Role.HR, employee_id: UUID | None = None) -> User:
    return User(
        id=USER,
        tenant_id=TENANT,
        email="user@workpulse.test",
        role=role,
        employee_id=employee_id,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )


async def test_identity_maps_the_authenticated_user() -> None:
    identity = await get_identity(make_user(role=Role.MANAGER, employee_id=EMPLOYEE))

    assert identity == Identity(
        tenant_id=TENANT,
        actor_id=USER,
        role=Role.MANAGER,
        employee_id=EMPLOYEE,
    )


async def test_identity_without_employee_link_has_none() -> None:
    identity = await get_identity(make_user())

    assert identity.actor_id == USER
    assert identity.employee_id is None


async def test_valid_session_resolves_to_the_identity() -> None:
    users, sessions = FakeUserRepo(), FakeSessionRepo()
    await seed_user(
        users, tenant_id=TENANT, user_id=USER, role=Role.EMPLOYEE, employee_id=EMPLOYEE
    )
    await sessions.add(
        TENANT,
        uuid7(),
        user_id=USER,
        token_hash=session_token_hash(TOKEN),
        expires_at=VALID_UNTIL,
    )

    user = await get_current_user(make_service(users, sessions), TOKEN)
    identity = await get_identity(user)

    assert identity == Identity(
        tenant_id=TENANT,
        actor_id=USER,
        role=Role.EMPLOYEE,
        employee_id=EMPLOYEE,
    )


@pytest.mark.parametrize("token", [None, "unknown-token"])
async def test_missing_or_unknown_session_fails_closed(token: str | None) -> None:
    users, sessions = FakeUserRepo(), FakeSessionRepo()

    with pytest.raises(UnauthenticatedError) as exc:
        await get_current_user(make_service(users, sessions), token)

    assert exc.value.status_code == 401
    assert exc.value.code == "unauthenticated"


async def test_expired_session_fails_closed() -> None:
    users, sessions = FakeUserRepo(), FakeSessionRepo()
    await seed_user(users, tenant_id=TENANT, user_id=USER)
    await sessions.add(
        TENANT,
        uuid7(),
        user_id=USER,
        token_hash=session_token_hash(TOKEN),
        expires_at=EXPIRED_AT,
    )

    with pytest.raises(UnauthenticatedError):
        await get_current_user(make_service(users, sessions), TOKEN)


async def test_deactivated_users_session_fails_closed() -> None:
    users, sessions = FakeUserRepo(), FakeSessionRepo()
    await seed_user(users, tenant_id=TENANT, user_id=USER, active=False)
    await sessions.add(
        TENANT,
        uuid7(),
        user_id=USER,
        token_hash=session_token_hash(TOKEN),
        expires_at=VALID_UNTIL,
    )

    with pytest.raises(UnauthenticatedError):
        await get_current_user(make_service(users, sessions), TOKEN)
