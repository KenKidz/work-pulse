"""Payroll worker entry point — `python -m workers.payroll.main` (T8).

Runs inside the `apps/api` uv environment (`make dev-payroll`), reading the same
settings/`.env` as the API and the other workers. Each job opens a fresh
database session that carries the payroll repository and service; the session
commits only after the calculation's state transition, payslips, event
envelope and audit entry are all written (ADR 0012/0018). SIGINT/SIGTERM ask
the worker to stop gracefully: the in-flight run finishes, everything else
stays queued for the next start. Database hiccups are the loop's to handle
(ids-only log + backoff); only configuration errors exit loudly for a
supervisor to act on — restart policy is Phase 8's.

Every seam port is the real adapter (ADR 0018), including the overtime
read/flip seam: a calculation consumes the month's `hr_verified` rows and
persists their handles for finalize exactly as the API's finalize path expects.
`build_job_unit` is the one composition root for that wiring, exported so the
worker path can be tested directly.
"""

import asyncio
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domains.payroll.service import PayrollService
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.engine import dispose_engine, get_session_factory
from app.infrastructure.postgres.outbox import PostgresEventOutbox
from app.infrastructure.postgres.payroll_facts import (
    PostgresAttendanceHoursPort,
    PostgresLeaveDaysPort,
    PostgresOvertimeHoursPort,
)
from app.infrastructure.postgres.repositories.payroll import PostgresPayrollRepository
from app.shared.boot import validate_boot_configuration
from app.shared.logging import configure_logging, get_logger
from app.shared.telemetry import (
    configure_telemetry,
    flush_worker_telemetry,
    start_worker_metrics_server,
)
from workers.payroll.runner import PayrollJobUnit, PayrollRunWorker

_log = get_logger("payroll.main")


def _utc_now() -> datetime:
    """The worker's injected clock (time is a parameter, conventions)."""
    return datetime.now(UTC)


def build_job_unit(session: AsyncSession) -> PayrollJobUnit:
    """The production job unit: real repository, seams and commit boundary."""
    repo = PostgresPayrollRepository(session)
    return PayrollJobUnit(
        repo=repo,
        service=PayrollService(
            repo=repo,
            audit=PostgresAuditWriter(session),
            outbox=PostgresEventOutbox(session, clock=_utc_now),
            overtime=PostgresOvertimeHoursPort(session),
            attendance=PostgresAttendanceHoursPort(session),
            leave_days=PostgresLeaveDaysPort(session),
        ),
        commit=session.commit,
    )


async def _run() -> None:
    settings = get_settings()
    configure_logging(settings.environment)
    configure_telemetry(settings)

    session_factory = get_session_factory()

    @asynccontextmanager
    async def unit() -> AsyncIterator[PayrollJobUnit]:
        """One job's session: repository, service and the commit boundary."""
        async with session_factory() as session:
            yield build_job_unit(session)

    worker = PayrollRunWorker(
        unit,
        batch_size=settings.payroll_worker_batch_size,
        poll_seconds=settings.payroll_worker_poll_seconds,
        backoff_base=settings.payroll_worker_backoff_base_seconds,
        backoff_max=settings.payroll_worker_backoff_max_seconds,
        clock=_utc_now,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop)

    metrics_server = start_worker_metrics_server(settings.payroll_metrics_port)
    run_task = asyncio.create_task(worker.run())
    started_task = asyncio.create_task(worker.started.wait())
    try:
        # Readiness is the first completed poll round (the database accepted a
        # queued-run query), never a sleep. A stop arriving first skips the line.
        await asyncio.wait(
            {run_task, started_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if worker.started.is_set():
            _log.info(
                "payroll_worker_started",
                batch_size=settings.payroll_worker_batch_size,
                poll_seconds=settings.payroll_worker_poll_seconds,
            )
        await run_task
    finally:
        started_task.cancel()
        if metrics_server is not None:
            metrics_server.stop()
        await dispose_engine()
        flush_worker_telemetry()
        _log.info("payroll_worker_stopped")


def main() -> None:
    validate_boot_configuration()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
