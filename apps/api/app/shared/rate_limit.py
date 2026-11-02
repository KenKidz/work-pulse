"""Redis sliding-window rate limiting (Phase 7, ADR 0025).

One shared limiter enforces two tiers (business rule 1):

- **auth** — `POST /auth/login` and `PUT /auth/password`, keyed by client IP
  (brute-force protection);
- **mutation** — every authenticated `POST`/`PUT`/`PATCH`/`DELETE` under the
  API, keyed by session user id, so budgets are independent per person and
  inherently tenant-scoped.

Reads are never throttled; the HTTP tiering itself lives in the middleware
(T7). This module is the core: an exact sorted-set sliding window, evaluated
atomically in one Redis Lua script. One member is appended per admitted request
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
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis

from app.config import Settings, get_settings
from app.infrastructure.redis.client import get_redis
from app.shared.logging import get_logger
from app.shared.telemetry import (
    record_rate_limit_fail_open,
    record_rate_limit_throttled,
)

_log = get_logger("rate_limit")

#: Redis key namespace; the tier and caller key follow it.
_KEY_PREFIX = "workpulse:rate_limit"

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


__all__ = [
    "RateLimitDecision",
    "RateLimitTier",
    "RateLimiter",
]
