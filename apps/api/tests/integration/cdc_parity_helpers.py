"""Deterministic drivers for the CDC parity harness (T6, AC 2–5).

The parity test must compare two projections fed from the same API writes:

- the fold path, derived from the committed `event_outbox` payloads exactly as
  the relay would publish them, run through the production `ReportsConsumer`;
- the CDC path, derived from the captured OLTP row state (the D8 column sets)
  wrapped in Debezium-shaped envelopes, run through the production
  `CdcSinkConsumer`.

Kafka is deliberately replaced by a one-page in-memory source: the consumers,
their commit-after-write ordering, the mapping and the ClickHouse writers are
the production implementations, while the test needs no broker and reaches
quiescence deterministically (the F7 "consumer to head" pattern without a
fixed sleep). T4's real-broker suite and T7's real-Debezium hop cover the
transport itself.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.attendance.models import AttendanceRecord
from app.domains.employees.models import Employee, Employment
from app.domains.leave.models import LeaveRequest, LeaveType
from app.domains.overtime.models import OvertimeRequest
from app.infrastructure.clickhouse.cdc_projection import ClickhouseCdcWriter
from app.infrastructure.clickhouse.projection import ClickhouseProjectionWriter
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from workers.cdc_sink.consumer import CdcSinkConsumer
from workers.reports.consumer import KafkaMessage, ReportsConsumer

_EPOCH = date(1970, 1, 1)
# A fixed source commit timestamp: the sink versions rows by it, and the
# freshness of the readiness evidence does not matter for a fresh group (D5).
_COMMIT_TS = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
_COMMIT_MS = int(_COMMIT_TS.timestamp() * 1000)

#: The bounded wait for a test-run consumer to reach its commit boundary.
_QUIESCENCE_TIMEOUT_SECONDS = 30.0


class RecordedMessage:
    """The minimal `ConsumerRecord` the consumer ports read."""

    __slots__ = ("value",)

    def __init__(self, value: bytes) -> None:
        self.value: bytes | None = value


class InMemorySource:
    """A one-page event source that records commits (the test's Kafka twin)."""

    def __init__(self, values: Sequence[bytes]) -> None:
        self._values = list(values)
        self._delivered = False
        self.commits = 0

    async def getmany(self, *, timeout_ms: int) -> Mapping[Any, Sequence[KafkaMessage]]:
        if not self._delivered:
            self._delivered = True
            return {0: [RecordedMessage(value) for value in self._values]}
        # Stay responsive to `stop()` without busy-spinning after the page.
        await asyncio.sleep(min(max(timeout_ms / 1000, 0.001), 0.05))
        return {}

    async def commit(self) -> None:
        self.commits += 1


async def _eventually(probe: Callable[[], bool], *, description: str) -> None:
    """Wait for `probe()` under one bounded wait — never a fixed sleep."""

    async def poll() -> None:
        while not probe():
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(poll(), _QUIESCENCE_TIMEOUT_SECONDS)
    except TimeoutError:
        raise AssertionError(f"timed out waiting for {description}") from None


async def run_fold_consumer(
    values: Sequence[bytes], writer: ClickhouseProjectionWriter
) -> None:
    """Drive the production fold consumer over `values` to its last commit."""
    source = InMemorySource(values)
    consumer = ReportsConsumer(
        source,
        writer,
        poll_seconds=0.01,
        backoff_base=0.01,
        backoff_max=0.05,
    )
    task = asyncio.create_task(consumer.run())
    try:
        # The reports loop commits once per handled/skipped message.
        await _eventually(
            lambda: source.commits >= len(values),
            description="the fold consumer to reach head",
        )
    finally:
        consumer.stop()
        await task


async def run_cdc_sink(values: Sequence[bytes], writer: ClickhouseCdcWriter) -> None:
    """Drive the production CDC sink over one snapshot page to its commit."""
    source = InMemorySource(values)
    consumer = CdcSinkConsumer(
        source,
        writer,
        poll_seconds=0.01,
        backoff_base=0.01,
        backoff_max=0.05,
        fresh_group=True,
    )
    task = asyncio.create_task(consumer.run())
    try:
        # The sink commits the whole page once, after rows, fence and offsets.
        await _eventually(
            lambda: source.commits >= 1,
            description="the CDC sink to reach head",
        )
    finally:
        consumer.stop()
        await task


async def outbox_envelopes(session: AsyncSession) -> list[bytes]:
    """The committed outbox payloads in relay (`uuid7`) order."""
    rows = (
        await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    ).scalars()
    return [json.dumps(row.payload).encode("utf-8") for row in rows]


async def captured_envelopes(session: AsyncSession) -> list[bytes]:
    """The captured OLTP row state as Debezium snapshot envelopes.

    Only the D8 columns are selected: identifiers, state, dates and the two
    additive numeric fields (`minutes_late`, overtime `hours`). No PII column
    has a query here, so the CDC path cannot accidentally project one.

    `leave_types` is emitted first so the sink's reference dictionary is warm
    before the leave facts (D7); the final envelope carries
    `source.snapshot = "last"` so the fresh sink flips readiness (D5).
    """
    captured: list[tuple[str, dict[str, Any]]] = []

    leave_types = (
        await session.execute(
            select(LeaveType.id, LeaveType.tenant_id, LeaveType.code).order_by(
                LeaveType.tenant_id, LeaveType.id
            )
        )
    ).all()
    captured.extend(
        (
            "leave_types",
            {
                "id": str(row.id),
                "tenant_id": str(row.tenant_id),
                "code": row.code,
            },
        )
        for row in leave_types
    )

    employees = (
        await session.execute(
            select(
                Employee.id,
                Employee.tenant_id,
                Employee.hired_at,
                Employee.terminated_at,
            ).order_by(Employee.tenant_id, Employee.id)
        )
    ).all()
    captured.extend(
        (
            "employees",
            {
                "id": str(row.id),
                "tenant_id": str(row.tenant_id),
                "hired_at": _epoch_days(row.hired_at),
                "terminated_at": _epoch_days(row.terminated_at),
            },
        )
        for row in employees
    )

    employments = (
        await session.execute(
            select(
                Employment.tenant_id,
                Employment.employee_id,
                Employment.department_id,
                Employment.position_id,
                Employment.location_id,
                Employment.effective_from,
                Employment.effective_to,
            ).order_by(
                Employment.tenant_id,
                Employment.employee_id,
                Employment.effective_from,
            )
        )
    ).all()
    captured.extend(
        (
            "employments",
            {
                "tenant_id": str(row.tenant_id),
                "employee_id": str(row.employee_id),
                "department_id": str(row.department_id),
                "position_id": str(row.position_id),
                "location_id": str(row.location_id),
                "effective_from": _epoch_days(row.effective_from),
                "effective_to": _epoch_days(row.effective_to),
            },
        )
        for row in employments
    )

    attendance = (
        await session.execute(
            select(
                AttendanceRecord.id,
                AttendanceRecord.tenant_id,
                AttendanceRecord.employee_id,
                AttendanceRecord.workday,
                AttendanceRecord.duration_minutes,
                AttendanceRecord.late,
                AttendanceRecord.minutes_late,
            ).order_by(AttendanceRecord.tenant_id, AttendanceRecord.id)
        )
    ).all()
    captured.extend(
        (
            "attendance_records",
            {
                "id": str(row.id),
                "tenant_id": str(row.tenant_id),
                "employee_id": str(row.employee_id),
                "workday": _epoch_days(row.workday),
                "duration_minutes": row.duration_minutes,
                "late": row.late,
                "minutes_late": row.minutes_late,
            },
        )
        for row in attendance
    )

    overtime = (
        await session.execute(
            select(
                OvertimeRequest.id,
                OvertimeRequest.tenant_id,
                OvertimeRequest.employee_id,
                OvertimeRequest.work_date,
                OvertimeRequest.hours,
                OvertimeRequest.status,
            ).order_by(OvertimeRequest.tenant_id, OvertimeRequest.id)
        )
    ).all()
    captured.extend(
        (
            "overtime_requests",
            {
                "id": str(row.id),
                "tenant_id": str(row.tenant_id),
                "employee_id": str(row.employee_id),
                "work_date": _epoch_days(row.work_date),
                "hours": _decimal_text(row.hours),
                "status": row.status,
            },
        )
        for row in overtime
    )

    leaves = (
        await session.execute(
            select(
                LeaveRequest.id,
                LeaveRequest.tenant_id,
                LeaveRequest.employee_id,
                LeaveRequest.leave_type_id,
                LeaveRequest.start_date,
                LeaveRequest.days,
                LeaveRequest.status,
            ).order_by(LeaveRequest.tenant_id, LeaveRequest.id)
        )
    ).all()
    captured.extend(
        (
            "leave_requests",
            {
                "id": str(row.id),
                "tenant_id": str(row.tenant_id),
                "employee_id": str(row.employee_id),
                "leave_type_id": str(row.leave_type_id),
                "start_date": _epoch_days(row.start_date),
                "days": row.days,
                "status": row.status,
            },
        )
        for row in leaves
    )

    last = len(captured) - 1
    return [
        json.dumps(
            _snapshot_envelope(table, after, index=index, last=last == index)
        ).encode("utf-8")
        for index, (table, after) in enumerate(captured)
    ]


def _snapshot_envelope(
    table: str, after: Mapping[str, Any], *, index: int, last: bool
) -> dict[str, Any]:
    """One Debezium-shaped `op: r` snapshot envelope (D9 wire encoding)."""
    return {
        "before": None,
        "after": dict(after),
        "source": {
            "ts_ms": _COMMIT_MS,
            "lsn": index,
            "snapshot": "last" if last else "true",
            "db": "workpulse_test",
            "schema": "public",
            "table": table,
        },
        "op": "r",
        "ts_ms": _COMMIT_MS + index,
        "transaction": None,
    }


def _epoch_days(value: date | None) -> int | None:
    return None if value is None else (value - _EPOCH).days


def _decimal_text(value: Decimal) -> str:
    """Debezium `decimal.handling.mode=string` (D9)."""
    return str(value)


__all__ = [
    "InMemorySource",
    "RecordedMessage",
    "captured_envelopes",
    "outbox_envelopes",
    "run_cdc_sink",
    "run_fold_consumer",
]
