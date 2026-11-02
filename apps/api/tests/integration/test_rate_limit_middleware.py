"""Rate-limit middleware over HTTP (T7, ADR 0025).

The real throwaway Redis (:6380) executes the sliding-window Lua script; a
fake clock drives every window boundary, so no test sleeps. The auth tier is
keyed by client IP, the mutation tier by the session user id resolved through
the real `AuthService.me` seam, reads stay unthrottled and an unreachable Redis
fails open. Authorization behavior is asserted unchanged under the middleware.

Sessions are seeded directly (user + `sessions` row, committed) so the
middleware's own read-only session — which cannot see the test's uncommitted
`get_session` override — resolves the same identity the route does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.config import Settings
from app.domains.identity.credentials import (
    SESSION_COOKIE_NAME,
    new_session_token,
    session_token_hash,
)
from app.domains.organizations.models import Department as DepartmentRow
from app.infrastructure.postgres.repositories.identity import (
    PostgresSessionRepository,
    PostgresUserRepository,
)
from app.infrastructure.redis.client import get_redis
from app.main import app
from app.shared.identity import Role
from app.shared.rate_limit import RateLimiter
from tests.integration.conftest import (
    TENANT_A,
    TENANT_B,
    ClientFactory,
    identity,
)

PASSWORD_HASH = "not-used-by-these-tests"  # sessions are seeded, login is not
IP = "203.0.113.10"
OTHER_IP = "203.0.113.11"


class FakeClock:
    """Wall-clock seconds driven explicitly by tests — no sleeps."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class UnavailableRedis:
    """A Redis client whose every command fails (fail-open path)."""

    async def eval(self, *_args: object, **_kwargs: object) -> object:
        raise RedisError("connection refused")


async def _flush_rate_limit_keys() -> None:
    """Remove only this feature's keys from the throwaway Redis."""
    redis = get_redis()
    keys = [key async for key in redis.scan_iter(match="workpulse:rate_limit:*")]
    if keys:
        await redis.delete(*keys)


@pytest.fixture(autouse=True)
async def _clean_rate_limit_state() -> AsyncIterator[None]:
    """Start and end every test with a cold limiter namespace."""
    await _flush_rate_limit_keys()
    previous = getattr(app.state, "rate_limiter", None)
    try:
        yield
    finally:
        app.state.rate_limiter = previous
        await _flush_rate_limit_keys()


def _install_limiter(
    clock: FakeClock,
    *,
    auth: tuple[int, float] = (30, 60.0),
    mutation: tuple[int, float] = (120, 60.0),
    redis: object | None = None,
) -> RateLimiter:
    """Install an app-state limiter with explicit budgets and a fake clock."""
    settings = Settings(
        rate_limit_enabled=True,
        # ASGITransport peers at loopback; the proxy trust makes the forwarded
        # test IPs meaningful, matching a real proxy that overwrites the header.
        rate_limit_trusted_proxies=["127.0.0.1"],
        rate_limit_auth_max_requests=auth[0],
        rate_limit_auth_window_seconds=auth[1],
        rate_limit_mutation_max_requests=mutation[0],
        rate_limit_mutation_window_seconds=mutation[1],
    )
    limiter = RateLimiter(
        redis=cast(Redis, redis) if redis is not None else None,
        settings=settings,
        clock=clock,
    )
    app.state.rate_limiter = limiter
    return limiter


async def _seed_user_with_session(
    session: AsyncSession,
    *,
    email: str,
    tenant: UUID = TENANT_A,
    role: Role = Role.ADMIN,
) -> tuple[UUID, str]:
    """A committed user + live session; returns (user id, cookie token)."""
    user = await PostgresUserRepository(session).add(
        tenant,
        uuid7(),
        email=email,
        role=role,
        employee_id=None,
        password_hash=PASSWORD_HASH,
    )
    token = new_session_token()
    await PostgresSessionRepository(session).add(
        tenant,
        uuid7(),
        user_id=user.id,
        token_hash=session_token_hash(token),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await session.commit()
    return user.id, token


def _assert_rate_limited(response: Any) -> None:
    assert response.status_code == 429, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "rate_limited"
    retry_after = int(response.headers["Retry-After"])
    assert 1 <= retry_after <= 60


async def test_auth_tier_throttles_the_31st_login_from_one_ip(
    client_factory: ClientFactory,
) -> None:
    """AC — 30 default-budget logins pass; the 31st is 429 before the route.

    Unknown emails keep the first 30 requests on the `401 invalid_credentials`
    path, so a `429` on the 31st proves the middleware short-circuited before
    `AuthService.login`. `PUT /auth/password` shares the same IP budget.
    """
    _install_limiter(FakeClock())

    async with client_factory() as client:
        for attempt in range(30):
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": f"nobody-{attempt}@example.test",
                    "password": "not-the-password",
                },
                headers={"X-Forwarded-For": IP},
            )
            assert response.status_code == 401, response.text

        throttled = await client.post(
            "/api/v1/auth/login",
            json={"email": "anyone@example.test", "password": "not-the-password"},
            headers={"X-Forwarded-For": IP},
        )
        password = await client.put(
            "/api/v1/auth/password",
            json={"current_password": "x", "new_password": "long-enough-password"},
            headers={"X-Forwarded-For": IP},
        )
        # The limiter runs inside the telemetry middleware, so its
        # short-circuits are visible in the bounded HTTP metric.
        metrics = await client.get("/metrics")

    _assert_rate_limited(throttled)
    _assert_rate_limited(password)  # same auth tier, same IP window
    assert (
        'workpulse_http_requests_total{method="POST",route="UNMATCHED",'
        'status_code="429"}'
    ) in metrics.text

    # A different client IP is a different auth budget (no shared throttle).
    async with client_factory() as other:
        allowed = await other.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.test", "password": "not-the-password"},
            headers={"X-Forwarded-For": OTHER_IP},
        )
    assert allowed.status_code == 401


async def test_mutation_tier_is_per_user_and_reads_stay_open(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    """AC — one user over budget throttles; another user and all reads do not."""
    _install_limiter(FakeClock(), mutation=(3, 60.0))
    _, token_a = await _seed_user_with_session(session, email="a@example.test")
    _, token_b = await _seed_user_with_session(session, email="b@example.test")

    async with client_factory(None) as client_a:
        client_a.cookies.set(SESSION_COOKIE_NAME, token_a)
        for index in range(3):
            created = await client_a.post(
                "/api/v1/departments",
                json={"name": f"Team A-{index}"},
                headers={"X-Forwarded-For": IP},
            )
            assert created.status_code == 201, created.text

        throttled = await client_a.post(
            "/api/v1/departments",
            json={"name": "Team A-overflow"},
            headers={"X-Forwarded-For": IP},
        )
        _assert_rate_limited(throttled)

        # Reads are unthrottled even while the user's mutation budget is spent.
        me = await client_a.get("/api/v1/auth/me", headers={"X-Forwarded-For": IP})
        assert me.status_code == 200, me.text

        # Exempt operating paths stay reachable under pressure.
        assert (await client_a.get("/healthz")).status_code == 200
        assert (await client_a.get("/metrics")).status_code == 200

    # The rejected mutation never reached the router: no orphan row.
    names = (await session.execute(select(DepartmentRow.name))).scalars().all()
    assert "Team A-overflow" not in names

    # User B's budget is independent (same IP, different session user).
    async with client_factory(None) as client_b:
        client_b.cookies.set(SESSION_COOKIE_NAME, token_b)
        allowed = await client_b.post(
            "/api/v1/departments",
            json={"name": "Team B-0"},
            headers={"X-Forwarded-For": IP},
        )
    assert allowed.status_code == 201, allowed.text

    # A sessionless mutation keys by IP and does not consume user A's budget.
    async with client_factory(None) as anonymous:
        out = await anonymous.post(
            "/api/v1/auth/logout", headers={"X-Forwarded-For": IP}
        )
    assert out.status_code == 204, out.text


async def test_budget_window_resets_on_the_injected_clock(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    """AC — explicit 1/60s budget: one allowed, one 429, reset on the clock."""
    clock = FakeClock()
    _install_limiter(clock, mutation=(1, 60.0))
    _, token = await _seed_user_with_session(session, email="clock@example.test")

    async with client_factory(None) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        first = await client.post("/api/v1/departments", json={"name": "Clock-0"})
        assert first.status_code == 201, first.text

        blocked = await client.post("/api/v1/departments", json={"name": "Clock-1"})
        _assert_rate_limited(blocked)
        assert blocked.headers["Retry-After"] == "60"

        clock.advance(60.0)
        after_reset = await client.post("/api/v1/departments", json={"name": "Clock-2"})
    assert after_reset.status_code == 201, after_reset.text


async def test_redis_unreachable_fails_open_without_logging_the_key(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    """AC — Redis errors never break traffic and the warning carries no key."""
    _install_limiter(FakeClock(), mutation=(1, 60.0), redis=UnavailableRedis())

    async with client_factory() as client:
        with structlog.testing.capture_logs() as logs:
            response = await client.post(
                "/api/v1/departments",
                json={"name": "Fail open"},
                headers={"X-Forwarded-For": IP},
            )
        assert response.status_code == 201, response.text

    fail_open = [
        entry for entry in logs if entry.get("event") == "rate_limit_fail_open"
    ]
    assert len(fail_open) == 1
    assert fail_open[0]["tier"] == "mutation"
    assert fail_open[0]["error"] == "RedisError"
    assert IP not in str(logs)

    # The request mutated normally: the fail-open path is not a silent drop.
    names = (await session.execute(select(DepartmentRow.name))).scalars().all()
    assert "Fail open" in names


async def test_middleware_does_not_change_authorization_semantics(
    client_factory: ClientFactory,
) -> None:
    """AC — cross-tenant reads stay 404 and denied-role mutations stay 403."""
    _install_limiter(FakeClock(), mutation=(10, 60.0))

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        created = await tenant_b.post(
            "/api/v1/departments", json={"name": "Tenant B only"}
        )
        assert created.status_code == 201, created.text
        department_id = created.json()["id"]

    async with client_factory(identity(tenant=TENANT_A)) as tenant_a:
        cross_tenant = await tenant_a.get(f"/api/v1/departments/{department_id}")
        assert cross_tenant.status_code == 404, cross_tenant.text

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        denied = await employee.post(
            "/api/v1/departments", json={"name": "Not allowed"}
        )
        assert denied.status_code == 403, denied.text
