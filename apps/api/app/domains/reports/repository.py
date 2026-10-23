"""Domain-owned I/O boundaries for Analytics & Reports (T4, ADR 0019).

Two protocols, both tenant-scoped on every call:

- `ProjectionWriter` is the reports worker's output port. It reads the prior
  row a fold needs (ordered per-employee consumption means one row at a time),
  upserts complete current-state rows, and executes the bounded re-stamp a
  transfer fold requests. The ClickHouse adapter lives in
  `app.infrastructure.clickhouse.projection`; the domain never imports it.
- `ReportsRepository` is the read-only aggregate boundary the API service uses
  (T5). Its adapter gates every query on the rebuild fence and applies
  `tenant_id` before anything else (ADR 0019 decisions 7 and 10).

The worker is a trusted cross-tenant process, but each envelope still carries
its tenant and every read/write is scoped by it — a projection bug must not be
able to cross tenants either.
"""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.domains.reports.schemas import (
    AttendanceAggregateRow,
    AttendanceFact,
    DimensionFold,
    DimensionState,
    HeadcountAggregateRow,
    LeaveAggregateRow,
    LeaveFact,
    OvertimeAggregateRow,
    OvertimeFact,
    ReportQuery,
)


class ProjectionWriter(Protocol):
    """The worker's read-back + upsert boundary over the projection store."""

    def load_dimensions(self, tenant_id: UUID, employee_id: UUID) -> DimensionState:
        """The employee row and all assignment intervals, ordered by start."""
        ...

    def get_overtime(self, tenant_id: UUID, overtime_id: UUID) -> OvertimeFact | None:
        """The request's current projected row, or `None` when unseen."""
        ...

    def get_attendance(
        self, tenant_id: UUID, attendance_id: UUID
    ) -> AttendanceFact | None:
        """The attendance record's current projected row, or `None`."""
        ...

    def get_leave(self, tenant_id: UUID, leave_request_id: UUID) -> LeaveFact | None:
        """The leave request's current projected row, or `None`."""
        ...

    def apply_dimension_fold(self, fold: DimensionFold) -> None:
        """Upsert the employee/intervals and run the fold's re-stamp pass.

        When `fold.restamp_from` is set, every existing fact for that employee
        dated on or after it re-resolves its dimension stamps against the
        updated interval history (ADR 0019 decision 5). The pass is idempotent
        and cannot touch another employee's rows.
        """
        ...

    def upsert_overtime(self, fact: OvertimeFact) -> None:
        """Write one complete current-state overtime row."""
        ...

    def upsert_attendance(self, fact: AttendanceFact) -> None:
        """Write one complete current-state attendance row."""
        ...

    def upsert_leave(self, fact: LeaveFact) -> None:
        """Write one complete current-state leave row."""
        ...


class ReportsRepository(Protocol):
    """Read-only aggregate boundary backing the four reports (T5 adapter)."""

    def ensure_ready(self) -> None:
        """Fail with `ReportsUnavailableError` while a rebuild is in progress.

        A missing `projection_state` row is a fresh projection and is served
        normally; `ready = 0` is the rebuild fence (ADR 0019 decision 7).
        """
        ...

    def overtime_aggregates(self, query: ReportQuery) -> Sequence[OvertimeAggregateRow]:
        """Verified overtime aggregates grouped by bucket and dimensions."""
        ...

    def attendance_aggregates(
        self, query: ReportQuery
    ) -> Sequence[AttendanceAggregateRow]:
        """Attendance aggregates grouped by bucket and dimensions."""
        ...

    def leave_aggregates(self, query: ReportQuery) -> Sequence[LeaveAggregateRow]:
        """Approved leave aggregates grouped by bucket and dimensions."""
        ...

    def headcount_aggregates(
        self, query: ReportQuery
    ) -> Sequence[HeadcountAggregateRow]:
        """Headcount/joiner/leaver aggregates from the dimension tables."""
        ...


__all__ = ["ProjectionWriter", "ReportsRepository"]
