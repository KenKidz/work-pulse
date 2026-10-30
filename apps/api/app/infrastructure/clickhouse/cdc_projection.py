"""ClickHouse landing adapter for the CDC projection sink (T4, ADR 0024).

Synchronous by design: the sink serializes changes and `clickhouse-connect`
speaks HTTP. Two properties are load-bearing:

- Every row's `updated_at` is the **source commit timestamp** from the
  Debezium envelope (`Change.commit_ts`), never ingest wall clock (rule 7), so
  at-least-once redelivery replaces instead of duplicating.
- Fact rows leave the dimension-stamp columns null: attribution is resolved at
  query time from captured `dim_assignment` intervals (D3), and the sink never
  runs a re-stamp pass.

After the T9 cutover the sink targets the canonical database (empty
`cdc_projection_database` = the `CLICKHOUSE_URL` database); the shadow
database was a parity-only target and is gone. Readiness writes use wall clock
on purpose: the `projection_state` fence is not a projected fact, and its
`ReplacingMergeTree` version must stay newer than a fence written by
`ops/cdc/register.sh`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from clickhouse_connect.driver.client import Client

from app.domains.reports.schemas import (
    AssignmentRow,
    AttendanceFact,
    EmployeeRow,
    LeaveFact,
    OvertimeFact,
)
from app.infrastructure.clickhouse.client import create_clickhouse_client
from app.infrastructure.clickhouse.schema import apply_clickhouse_schema

READINESS_KEY = "reports"
_DATABASE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_EMPLOYEE_COLUMNS = ("tenant_id", "employee_id", "hired_at", "terminated_at")
_ASSIGNMENT_COLUMNS = (
    "tenant_id",
    "employee_id",
    "effective_from",
    "effective_to",
    "department_id",
    "position_id",
    "location_id",
)
_ATTENDANCE_COLUMNS = (
    "tenant_id",
    "attendance_id",
    "employee_id",
    "workday",
    "duration_minutes",
    "late",
    "minutes_late",
    "department_id",
    "position_id",
    "location_id",
    "hired_at",
)
_OVERTIME_COLUMNS = (
    "tenant_id",
    "overtime_id",
    "employee_id",
    "work_date",
    "hours",
    "status",
    "department_id",
    "position_id",
    "location_id",
    "hired_at",
)
_LEAVE_COLUMNS = (
    "tenant_id",
    "leave_request_id",
    "employee_id",
    "start_date",
    "leave_type_code",
    "approved_day_count",
    "department_id",
    "position_id",
    "location_id",
    "hired_at",
)


class ClickhouseCdcWriter:
    """CDC sink output port over one ClickHouse database (canonical by default)."""

    def __init__(
        self,
        client: Client,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._last_readiness_stamp: datetime | None = None
        # Rows are buffered for the current consumer batch and inserted as one
        # block per table on `flush()`, so a snapshot replay does not create one
        # ClickHouse part (and one HTTP round trip) per captured row.
        self._pending: dict[str, tuple[tuple[str, ...], list[list[Any]]]] = {}

    @classmethod
    def connect(cls, database: str | None = None) -> ClickhouseCdcWriter:
        """Open a client for `database` (empty = the DSN database).

        A named target database is created first from a base connection, then a
        database-scoped client is opened so `schema.sql`'s unqualified DDL lands
        in that database.
        """
        target = (database or "").strip()
        if not target:
            return cls(create_clickhouse_client())
        if _DATABASE_PATTERN.fullmatch(target) is None:
            raise ValueError(f"invalid ClickHouse database name: {target!r}")

        base = create_clickhouse_client()
        try:
            base.command(f"CREATE DATABASE IF NOT EXISTS {target}")
        finally:
            base.close()
        return cls(create_clickhouse_client(database=target))

    def close(self) -> None:
        self._client.close()

    def apply_schema(self) -> None:
        """Apply the projection DDL in this writer's target database."""
        apply_clickhouse_schema(self._client)

    # --- Readiness fence (D5) -----------------------------------------

    def readiness(self) -> tuple[int, datetime] | None:
        """The current `(ready, fence timestamp)`, or `None` for no marker.

        The fence timestamp is the rebuild epoch: evidence older than it is
        history replayed from before the fence and must not flip readiness.
        """
        rows = self._client.query(
            "SELECT ready, updated_at FROM projection_state FINAL "
            "WHERE key = {key:String}",
            parameters={"key": READINESS_KEY},
        ).result_rows
        if not rows:
            return None
        ready, fenced_at = rows[0]
        if fenced_at.tzinfo is None:
            fenced_at = fenced_at.replace(tzinfo=UTC)
        return int(ready), fenced_at.astimezone(UTC)

    def mark_rebuilding(self) -> None:
        """Fence report reads (`ready = 0`) while a snapshot is landing."""
        self._write_readiness(0)

    def mark_ready(self) -> None:
        """Flip the fence after snapshot-done evidence (`ready = 1`)."""
        self._write_readiness(1)

    def flush(self) -> None:
        """Insert every buffered table's rows as one ClickHouse block.

        Tables are cleared as their insert succeeds: a retry after a later
        table failed re-sends only the failed table, and the whole batch stays
        idempotent under `ReplacingMergeTree(updated_at)`.
        """
        while self._pending:
            table, (columns, rows) = next(iter(self._pending.items()))
            self._client.insert(
                table,
                rows,
                column_names=[*columns, "updated_at"],
            )
            del self._pending[table]

    # --- Dimension upserts --------------------------------------------

    def upsert_employee(self, row: EmployeeRow, updated_at: datetime) -> None:
        self._write(
            "dim_employee",
            _EMPLOYEE_COLUMNS,
            (
                row.tenant_id,
                row.employee_id,
                row.hired_at,
                row.terminated_at,
            ),
            updated_at,
        )

    def upsert_assignment(self, row: AssignmentRow, updated_at: datetime) -> None:
        self._write(
            "dim_assignment",
            _ASSIGNMENT_COLUMNS,
            (
                row.tenant_id,
                row.employee_id,
                row.effective_from,
                row.effective_to,
                row.department_id,
                row.position_id,
                row.location_id,
            ),
            updated_at,
        )

    # --- Fact upserts -------------------------------------------------

    def upsert_attendance(self, fact: AttendanceFact, updated_at: datetime) -> None:
        self._write(
            "fact_attendance",
            _ATTENDANCE_COLUMNS,
            (
                fact.tenant_id,
                fact.attendance_id,
                fact.employee_id,
                fact.workday,
                fact.duration_minutes,
                fact.late,
                fact.minutes_late,
                fact.department_id,
                fact.position_id,
                fact.location_id,
                fact.hired_at,
            ),
            updated_at,
        )

    def upsert_overtime(self, fact: OvertimeFact, updated_at: datetime) -> None:
        self._write(
            "fact_overtime",
            _OVERTIME_COLUMNS,
            (
                fact.tenant_id,
                fact.overtime_id,
                fact.employee_id,
                fact.work_date,
                fact.hours,
                fact.status,
                fact.department_id,
                fact.position_id,
                fact.location_id,
                fact.hired_at,
            ),
            updated_at,
        )

    def upsert_leave(self, fact: LeaveFact, updated_at: datetime) -> None:
        self._write(
            "fact_leave",
            _LEAVE_COLUMNS,
            (
                fact.tenant_id,
                fact.leave_request_id,
                fact.employee_id,
                fact.start_date,
                fact.leave_type_code,
                fact.approved_day_count,
                fact.department_id,
                fact.position_id,
                fact.location_id,
                fact.hired_at,
            ),
            updated_at,
        )

    # --- Internals ----------------------------------------------------

    def _write(
        self,
        table: str,
        columns: tuple[str, ...],
        values: tuple[Any, ...],
        updated_at: datetime,
    ) -> None:
        pending = self._pending.get(table)
        if pending is None:
            self._pending[table] = (columns, [[*values, updated_at]])
            return
        pending_columns, rows = pending
        if pending_columns != columns:  # pragma: no cover - internal invariant
            raise ValueError(f"column set changed while buffering {table!r}")
        rows.append([*values, updated_at])

    def _write_readiness(self, ready: int) -> None:
        # A readiness write must never overtake buffered projection rows: the
        # reports read gate checks this marker before reading the tables.
        self.flush()
        self._client.insert(
            "projection_state",
            [[READINESS_KEY, ready, self._next_readiness_stamp()]],
            column_names=["key", "ready", "updated_at"],
        )

    def _next_readiness_stamp(self) -> datetime:
        """Wall-clock fence version, strictly increasing in this process.

        `register.sh` seeds a later wall-clock stamp than any source commit
        timestamp, so the sink's flip must also use wall clock or the
        `ReplacingMergeTree` would keep the older `ready = 0` row.
        """
        now = self._clock()
        if self._last_readiness_stamp is not None and now <= self._last_readiness_stamp:
            now = self._last_readiness_stamp + timedelta(microseconds=1)
        self._last_readiness_stamp = now
        return now


__all__ = ["ClickhouseCdcWriter"]
