"""The payroll run job loop: `payroll_runs` → `PayrollService.calculate_run`.

One instance (v1). Payroll calculation is deliberately asynchronous (ADR 0003):
the create/recalculate endpoints only queue a run, and this worker performs the
`QUEUED → CALCULATING → CALCULATED | FAILED` transition outside the request.

Claiming is the service's state-guarded transition (`WHERE status = 'queued'`),
the exactly-one-wins primitive shared with finalize (spec R6): two workers that
see the same queued id both attempt the claim, one wins, the loser's pass
returns the already-moved run without replacing the result set. The run's
calculation, its provisional payslips, the `payroll.calculated` outbox envelope
and the run's audit entry commit together in the unit-of-work transaction
(ADR 0012/0018).

A failed calculation is a persisted `FAILED` state with a stable, PII-free
`failed_code`, recoverable through `recalculate`; an *infrastructure* failure
that prevents even that transition leaves the run `QUEUED` and is retried with
bounded exponential backoff. The loop never dies on a database hiccup (no
supervisor exists in v1) and logs ids only — amounts, notes and payslip lines
are PII and never reach a log line (`.ai/security.md`).

The unit-of-work port is structural, so tests drive this exact loop with an
in-memory or real-session fake (`.ai/testing.md`).
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.domains.payroll.schemas import PayrollRun
from app.shared.logging import get_logger
from app.shared.telemetry import worker_unit

_log = get_logger("payroll.worker")

#: Bounded synthetic unit label for payroll jobs: a queued run is work, not a
#: domain event, so it has no `event_type` on the wire (ADR 0023).
PAYROLL_RUN_UNIT = "payroll.run"

# A bounded exponent: an unbounded `2 ** n` overflows to a float error after
# ~1025 consecutive failures and would kill the loop mid-outage.
_MAX_BACKOFF_EXPONENT = 32


def _utc_now() -> datetime:
    """The worker's injected clock (time is a parameter, `.ai/conventions.md`)."""
    return datetime.now(UTC)


class QueuedRunRepository(Protocol):
    """Worker-side read port — `PostgresPayrollRepository` satisfies it."""

    async def queued_run_ids(self, limit: int) -> Sequence[UUID]:
        """The oldest `QUEUED` run ids across tenants."""
        ...


class RunCalculator(Protocol):
    """The one application operation the worker invokes (`PayrollService`)."""

    async def calculate_run(
        self, run_id: UUID, now: datetime, *, tenant_id: UUID | None = None
    ) -> PayrollRun: ...


class JobUnit(Protocol):
    """The two operations one unit of work exposes to the loop.

    Structural on purpose: `main` supplies the Postgres-backed
    `PayrollJobUnit`, while tests wrap it (for example to synchronize a
    concurrent-claim race) without subclassing a frozen dataclass.
    """

    async def queued_run_ids(self, limit: int) -> Sequence[UUID]: ...

    async def calculate(self, run_id: UUID, now: datetime) -> PayrollRun: ...


@dataclass(frozen=True, slots=True)
class PayrollJobUnit:
    """One fresh-session unit of work for the worker.

    A unit is opened per pass (listing) and per run (calculation), so a failed
    transaction never leaks into the next job. `commit` is the session's
    commit: a calculation is only durable — and only then stops being queued —
    after it returns.
    """

    repo: QueuedRunRepository
    service: RunCalculator
    commit: Callable[[], Awaitable[None]]

    async def queued_run_ids(self, limit: int) -> Sequence[UUID]:
        return await self.repo.queued_run_ids(limit)

    async def calculate(self, run_id: UUID, now: datetime) -> PayrollRun:
        run = await self.service.calculate_run(run_id, now)
        await self.commit()
        return run


class UnitFactory(Protocol):
    """Opens a `JobUnit` context (`main` wires a DB session inside)."""

    def __call__(self) -> AbstractAsyncContextManager[JobUnit]: ...


class PayrollRunWorker:
    """The poll → claim/calculate loop for queued payroll runs."""

    def __init__(
        self,
        unit_factory: UnitFactory,
        *,
        batch_size: int,
        poll_seconds: float,
        backoff_base: float,
        backoff_max: float,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be > 0")
        if backoff_base <= 0 or backoff_max < backoff_base:
            raise ValueError("backoff_max must be >= backoff_base > 0")
        self._unit_factory = unit_factory
        self._batch_size = batch_size
        self._poll_seconds = poll_seconds
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._clock = clock
        self._stopping = asyncio.Event()
        # Set after the first successful queued-run listing: the database is
        # reachable and the loop is ready — readiness for the gate's log line
        # and tests, never a sleep (the relay/consumer precedent). It is set
        # before the first batch runs, so a startup backlog cannot delay it.
        self.started = asyncio.Event()

    def stop(self) -> None:
        """Ask the loop to stop gracefully (idempotent, signal-handler safe).

        Checked before each poll and between runs: an in-flight calculation
        always commits (or rolls back) first, and the next run stays queued for
        the next process start.
        """
        self._stopping.set()

    async def run(self) -> None:
        """Poll queued runs until `stop()`; hiccups back off, never kill the loop."""
        consecutive_failures = 0
        while not self._stopping.is_set():
            try:
                processed, failures = await self._drain()
            except Exception as exc:
                # A database hiccup must not kill the worker: back off and
                # retry. The type only — driver text can embed statement data.
                consecutive_failures += 1
                _log.warning("payroll_worker_pass_failed", error=type(exc).__name__)
                await self._pause(self._backoff_seconds(consecutive_failures))
                continue
            if self._stopping.is_set():
                break
            if failures:
                consecutive_failures += failures
                await self._pause(self._backoff_seconds(consecutive_failures))
                continue
            consecutive_failures = 0
            if processed < self._batch_size:
                # A short batch means the queue is drained: wait for new work
                # instead of busy-polling.
                await self._pause(self._poll_seconds)

    async def run_once(self) -> int:
        """One poll/calculation pass — the deterministic unit for tests.

        Returns the number of runs processed (including runs persisted as
        `FAILED`). A listing failure propagates; a per-run failure is logged
        and leaves that run queued for a later pass.
        """
        processed, _ = await self._drain()
        return processed

    async def _drain(self) -> tuple[int, int]:
        """List one batch and calculate each run in its own transaction."""
        async with self._unit_factory() as unit:
            run_ids = list(await unit.queued_run_ids(self._batch_size))
        # The database accepted the poll: the loop is ready even if the first
        # batch is long. A listing failure leaves this unset and backs off.
        self.started.set()

        processed = 0
        failures = 0
        for run_id in run_ids:
            if self._stopping.is_set():
                break  # this run stays queued for the next process start
            async with worker_unit("payroll", PAYROLL_RUN_UNIT) as unit_span:
                try:
                    async with self._unit_factory() as unit:
                        run = await unit.calculate(run_id, self._clock())
                except Exception as exc:
                    # The unit's transaction rolled back; the run is still
                    # queued and the next pass retries it after a bounded
                    # backoff. Logged inside the span so the failure line
                    # stays trace-correlated.
                    unit_span.mark_failed(type(exc).__name__)
                    failures += 1
                    _log.warning(
                        "payroll_run_failed",
                        run_id=str(run_id),
                        error=type(exc).__name__,
                    )
                    continue
                processed += 1
                _log.info(
                    "payroll_run_processed",
                    run_id=str(run_id),
                    status=run.status.value,
                )
        return processed, failures

    def _backoff_seconds(self, consecutive_failures: int) -> float:
        """Exponential between polls: base, 2×, 4×, … capped at `backoff_max`."""
        exponent = min(max(consecutive_failures - 1, 0), _MAX_BACKOFF_EXPONENT)
        return min(self._backoff_base * 2**exponent, self._backoff_max)

    async def _pause(self, seconds: float) -> None:
        """Sleep, but wake immediately on `stop()`."""
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except TimeoutError:
            pass
