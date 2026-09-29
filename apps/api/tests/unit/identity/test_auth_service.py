"""Unit tests for AuthService — login/session/password rules (T3, no DB)."""

import json

import pytest
from uuid_utils.compat import uuid7

from app.domains.identity.credentials import session_token_hash, verify_password
from app.domains.identity.exceptions import (
    InvalidCredentialsError,
    InvalidPasswordError,
    UnauthenticatedError,
)
from app.domains.identity.schemas import ChangePasswordRequest, LoginRequest, User
from app.domains.identity.service import AuthService
from app.shared.identity import Role
from tests.unit.identity.conftest import (
    DEFAULT_PASSWORD,
    NOW,
    SESSION_TTL,
    TENANT_A,
    seed_user,
)
from tests.unit.identity.fakes import (
    FakeSessionRepo,
    FakeUserRepo,
    RecordingAuditWriter,
)


def test_user_wire_shape_never_carries_credentials() -> None:
    assert set(User.model_fields) == {
        "id",
        "tenant_id",
        "email",
        "role",
        "employee_id",
        "active",
        "created_at",
        "updated_at",
    }


async def test_login_returns_user_and_stores_only_the_token_hash(
    auth: AuthService,
    users: FakeUserRepo,
    sessions: FakeSessionRepo,
    audit: RecordingAuditWriter,
    default_password_hash: str,
) -> None:
    user = await seed_user(
        users,
        email="ada@workpulse.test",
        role=Role.ADMIN,
        password_hash=default_password_hash,
    )

    logged_in, token = await auth.login(
        LoginRequest(email=user.email, password=DEFAULT_PASSWORD), NOW
    )

    assert isinstance(logged_in, User)
    assert (logged_in.id, logged_in.email, logged_in.role) == (
        user.id,
        user.email,
        Role.ADMIN,
    )
    assert "password_hash" not in logged_in.model_dump()

    stored = next(iter(sessions.rows.values()))
    assert stored.token_hash == session_token_hash(token)
    assert stored.token_hash != token  # the raw token is never persisted
    assert stored.user_id == user.id and stored.tenant_id == TENANT_A
    assert stored.expires_at == NOW + SESSION_TTL  # absolute 7-day TTL
    assert audit.entries == []  # login is not audited


async def test_login_failure_paths_are_identical(
    auth: AuthService,
    users: FakeUserRepo,
    sessions: FakeSessionRepo,
    default_password_hash: str,
) -> None:
    await seed_user(
        users, email="ada@workpulse.test", password_hash=default_password_hash
    )

    failures = []
    for email, password in (
        ("missing@workpulse.test", DEFAULT_PASSWORD),  # unknown email
        ("ada@workpulse.test", "wrong password entirely"),  # wrong password
    ):
        with pytest.raises(InvalidCredentialsError) as exc:
            await auth.login(LoginRequest(email=email, password=password), NOW)
        failures.append(
            (exc.value.status_code, exc.value.code, exc.value.message, str(exc.value))
        )

    # Byte-identical: one failure path, no enumeration signal.
    assert failures[0] == failures[1]
    assert failures[0][:3] == (401, "invalid_credentials", "Invalid email or password")
    assert sessions.rows == {}  # a failed login never creates a session


async def test_login_deactivated_user_cannot_log_in(
    auth: AuthService,
    users: FakeUserRepo,
    sessions: FakeSessionRepo,
    default_password_hash: str,
) -> None:
    user = await seed_user(
        users,
        email="gone@workpulse.test",
        active=False,
        password_hash=default_password_hash,
    )

    with pytest.raises(InvalidCredentialsError) as exc:
        await auth.login(LoginRequest(email=user.email, password=DEFAULT_PASSWORD), NOW)

    # Same static body as the unknown-email / wrong-password failures.
    assert (exc.value.status_code, exc.value.code, exc.value.message) == (
        401,
        "invalid_credentials",
        "Invalid email or password",
    )
    assert sessions.rows == {}


async def test_logout_deletes_the_session_and_is_idempotent(
    auth: AuthService,
    users: FakeUserRepo,
    sessions: FakeSessionRepo,
    default_password_hash: str,
) -> None:
    user = await seed_user(users, password_hash=default_password_hash)
    _, token = await auth.login(
        LoginRequest(email=user.email, password=DEFAULT_PASSWORD), NOW
    )

    await auth.logout(token)
    assert sessions.rows == {}

    await auth.logout(token)  # already gone: still 204
    await auth.logout(None)  # no session at all: still 204


async def test_me_resolves_the_session_to_a_wire_user(
    auth: AuthService, users: FakeUserRepo, default_password_hash: str
) -> None:
    user = await seed_user(users, password_hash=default_password_hash)
    _, token = await auth.login(
        LoginRequest(email=user.email, password=DEFAULT_PASSWORD), NOW
    )

    current = await auth.me(token, NOW)

    assert isinstance(current, User)
    assert current.id == user.id
    assert "password_hash" not in current.model_dump()


async def test_me_rejects_every_unusable_session(
    auth: AuthService,
    users: FakeUserRepo,
    sessions: FakeSessionRepo,
    default_password_hash: str,
) -> None:
    user = await seed_user(users, password_hash=default_password_hash)
    _, valid_token = await auth.login(
        LoginRequest(email=user.email, password=DEFAULT_PASSWORD), NOW
    )

    # An expired row (absolute TTL) and a revoked row — both unusable.
    expired_token = "expired-token"
    await sessions.add(
        TENANT_A,
        uuid7(),
        user_id=user.id,
        token_hash=session_token_hash(expired_token),
        expires_at=NOW,
    )
    revoked_token = "revoked-token"
    await sessions.add(
        TENANT_A,
        uuid7(),
        user_id=user.id,
        token_hash=session_token_hash(revoked_token),
        expires_at=NOW + SESSION_TTL,
    )
    await auth.logout(revoked_token)

    for candidate in (None, "unknown-token", expired_token, revoked_token):
        with pytest.raises(UnauthenticatedError) as exc:
            await auth.me(candidate, NOW)
        assert (exc.value.status_code, exc.value.code) == (401, "unauthenticated")

    # Deactivation takes effect immediately: the existing session is rejected.
    await users.update(TENANT_A, user.id, active=False)
    with pytest.raises(UnauthenticatedError):
        await auth.me(valid_token, NOW)


async def test_change_password_requires_the_current_password(
    auth: AuthService,
    users: FakeUserRepo,
    sessions: FakeSessionRepo,
    audit: RecordingAuditWriter,
    default_password_hash: str,
) -> None:
    user = await seed_user(users, password_hash=default_password_hash)
    _, token = await auth.login(
        LoginRequest(email=user.email, password=DEFAULT_PASSWORD), NOW
    )

    with pytest.raises(InvalidPasswordError) as exc:
        await auth.change_password(
            token,
            ChangePasswordRequest(
                current_password="not-the-password",
                new_password="a-brand-new-password",
            ),
            NOW,
        )

    assert (exc.value.status_code, exc.value.code) == (400, "invalid_password")
    stored = await users.get_credentials(TENANT_A, user.id)
    assert stored is not None
    assert await verify_password(stored.password_hash, DEFAULT_PASSWORD)
    # Nothing revoked, nothing audited — the change failed.
    assert await sessions.get_by_token_hash(session_token_hash(token), NOW) is not None
    assert audit.entries == []


async def test_change_password_rehashes_revokes_all_sessions_and_audits(
    auth: AuthService,
    users: FakeUserRepo,
    sessions: FakeSessionRepo,
    audit: RecordingAuditWriter,
    default_password_hash: str,
) -> None:
    user = await seed_user(users, password_hash=default_password_hash)
    _, first = await auth.login(
        LoginRequest(email=user.email, password=DEFAULT_PASSWORD), NOW
    )
    _, second = await auth.login(
        LoginRequest(email=user.email, password=DEFAULT_PASSWORD), NOW
    )
    other = await seed_user(
        users, email="grace@workpulse.test", password_hash=default_password_hash
    )
    _, other_token = await auth.login(
        LoginRequest(email=other.email, password=DEFAULT_PASSWORD), NOW
    )

    new_password = "another-good-password"
    await auth.change_password(
        first,
        ChangePasswordRequest(
            current_password=DEFAULT_PASSWORD, new_password=new_password
        ),
        NOW,
    )

    stored = await users.get_credentials(TENANT_A, user.id)
    assert stored is not None
    assert await verify_password(stored.password_hash, new_password)
    assert not await verify_password(stored.password_hash, DEFAULT_PASSWORD)

    # Every session of the user is revoked; the other user's survives.
    assert await sessions.get_by_token_hash(session_token_hash(first), NOW) is None
    assert await sessions.get_by_token_hash(session_token_hash(second), NOW) is None
    assert (
        await sessions.get_by_token_hash(session_token_hash(other_token), NOW)
        is not None
    )
    with pytest.raises(UnauthenticatedError):
        await auth.me(first, NOW)

    # One audit entry, acting user = the user, no password material anywhere.
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert (entry.action, entry.entity_type) == ("user.password_changed", "user")
    assert entry.entity_id == user.id and entry.actor_id == user.id
    assert entry.occurred_at == NOW
    serialized = json.dumps(entry.model_dump(mode="json"))
    assert DEFAULT_PASSWORD not in serialized and new_password not in serialized

    # The new password works end-to-end.
    _, re_login = await auth.login(
        LoginRequest(email=user.email, password=new_password), NOW
    )
    assert (await auth.me(re_login, NOW)).id == user.id


async def test_change_password_requires_a_session(auth: AuthService) -> None:
    payload = ChangePasswordRequest(
        current_password=DEFAULT_PASSWORD, new_password="another-good-password"
    )

    with pytest.raises(UnauthenticatedError):
        await auth.change_password(None, payload, NOW)
    with pytest.raises(UnauthenticatedError):
        await auth.change_password("unknown-token", payload, NOW)
