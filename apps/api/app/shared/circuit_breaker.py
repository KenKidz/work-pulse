"""Per-process circuit breaker for non-critical outbound calls (Phase 7).

Breakers wrap only dependencies whose failure does not make the whole monolith
unusable (ADR 0025): the relay's Kafka publish, the MinIO storage client and
the Resend/Slack adapters. PostgreSQL, Redis and ClickHouse stay unwrapped —
when they are down, the write/read path is already total, so a breaker would
only add latency to a dead path (business rule 6).

Semantics (business rule 7):

- **closed** — calls pass through. Consecutive failures open the breaker at
  ``failure_threshold`` (default 5).
- **open** — calls fail fast with :class:`CircuitOpenError` and **no outbound
  I/O**. After ``cooldown_seconds`` (default 30) the breaker turns half-open.
- **half-open** — exactly one trial call is admitted; concurrent callers fail
  fast until the trial resolves. Success closes the breaker (and resets the
  failure count); failure re-opens it with a fresh cooldown.

State lives in memory per process, matching the v1 deployment (one uvicorn, one
process per worker). Multi-process deployments would need shared state; that
is deliberately out of scope and recorded in ADR 0025.

Domains never import this module (AGENTS.md architecture rule 3). It is
applied inside infrastructure adapters and worker call sites, and its
``CircuitOpenError`` maps to the house ``503 dependency_unavailable`` shape via
the existing ``WorkPulseError`` handler.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TypeVar

from app.config import Settings, get_settings
from app.shared.errors import DependencyUnavailableError
from app.shared.telemetry import (
    record_circuit_breaker_rejected,
    record_circuit_breaker_state,
    record_circuit_breaker_transition,
)

T = TypeVar("T")

#: Closed vocabulary for the `dependency` metric label / breaker identity.
CIRCUIT_BREAKER_DEPENDENCIES = frozenset({"kafka", "minio", "resend", "slack"})

DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_COOLDOWN_SECONDS = 30.0


class BreakerState(StrEnum):
    """The three breaker states; values are the bounded metric label."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(DependencyUnavailableError):
    """A wrapped dependency's breaker is open — fail fast, no outbound I/O.

    Subclasses :class:`DependencyUnavailableError`, so the shared error handler
    renders ``503 {"error": {"code": "dependency_unavailable", ...}}`` without
    breaker-specific HTTP translation. The message never names the dependency:
    the dependency identity is for logs/metrics, not for clients.
    """

    def __init__(self, dependency: str) -> None:
        self.dependency = dependency
        super().__init__()


class CircuitBreaker:
    """Thread-safe closed → open → half-open state machine for one dependency.

    ``clock`` is injectable (monotonic seconds) so tests drive cooldown expiry
    without sleeping. The lock only guards in-memory state; it is never held
    across the wrapped operation.
    """

    def __init__(
        self,
        dependency: str,
        *,
        enabled: bool = True,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if dependency not in CIRCUIT_BREAKER_DEPENDENCIES:
            raise ValueError(
                f"Unknown circuit-breaker dependency {dependency!r}; "
                f"expected one of {sorted(CIRCUIT_BREAKER_DEPENDENCIES)}"
            )
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")

        self._dependency = dependency
        self._enabled = enabled
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0

        record_circuit_breaker_state(self._dependency, self._state.value)

    @classmethod
    def from_settings(
        cls,
        dependency: str,
        settings: Settings | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> CircuitBreaker:
        """Build a breaker from the ``CIRCUIT_BREAKER_*`` Settings block."""
        resolved = settings if settings is not None else get_settings()
        return cls(
            dependency,
            enabled=resolved.circuit_breaker_enabled,
            failure_threshold=resolved.circuit_breaker_failure_threshold,
            cooldown_seconds=resolved.circuit_breaker_cooldown_seconds,
            clock=clock,
        )

    @property
    def dependency(self) -> str:
        return self._dependency

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def state(self) -> BreakerState:
        with self._lock:
            return self._state

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run an async operation through the breaker."""
        trial = self._acquire()
        try:
            result = await operation()
        except Exception:
            self._record_failure(trial)
            raise
        except BaseException:
            # Cancellation/SystemExit must not leave the single half-open trial
            # stuck forever; release it without counting a dependency failure.
            self._release(trial)
            raise
        else:
            self._record_success(trial)
            return result

    def call_sync(self, operation: Callable[[], T]) -> T:
        """Run a synchronous operation through the breaker."""
        trial = self._acquire()
        try:
            result = operation()
        except Exception:
            self._record_failure(trial)
            raise
        except BaseException:
            self._release(trial)
            raise
        else:
            self._record_success(trial)
            return result

    def _acquire(self) -> bool:
        """Admit a call; return True when it is the half-open trial.

        Raises :class:`CircuitOpenError` while open (before cooldown) or while
        another caller holds the half-open trial — never performing I/O.
        """
        with self._lock:
            if not self._enabled:
                return False

            if self._state is BreakerState.CLOSED:
                return False

            if self._state is BreakerState.HALF_OPEN:
                record_circuit_breaker_rejected(self._dependency)
                raise CircuitOpenError(self._dependency)

            # OPEN: only the cooldown boundary admits the single trial call.
            if self._clock() - self._opened_at < self._cooldown_seconds:
                record_circuit_breaker_rejected(self._dependency)
                raise CircuitOpenError(self._dependency)

            # HALF_OPEN itself is the single-trial permit: `_acquire` rejects
            # every caller until this trial records success/failure.
            self._transition(BreakerState.HALF_OPEN)
            return True

    def _record_success(self, trial: bool) -> None:
        with self._lock:
            if not self._enabled:
                return
            if trial:
                # Only the admitted trial may close the breaker; a late success
                # from a call admitted while closed is ignored.
                if self._state is BreakerState.HALF_OPEN:
                    self._consecutive_failures = 0
                    self._transition(BreakerState.CLOSED)
                return
            if self._state is BreakerState.CLOSED:
                self._consecutive_failures = 0

    def _record_failure(self, trial: bool) -> None:
        with self._lock:
            if not self._enabled:
                return
            if trial:
                if self._state is BreakerState.HALF_OPEN:
                    self._open()
                return
            if self._state is not BreakerState.CLOSED:
                # A straggler admitted before the breaker opened must not add
                # failures to, or disturb, the open state.
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._open()

    def _release(self, trial: bool) -> None:
        """Release a half-open trial that ended without a success/failure."""
        if not trial:
            return
        with self._lock:
            if self._state is BreakerState.HALF_OPEN:
                self._open()

    def _open(self) -> None:
        self._opened_at = self._clock()
        self._transition(BreakerState.OPEN)

    def _transition(self, state: BreakerState) -> None:
        """Record a transition; callers hold the lock."""
        if state is self._state:
            return
        self._state = state
        record_circuit_breaker_transition(self._dependency, state.value)
        record_circuit_breaker_state(self._dependency, state.value)


__all__ = [
    "BreakerState",
    "CIRCUIT_BREAKER_DEPENDENCIES",
    "CircuitBreaker",
    "CircuitOpenError",
]
