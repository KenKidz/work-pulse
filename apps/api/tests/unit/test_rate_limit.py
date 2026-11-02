"""Unit tests for the Redis sliding-window rate limiter core (T6, ADR 0025).

Pure logic: a fake Redis executes a Python mirror of the Lua window policy, and
a fake clock drives every boundary — no wall-clock sleeps and no Redis server
(the real Lua script is exercised by the T7 integration tests against the
throwaway Redis). Covers window math, `Retry-After` countdown/reset, key and
tier isolation, fail-open on any Redis error, Settings validation and the
bounded metric labels.
"""

from __future__ import annotations

import math
from typing import cast

import pytest
import structlog
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.requests import Request

from app.config import Settings
from app.shared.rate_limit import RateLimiter, RateLimitTier, client_ip
from app.shared.telemetry import RATE_LIMIT_TIERS
from tests.unit.telemetry_harness import TelemetryHarness

IP = "203.0.113.7"


class FakeClock:
    """Wall-clock seconds driven explicitly by tests — no sleeps."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRedis:
    """Executes a Python mirror of `_SLIDING_WINDOW_SCRIPT` (in-memory sets).

    The mirror exists so the limiter's verdicts can be tested without Redis.
    The Lua script itself is pinned by the integration tests; this class only
    mirrors its trim/count/add/retry semantics, including removing entries at
    the exact window boundary (`ZREMRANGEBYSCORE` is inclusive).
    """

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.eval_calls = 0
        self.windows: dict[str, list[tuple[float, str]]] = {}
        self.response: list[int] | None = None

    async def eval(self, script: str, numkeys: int, *args: object) -> list[int]:
        self.eval_calls += 1
        if self.error is not None:
            raise self.error
        assert numkeys == 1
        assert script  # the production script is always sent
        key = str(args[0])
        now = float(str(args[1]))
        window = float(str(args[2]))
        limit = int(str(args[3]))
        member = str(args[4])

        if self.response is not None:
            return self.response

        entries = [
            entry for entry in self.windows.get(key, []) if entry[0] > now - window
        ]
        if len(entries) >= limit:
            retry_after = 1
            if entries:
                retry_after = max(1, math.ceil(entries[0][0] + window - now))
            self.windows[key] = entries
            return [0, retry_after]
        entries.append((now, member))
        self.windows[key] = entries
        return [1, 0]


def _limiter(
    redis: FakeRedis,
    clock: FakeClock,
    *,
    enabled: bool = True,
    auth: tuple[int, float] = (3, 60.0),
    mutation: tuple[int, float] = (120, 60.0),
) -> RateLimiter:
    settings = Settings(
        rate_limit_enabled=enabled,
        rate_limit_auth_max_requests=auth[0],
        rate_limit_auth_window_seconds=auth[1],
        rate_limit_mutation_max_requests=mutation[0],
        rate_limit_mutation_window_seconds=mutation[1],
    )
    return RateLimiter(
        redis=cast(Redis, redis),
        settings=settings,
        clock=clock,
    )


async def test_budget_plus_one_is_rejected_with_the_full_retry_after() -> None:
    clock = FakeClock()
    redis = FakeRedis()
    limiter = _limiter(redis, clock, auth=(3, 60.0))

    for _ in range(3):
        assert (await limiter.check(RateLimitTier.AUTH, IP)).allowed

    rejected = await limiter.check(RateLimitTier.AUTH, IP)

    assert rejected.allowed is False
    assert rejected.retry_after_seconds == 60
    assert redis.eval_calls == 4


async def test_retry_after_counts_down_and_the_window_resets_on_the_clock() -> None:
    clock = FakeClock()
    redis = FakeRedis()
    limiter = _limiter(redis, clock, auth=(3, 60.0))
    for _ in range(3):
        await limiter.check(RateLimitTier.AUTH, IP)

    clock.advance(20.0)
    assert (await limiter.check(RateLimitTier.AUTH, IP)).retry_after_seconds == 40

    clock.advance(39.9)
    # 0.1s left rounds up to the one-second floor, never zero.
    assert (await limiter.check(RateLimitTier.AUTH, IP)).retry_after_seconds == 1

    clock.advance(0.1)
    assert (await limiter.check(RateLimitTier.AUTH, IP)).allowed is True


async def test_the_window_resets_completely_after_the_window() -> None:
    clock = FakeClock()
    redis = FakeRedis()
    limiter = _limiter(redis, clock, auth=(3, 60.0))

    for _ in range(3):
        assert (await limiter.check(RateLimitTier.AUTH, IP)).allowed
    assert not (await limiter.check(RateLimitTier.AUTH, IP)).allowed

    clock.advance(60.0)
    for _ in range(3):
        assert (await limiter.check(RateLimitTier.AUTH, IP)).allowed


async def test_keys_have_independent_budgets() -> None:
    clock = FakeClock()
    redis = FakeRedis()
    limiter = _limiter(redis, clock, auth=(3, 60.0))

    for _ in range(3):
        await limiter.check(RateLimitTier.AUTH, "198.51.100.1")
    assert not (await limiter.check(RateLimitTier.AUTH, "198.51.100.1")).allowed

    # A different client IP (even on the same tier) still has its budget.
    for _ in range(3):
        assert (await limiter.check(RateLimitTier.AUTH, "198.51.100.2")).allowed


async def test_tiers_have_independent_budgets_and_are_not_shared() -> None:
    clock = FakeClock()
    redis = FakeRedis()
    limiter = _limiter(redis, clock, auth=(1, 60.0), mutation=(2, 60.0))
    key = "same-key"

    assert (await limiter.check(RateLimitTier.AUTH, key)).allowed
    assert not (await limiter.check(RateLimitTier.AUTH, key)).allowed

    assert (await limiter.check(RateLimitTier.MUTATION, key)).allowed
    assert (await limiter.check(RateLimitTier.MUTATION, key)).allowed
    assert not (await limiter.check(RateLimitTier.MUTATION, key)).allowed


async def test_redis_errors_fail_open_without_logging_the_key(
    telemetry_harness: TelemetryHarness,
) -> None:
    clock = FakeClock()
    redis = FakeRedis(error=RedisError("connection refused"))
    limiter = _limiter(redis, clock)

    with structlog.testing.capture_logs() as logs:
        decision = await limiter.check(RateLimitTier.MUTATION, "user-42")

    assert decision.allowed is True
    assert len(logs) == 1
    assert logs[0]["event"] == "rate_limit_fail_open"
    assert logs[0]["tier"] == "mutation"
    assert logs[0]["error"] == "RedisError"
    assert "user-42" not in str(logs)

    metrics = telemetry_harness.metrics()
    assert 'workpulse_rate_limit_fail_open_total{tier="mutation"} 1.0' in metrics
    assert "user-42" not in metrics


async def test_a_malformed_script_answer_fails_open(
    telemetry_harness: TelemetryHarness,
) -> None:
    clock = FakeClock()
    redis = FakeRedis()
    redis.response = []
    limiter = _limiter(redis, clock)

    with structlog.testing.capture_logs() as logs:
        decision = await limiter.check(RateLimitTier.AUTH, IP)

    assert decision.allowed is True
    assert logs[0]["event"] == "rate_limit_fail_open"
    assert logs[0]["error"] == "ValueError"
    assert 'workpulse_rate_limit_fail_open_total{tier="auth"} 1.0' in (
        telemetry_harness.metrics()
    )


async def test_throttled_requests_are_counted_with_bounded_labels(
    telemetry_harness: TelemetryHarness,
) -> None:
    clock = FakeClock()
    redis = FakeRedis()
    limiter = _limiter(redis, clock, auth=(1, 60.0))

    assert (await limiter.check(RateLimitTier.AUTH, IP)).allowed
    assert not (await limiter.check(RateLimitTier.AUTH, IP)).allowed

    metrics = telemetry_harness.metrics()
    assert 'workpulse_rate_limit_throttled_total{tier="auth"} 1.0' in metrics
    assert 'tier="mutation"' not in metrics
    assert IP not in metrics


async def test_disabled_limiter_passes_through_without_redis() -> None:
    clock = FakeClock()
    redis = FakeRedis()
    limiter = _limiter(redis, clock, enabled=False)

    assert (await limiter.check(RateLimitTier.AUTH, IP)).allowed
    assert redis.eval_calls == 0


async def test_invalid_tier_or_key_is_a_programming_error() -> None:
    limiter = _limiter(FakeRedis(), FakeClock())

    with pytest.raises(ValueError):
        await limiter.check(cast(RateLimitTier, "read"), IP)
    with pytest.raises(ValueError):
        await limiter.check(RateLimitTier.AUTH, "")


def test_from_settings_reads_the_rate_limit_block() -> None:
    settings = Settings(
        rate_limit_enabled=True,
        rate_limit_auth_max_requests=1,
        rate_limit_auth_window_seconds=10.0,
        rate_limit_mutation_max_requests=2,
        rate_limit_mutation_window_seconds=20.0,
    )
    limiter = RateLimiter.from_settings(
        settings, redis=cast(Redis, FakeRedis()), clock=FakeClock()
    )

    assert limiter.enabled is True


def test_tier_vocabulary_matches_the_metric_label_vocabulary() -> None:
    assert {tier.value for tier in RateLimitTier} == set(RATE_LIMIT_TIERS)


@pytest.mark.parametrize("budget", [0, -1])
def test_settings_reject_a_non_positive_budget(budget: int) -> None:
    with pytest.raises(ValidationError):
        Settings(rate_limit_auth_max_requests=budget)
    with pytest.raises(ValidationError):
        Settings(rate_limit_mutation_max_requests=budget)


@pytest.mark.parametrize(
    "window",
    [0.0, -1.0, float("inf"), float("nan")],
    ids=["zero", "negative", "infinity", "nan"],
)
def test_settings_reject_an_invalid_window(window: float) -> None:
    with pytest.raises(ValidationError):
        Settings(rate_limit_auth_window_seconds=window)
    with pytest.raises(ValidationError):
        Settings(rate_limit_mutation_window_seconds=window)


def test_client_ip_uses_the_first_forwarded_hop_then_the_peer() -> None:
    forwarded = Request(
        {
            "type": "http",
            "headers": [(b"x-forwarded-for", b"198.51.100.7, 10.0.0.2")],
            "client": ("203.0.113.9", 4242),
        }
    )
    assert client_ip(forwarded) == "198.51.100.7"

    peer = Request({"type": "http", "headers": [], "client": ("203.0.113.9", 4242)})
    assert client_ip(peer) == "203.0.113.9"

    # A synthetic transport may omit the peer; a key always exists.
    assert client_ip(Request({"type": "http", "headers": [], "client": None})) == (
        "unknown"
    )
