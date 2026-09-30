"""HTTP integration tests for the auth routes (T4) — AC1, AC2, AC3, AC4, AC8, AC9.

The httpx cookie jar is the real flow: login sets `wp_session`, later requests
carry it cross-request, logout clears it. Users are seeded through the real
repository with real argon2 hashes — no auth route creates users until T6, and
these tests exercise the service/repository/session stack end to end.
"""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.config import Settings, get_settings
from app.domains.identity.credentials import (
    hash_password,
    new_session_token,
    session_token_hash,
)
from app.domains.identity.schemas import User
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.repositories.identity import (
    PostgresSessionRepository,
    PostgresUserRepository,
)
from app.main import app
from app.shared.identity import Role
from tests.integration.conftest import PINNED_USER_KEYS, TENANT_A, ClientFactory

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "stapled-battery-wrong"
INVALID_CREDENTIALS_BODY = {
    "error": {"code": "invalid_credentials", "message": "Invalid email or password"}
}
UNAUTHENTICATED_BODY = {
    "error": {"code": "unauthenticated", "message": "Authentication required"}
}


async def _seed_user(
    session: AsyncSession,
    *,
    email: str = "ada@workpulse.test",
    password: str = PASSWORD,
    role: Role = Role.ADMIN,
    active: bool = True,
) -> User:
    """One user with a real argon2 hash, straight through the repository."""
    return await PostgresUserRepository(session).add(
        TENANT_A,
        uuid7(),
        email=email,
        role=role,
        employee_id=None,
        password_hash=await hash_password(password),
        active=active,
    )


def _login(
    email: str = "ada@workpulse.test", password: str = PASSWORD
) -> dict[str, str]:
    return {"email": email, "password": password}


async def test_login_sets_cookie_and_me_resolves(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC1: seeded user logs in → `200 User`; `wp_session` is set; me works."""
    user = await _seed_user(session)
    async with client_factory({}) as client:
        response = await client.post("/api/v1/auth/login", json=_login())
        assert response.status_code == 200
        body = response.json()
        assert set(body) == PINNED_USER_KEYS  # exact pinned shape, no secrets
        assert body["id"] == str(user.id)
        assert body["tenant_id"] == str(TENANT_A)
        assert body["email"] == "ada@workpulse.test"
        assert body["role"] == "admin"
        assert body["employee_id"] is None
        assert body["active"] is True

        # Cookie flags: pinned contract; `Secure` off by dev default.
        lowered = response.headers["set-cookie"].lower()
        assert lowered.startswith("wp_session=")
        assert "httponly" in lowered
        assert "samesite=lax" in lowered
        assert "path=/" in lowered
        assert "secure" not in lowered
        assert client.cookies.get("wp_session") is not None

        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json() == body


async def test_secure_cookie_flag_follows_settings(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """Pinned: `Secure` is settings-driven (on outside local dev)."""
    await _seed_user(session)
    app.dependency_overrides[get_settings] = lambda: Settings(
        session_cookie_secure=True
    )
    try:
        async with client_factory({}) as client:
            response = await client.post("/api/v1/auth/login", json=_login())
            assert response.status_code == 200
            assert "secure" in response.headers["set-cookie"].lower()
    finally:
        app.dependency_overrides.pop(get_settings, None)


async def test_unknown_email_wrong_password_and_inactive_share_one_failure(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC2 (+ AC8 login half): one byte-identical 401 — no enumeration."""
    await _seed_user(session, email="active@workpulse.test")
    await _seed_user(session, email="inactive@workpulse.test", active=False)

    async with client_factory({}) as client:
        wrong = await client.post(
            "/api/v1/auth/login",
            json=_login("active@workpulse.test", "definitely-not-the-password"),
        )
        unknown = await client.post(
            "/api/v1/auth/login", json=_login("nobody@workpulse.test")
        )
        inactive = await client.post(
            "/api/v1/auth/login", json=_login("inactive@workpulse.test")
        )

    assert wrong.status_code == unknown.status_code == inactive.status_code == 401
    assert wrong.content == unknown.content == inactive.content  # byte-identical
    assert wrong.json() == INVALID_CREDENTIALS_BODY
    assert "set-cookie" not in wrong.headers  # a failed login issues no session


async def test_missing_unknown_and_expired_sessions_are_401(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC3 (auth half): every unusable session is `401 unauthenticated`."""
    user = await _seed_user(session)

    async with client_factory({}) as client:
        missing = await client.get("/api/v1/auth/me")
        assert missing.status_code == 401 and missing.json() == UNAUTHENTICATED_BODY
        denied = await client.put(
            "/api/v1/auth/password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert denied.status_code == 401 and denied.json() == UNAUTHENTICATED_BODY

    async with client_factory({}) as client:
        client.cookies.set("wp_session", "not-a-real-token")
        unknown = await client.get("/api/v1/auth/me")
        assert unknown.status_code == 401 and unknown.json() == UNAUTHENTICATED_BODY

    # An expired row is rejected even though the token exists.
    token = new_session_token()
    await PostgresSessionRepository(session).add(
        TENANT_A,
        uuid7(),
        user_id=user.id,
        token_hash=session_token_hash(token),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    async with client_factory({}) as client:
        client.cookies.set("wp_session", token)
        expired = await client.get("/api/v1/auth/me")
        assert expired.status_code == 401 and expired.json() == UNAUTHENTICATED_BODY


async def test_logout_is_idempotent_and_revokes_the_session(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_user(session)
    async with client_factory({}) as client:
        login = await client.post("/api/v1/auth/login", json=_login())
        assert login.status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 200

        out = await client.post("/api/v1/auth/logout")
        assert out.status_code == 204
        assert out.content == b""
        assert client.cookies.get("wp_session") is None  # cookie cleared
        assert (await client.get("/api/v1/auth/me")).status_code == 401

        again = await client.post("/api/v1/auth/logout")  # idempotent alone
        assert again.status_code == 204


async def test_change_password_revokes_all_sessions_and_new_password_logs_in(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC4: the pre-change sessions die; the new password logs in."""
    await _seed_user(session)
    async with client_factory({}) as first, client_factory({}) as second:
        for client in (first, second):
            assert (
                await client.post("/api/v1/auth/login", json=_login())
            ).status_code == 200
            assert (await client.get("/api/v1/auth/me")).status_code == 200

        # Wrong current password: 400 — and the session survives.
        wrong = await first.put(
            "/api/v1/auth/password",
            json={
                "current_password": "not-the-current-one",
                "new_password": NEW_PASSWORD,
            },
        )
        assert wrong.status_code == 400
        assert wrong.json() == {
            "error": {
                "code": "invalid_password",
                "message": "Current password is incorrect",
            }
        }
        assert (await first.get("/api/v1/auth/me")).status_code == 200

        changed = await first.put(
            "/api/v1/auth/password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert changed.status_code == 204

        # Every session of that user is revoked — both tabs go 401.
        assert (await first.get("/api/v1/auth/me")).status_code == 401
        assert (await second.get("/api/v1/auth/me")).status_code == 401

    async with client_factory({}) as client:
        old = await client.post("/api/v1/auth/login", json=_login())
        assert old.status_code == 401  # the old password is gone
        assert (
            await client.post("/api/v1/auth/login", json=_login(password=NEW_PASSWORD))
        ).status_code == 200


async def test_new_password_below_minimum_is_a_validation_error(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_user(session)
    async with client_factory({}) as client:
        await client.post("/api/v1/auth/login", json=_login())
        response = await client.put(
            "/api/v1/auth/password",
            json={"current_password": PASSWORD, "new_password": "ninechars"},
        )
        assert response.status_code == 422
        assert response.json() == {
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
            }
        }
        # The rejected request changed nothing: the session is still valid.
        assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_deactivated_user_is_rejected_everywhere_until_activated(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC8: deactivation kills the live session and login; activate restores."""
    user = await _seed_user(session)
    users = PostgresUserRepository(session)
    async with client_factory({}) as client:
        assert (
            await client.post("/api/v1/auth/login", json=_login())
        ).status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 200

        await users.update(TENANT_A, user.id, active=False)

        assert (await client.get("/api/v1/auth/me")).status_code == 401
        denied = await client.post("/api/v1/auth/login", json=_login())
        assert denied.status_code == 401
        assert denied.json() == INVALID_CREDENTIALS_BODY

        await users.update(TENANT_A, user.id, active=True)
        assert (
            await client.post("/api/v1/auth/login", json=_login())
        ).status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_password_change_writes_one_audit_entry_without_secrets(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC9 (password change): acting user id recorded, no password material."""
    user = await _seed_user(session)
    async with client_factory({}) as client:
        await client.post("/api/v1/auth/login", json=_login())
        # A failed change is not a mutation — it must not be audited.
        await client.put(
            "/api/v1/auth/password",
            json={
                "current_password": "wrong-password-here",
                "new_password": NEW_PASSWORD,
            },
        )
        changed = await client.put(
            "/api/v1/auth/password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert changed.status_code == 204

    rows = list(
        (
            await session.execute(
                select(AuditEntry).where(AuditEntry.action == "user.password_changed")
            )
        ).scalars()
    )
    assert len(rows) == 1
    entry = rows[0]
    assert entry.tenant_id == TENANT_A
    assert entry.actor_id == user.id  # the acting user id
    assert entry.entity_type == "user"
    assert entry.entity_id == user.id
    assert entry.changes == {"password": "changed"}
    serialized = json.dumps(entry.changes)
    assert PASSWORD not in serialized
    assert NEW_PASSWORD not in serialized
    assert "argon2" not in serialized


async def test_login_and_logout_write_no_audit_entries(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """Login/logout are not workforce mutations (failed logins stored never)."""
    await _seed_user(session)
    async with client_factory({}) as client:
        await client.post("/api/v1/auth/login", json=_login())
        await client.get("/api/v1/auth/me")
        await client.post("/api/v1/auth/logout")

    rows = (await session.execute(select(AuditEntry))).scalars().all()
    assert list(rows) == []
