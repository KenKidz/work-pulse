"""Unit tests for the circuit breaker state machine (pure logic, fake clock).

Covers the T2 contract: threshold trip, fail-fast with no I/O while open, the
single half-open trial (concurrent callers rejected), recovery by success and
re-open by a failed trial, plus the Settings/disabled/invalid-input edges.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.shared.circuit_breaker import (
    BreakerState,
    CircuitBreaker,
    CircuitOpenError,
)
from app.shared.telemetry import CIRCUIT_BREAKER_STATES


class FakeClock:
    """Monotonic clock driven explicitly by tests — no wall-clock sleeps."""

    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_breaker(
    clock: FakeClock,
    *,
    threshold: int = 3,
    cooldown: float = 30.0,
    enabled: bool = True,
) -> CircuitBreaker:
    return CircuitBreaker(
        "minio",
        enabled=enabled,
        failure_threshold=threshold,
        cooldown_seconds=cooldown,
        clock=clock,
    )


def _fail() -> None:
    raise RuntimeError("dependency down")


def _ok() -> str:
    return "ok"


def test_opens_after_the_consecutive_failure_threshold() -> None:
    breaker = make_breaker(FakeClock())

    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call_sync(_fail)
        assert breaker.state is BreakerState.CLOSED

    with pytest.raises(RuntimeError):
        breaker.call_sync(_fail)

    assert breaker.state is BreakerState.OPEN


def test_success_resets_the_consecutive_failure_count() -> None:
    breaker = make_breaker(FakeClock())

    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call_sync(_fail)
    assert breaker.call_sync(_ok) == "ok"

    # A fresh pair of failures must not reach the threshold of three.
    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call_sync(_fail)

    assert breaker.state is BreakerState.CLOSED


def test_open_breaker_fails_fast_without_calling_the_operation() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock, threshold=1)
    calls = 0

    def counted() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    with pytest.raises(RuntimeError):
        breaker.call_sync(_fail)
    assert breaker.state is BreakerState.OPEN

    with pytest.raises(CircuitOpenError):
        breaker.call_sync(counted)

    assert calls == 0
    assert breaker.state is BreakerState.OPEN


def test_cooldown_boundary_admits_exactly_one_half_open_trial() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock, threshold=1, cooldown=30.0)

    with pytest.raises(RuntimeError):
        breaker.call_sync(_fail)

    clock.advance(29.9)
    with pytest.raises(CircuitOpenError):
        breaker.call_sync(_ok)
    assert breaker.state is BreakerState.OPEN

    clock.advance(0.1)
    assert breaker.call_sync(_ok) == "ok"
    assert breaker.state is BreakerState.CLOSED


async def test_half_open_admits_one_trial_and_rejects_concurrent_callers() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock, threshold=1, cooldown=30.0)

    with pytest.raises(RuntimeError):
        breaker.call_sync(_fail)
    clock.advance(30.0)

    gate = asyncio.Event()

    async def slow_trial() -> str:
        await gate.wait()
        return "trial"

    async def rejected() -> str:  # pragma: no cover — must never be awaited
        raise AssertionError("a concurrent caller must not reach the operation")

    trial = asyncio.create_task(breaker.call(slow_trial))
    await asyncio.sleep(0)

    assert breaker.state is BreakerState.HALF_OPEN
    with pytest.raises(CircuitOpenError):
        await breaker.call(rejected)

    gate.set()
    assert await trial == "trial"
    assert breaker.state is BreakerState.CLOSED

    # Once closed, ordinary concurrent traffic flows again.
    assert await breaker.call(slow_trial) == "trial"


def test_failed_half_open_trial_reopens_the_breaker() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock, threshold=1, cooldown=30.0)

    with pytest.raises(RuntimeError):
        breaker.call_sync(_fail)

    clock.advance(30.0)
    with pytest.raises(RuntimeError):
        breaker.call_sync(_fail)
    assert breaker.state is BreakerState.OPEN

    # A fresh cooldown is required; the failed trial did not leave it half-open.
    with pytest.raises(CircuitOpenError):
        breaker.call_sync(_ok)

    clock.advance(30.0)
    assert breaker.call_sync(_ok) == "ok"
    assert breaker.state is BreakerState.CLOSED


def test_disabled_breaker_passes_every_call_through() -> None:
    breaker = make_breaker(FakeClock(), threshold=1, enabled=False)

    for _ in range(10):
        with pytest.raises(RuntimeError):
            breaker.call_sync(_fail)

    assert breaker.state is BreakerState.CLOSED
    assert breaker.call_sync(_ok) == "ok"


def test_from_settings_uses_the_circuit_breaker_block() -> None:
    clock = FakeClock()
    settings = Settings(
        circuit_breaker_enabled=True,
        circuit_breaker_failure_threshold=2,
        circuit_breaker_cooldown_seconds=10.0,
    )
    breaker = CircuitBreaker.from_settings("kafka", settings, clock=clock)

    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call_sync(_fail)
    assert breaker.state is BreakerState.OPEN

    clock.advance(10.0)
    assert breaker.call_sync(_ok) == "ok"
    assert breaker.state is BreakerState.CLOSED


@pytest.mark.parametrize("threshold", [0, -1])
def test_settings_reject_a_non_positive_failure_threshold(threshold: int) -> None:
    with pytest.raises(ValidationError):
        Settings(circuit_breaker_failure_threshold=threshold)


@pytest.mark.parametrize(
    "cooldown",
    [-0.1, float("inf"), float("nan")],
    ids=["negative", "infinity", "nan"],
)
def test_settings_reject_an_invalid_cooldown(cooldown: float) -> None:
    with pytest.raises(ValidationError):
        Settings(circuit_breaker_cooldown_seconds=cooldown)


def test_breaker_states_match_the_metric_label_vocabulary() -> None:
    assert {state.value for state in BreakerState} == set(CIRCUIT_BREAKER_STATES)


def test_unknown_dependency_and_invalid_settings_are_rejected() -> None:
    clock = FakeClock()

    with pytest.raises(ValueError):
        CircuitBreaker("postgres", clock=clock)
    with pytest.raises(ValueError):
        CircuitBreaker("minio", failure_threshold=0, clock=clock)
    with pytest.raises(ValueError):
        CircuitBreaker("minio", cooldown_seconds=-1.0, clock=clock)
