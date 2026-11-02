"""Redis sliding-window rate limiting (Phase 7, ADR 0025).

One shared limiter enforces two tiers (business rule 1):

- **auth** — `POST /auth/login` and `PUT /auth/password`, keyed by client IP
  (brute-force protection);
- **mutation** — every authenticated `POST`/`PUT`/`PATCH`/`DELETE` under the
  API, keyed by session user id, so budgets are independent per person and
  inherently tenant-scoped.

Reads are never throttled. This module holds both the core — an exact
sorted-set sliding window, evaluated atomically in one Redis Lua script — and
the shared ASGI middleware that classifies each request before routing (T7).
One member is appended per admitted request
and scores older than `now - window` are trimmed first, so a window always
holds at most one budget's worth of members and the key expires after the
window. Rejected requests append nothing: the reset instant is the oldest
admitted request's expiry.

**Fail-open (business rule 4).** Any Redis failure — unreachable, slow (the
client's 0.5s connect / 1s socket timeouts bound the wait), or a malformed
script answer — lets the request proceed and records a PII-free warning plus
the `workpulse_rate_limit_fail_open` counter. Reliability machinery must never
break user traffic. The limiter key (IP or user id) is never logged and never
becomes a metric label.

`clock` defaults to `time.time` (wall-clock epoch seconds) rather than
`time.monotonic`: window scores are shared state, so they must be comparable
across processes/instances. Tests inject a fake clock and never wait on the
wall clock.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings, get_settings
from app.domains.identity.credentials import SESSION_COOKIE_NAME
from app.infrastructure.redis.client import get_redis
from app.shared.errors import RateLimitedError, UnauthenticatedError, error_response
from app.shared.logging import get_logger
from app.shared.telemetry import (
    METRICS_PATH,
    record_rate_limit_fail_open,
    record_rate_limit_throttled,
)

_log = get_logger("rate_limit")

#: Redis key namespace; the tier and caller key follow it.
_KEY_PREFIX = "workpulse:rate_limit"

#: Mount prefix shared with `app.main`; the mutation tier is scoped to it.
_API_PREFIX = "/api/v1"

#: Auth-tier routes (business rule 1): keyed per client IP.
_AUTH_LOGIN_PATH = f"{_API_PREFIX}/auth/login"
_AUTH_PASSWORD_PATH = f"{_API_PREFIX}/auth/password"

#: Paths that stay reachable under pressure: operators must be able to see the
#: system while a tier is throttling.
_EXEMPT_PATHS = frozenset({"/healthz", METRICS_PATH})

#: Mutating methods covered by the mutation tier (reads are never throttled).
_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Atomic sliding-window decision. `KEYS[1]` is the caller key, `ARGV` is
#: `now` (epoch seconds), `window` (seconds), `limit` (requests) and a unique
#: member id. Returns `{allowed, retry_after}` where `retry_after` is whole
#: seconds until the oldest admitted request leaves the window (0 when allowed).
#:
#: Doing trim/count/add in one script is what makes concurrent requests obey
#: the budget: a read-then-write from Python would admit a burst past the limit.
_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = 1
    if oldest[2] then
        retry_after = math.ceil(tonumber(oldest[2]) + window - now)
        if retry_after < 1 then retry_after = 1 end
    end
    return {0, retry_after}
end

redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, math.ceil(window * 1000))
return {1, 0}
"""


class RateLimitTier(StrEnum):
    """The two throttled surfaces; values are the bounded `tier` label."""

    AUTH = "auth"
    MUTATION = "mutation"


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """One limiter verdict; a rejection carries whole seconds to the reset."""

    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter:
    """Exact per-key sliding-window limiter over Redis (fail-open)."""

    def __init__(
        self,
        *,
        redis: Redis | None = None,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._settings = settings if settings is not None else get_settings()
        self._clock = clock

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        redis: Redis | None = None,
        clock: Callable[[], float] = time.time,
    ) -> RateLimiter:
        """Build the limiter from the ``RATE_LIMIT_*`` Settings block."""
        resolved = settings if settings is not None else get_settings()
        return cls(redis=redis, settings=resolved, clock=clock)

    @property
    def enabled(self) -> bool:
        return self._settings.rate_limit_enabled

    @property
    def settings(self) -> Settings:
        """The Settings block this limiter reads its tier budgets from."""
        return self._settings

    async def check(self, tier: RateLimitTier, key: str) -> RateLimitDecision:
        """Consume one slot for `tier`/`key` and return the verdict.

        `key` is the caller identity (client IP or session user id). It is
        used only as a Redis key component — never logged, never a metric
        label. A limiter that cannot reach Redis fails open.
        """
        if not isinstance(tier, RateLimitTier):
            raise ValueError(f"unknown rate-limit tier: {tier!r}")
        if not key:
            raise ValueError("rate-limit key must be a non-empty string")

        if not self._settings.rate_limit_enabled:
            return RateLimitDecision(allowed=True)

        limit, window = self._tier_budget(tier)
        now = self._clock()
        client = self._redis if self._redis is not None else get_redis()

        try:
            raw = await client.eval(
                _SLIDING_WINDOW_SCRIPT,
                1,
                f"{_KEY_PREFIX}:{tier.value}:{key}",
                f"{float(now):.6f}",
                f"{float(window):.6f}",
                str(limit),
                uuid4().hex,
            )
            decision = _decode_decision(raw)
        except Exception as exc:
            # Fail open. Log only the tier and exception type: the key is an
            # IP/user id and driver text can echo connection details.
            _log.warning(
                "rate_limit_fail_open",
                tier=tier.value,
                error=type(exc).__name__,
            )
            record_rate_limit_fail_open(tier.value)
            return RateLimitDecision(allowed=True)

        if not decision.allowed:
            record_rate_limit_throttled(tier.value)
        return decision

    def _tier_budget(self, tier: RateLimitTier) -> tuple[int, float]:
        if tier is RateLimitTier.AUTH:
            return (
                self._settings.rate_limit_auth_max_requests,
                self._settings.rate_limit_auth_window_seconds,
            )
        return (
            self._settings.rate_limit_mutation_max_requests,
            self._settings.rate_limit_mutation_window_seconds,
        )


def _decode_decision(raw: Any) -> RateLimitDecision:
    """Translate the script answer; a malformed answer fails open upstream."""
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError("unexpected rate-limit script response")
    if int(raw[0]) == 1:
        return RateLimitDecision(allowed=True)
    return RateLimitDecision(
        allowed=False,
        retry_after_seconds=max(1, int(raw[1])),
    )


def client_ip(request: Request) -> str:
    """The auth-tier key: first `X-Forwarded-For` hop, else the socket peer.

    Local/dev traffic through a reverse proxy carries the caller in the first
    forwarded hop; without the header the ASGI peer address is used. A missing
    peer (synthetic transports) collapses to `unknown` so a key always exists.
    The value is only a Redis key component — never logged, never a metric
    label.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    client = request.client
    return client.host if client is not None else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply the auth/mutation tiers before any router or service work (T7).

    The limiter is read from `app.state.rate_limiter` on every request so the
    app factory's seam stays testable: an integration test can install a
    limiter built with an injected clock and explicit budgets without rebuilding
    the application.

    A throttled request returns the house error shape directly instead of
    raising: user middleware runs outside Starlette's `ExceptionMiddleware`, so
    a raised exception would surface as a generic 500. Nothing downstream runs
    on a rejection — no router, service, audit entry or event. Tier resolution
    itself never blocks: a session that cannot be resolved falls back to the
    client IP so the mutation tier still bounds the request; the route's own
    identity dependency remains the single authorization decision.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)
        if limiter is None or not limiter.enabled:
            return await call_next(request)

        tier = self._tier_for(request)
        if tier is None:
            return await call_next(request)

        key = (
            client_ip(request)
            if tier is RateLimitTier.AUTH
            else await self._mutation_key(request, limiter)
        )
        decision = await limiter.check(tier, key)
        if not decision.allowed:
            return error_response(RateLimitedError(decision.retry_after_seconds))
        return await call_next(request)

    @staticmethod
    def _tier_for(request: Request) -> RateLimitTier | None:
        """Classify the request before routing; `None` means unthrottled.

        Normalizing the trailing slash keeps `/auth/login/` on the auth tier
        even though FastAPI would redirect it before the route matches.
        """
        path = request.url.path.rstrip("/") or "/"
        if path in _EXEMPT_PATHS:
            return None
        if request.method == "POST" and path == _AUTH_LOGIN_PATH:
            return RateLimitTier.AUTH
        if request.method == "PUT" and path == _AUTH_PASSWORD_PATH:
            return RateLimitTier.AUTH
        if request.method in _MUTATION_METHODS and (
            path == _API_PREFIX or path.startswith(f"{_API_PREFIX}/")
        ):
            return RateLimitTier.MUTATION
        return None

    @staticmethod
    async def _mutation_key(request: Request, limiter: RateLimiter) -> str:
        """Session user id, or the client IP when no session resolves.

        The lookup reuses the auth service seam (`resolve_session_user_id`) for
        the user id only. A missing/expired/deactivated session is expected and
        silent; an unexpected resolution failure is logged (error type only)
        and falls back to the IP so reliability plumbing never turns a request
        into a middleware 500 — the route still answers its own auth error.
        """
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if token:
            # Local import: `app.dependencies` wires every domain and is not
            # needed by the limiter core or its unit tests.
            from app.dependencies import resolve_session_user_id

            try:
                user_id = await resolve_session_user_id(token, limiter.settings)
            except UnauthenticatedError:
                user_id = None
            except Exception as exc:  # noqa: BLE001 - never fail the request here
                _log.warning(
                    "rate_limit_identity_failed",
                    error=type(exc).__name__,
                )
                user_id = None
            if user_id is not None:
                return str(user_id)
        return client_ip(request)


__all__ = [
    "RateLimitDecision",
    "RateLimitTier",
    "RateLimitMiddleware",
    "RateLimiter",
    "client_ip",
]
