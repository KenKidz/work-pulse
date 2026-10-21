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

Overtime is still the seam placeholder (`NoOvertimeHoursPort`, ADR 0018): until
`overtime.md` (P0) lands a calculation consumes zero verified hours, exactly as
the API does. The real adapter replaces one constructor argument.
"""

import asyncio
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from app.config import get_settings
from app.domains.payroll.repository import NoOvertimeHoursPort
from app.domains.payroll.service import PayrollService
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.engine import dispose_engine, get_session_factory
from app.infrastructure.postgres.outbox import PostgresEventOutbox
from app.infrastructure.postgres.payroll_facts import (
    PostgresAttendanceHoursPort,
    PostgresLeaveDaysPort,
)
from app.infrastructure.postgres.repositories.payroll import PostgresPayrollRepository
from app.shared.logging import configure_logging, get_logger
from workers.payroll.runner import PayrollJobUnit, PayrollRunWorker

_log = get_logger("payroll.main")


def _utc_now() -> datetime:
    """The worker's injected clock (time is a parameter, conventions)."""
    return datetime.now(UTC)


async def _run() -> None:
    settings = get_settings()
    configure_logging(settings.environment)

    session_factory = get_session_factory()

    @asynccontextmanager
    async def unit() -> AsyncIterator[PayrollJobUnit]:
        """One job's session: repository, service and the commit boundary."""
        async with session_factory() as session:
            repo = PostgresPayrollRepository(session)
            yield PayrollJobUnit(
                repo=repo,
                service=PayrollService(
                    repo=repo,
                    audit=PostgresAuditWriter(session),
                    outbox=PostgresEventOutbox(session, clock=_utc_now),
                    overtime=NoOvertimeHoursPort(),
                    attendance=PostgresAttendanceHoursPort(session),
                    leave_days=PostgresLeaveDaysPort(session),
                ),
                commit=session.commit,
            )

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
        await dispose_engine()
        _log.info("payroll_worker_stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
