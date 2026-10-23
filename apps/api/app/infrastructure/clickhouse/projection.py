"""ClickHouse implementation of the reports projection writer (T4, ADR 0019).

Synchronous by design: the worker serializes events, and `clickhouse-connect`
speaks the HTTP interface. Every write is a complete-row upsert keyed by the
ADR's ordering key with a fresh, monotonically increasing `updated_at` (the
`ReplacingMergeTree` version), so a redelivered envelope converges to the same
row and a transfer correction replaces stale dimension stamps. Reads use
`FINAL` — omitting it would expose duplicate parts and double aggregates.

The bounded re-stamp pass runs inside `apply_dimension_fold`: after the new
interval history is written, every fact for that one employee dated on or after
the transfer's `effective_from` re-resolves its stamp. Facts before the date are
never touched, and the pass is idempotent (ADR 0019 decision 5).
"""

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from uuid import UUID

from clickhouse_connect.driver.client import Client

from app.domains.reports.folds import stamp_for
from app.domains.reports.schemas import (
    AssignmentRow,
    AttendanceFact,
    DimensionFold,
    DimensionStamp,
    DimensionState,
    EmployeeRow,
    LeaveFact,
    OvertimeFact,
)

_HUNDREDTH = Decimal("0.01")

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


def _decimal(value: object) -> Decimal:
    """Parse a ClickHouse decimal without going through binary float."""
    return Decimal(str(value)).quantize(_HUNDREDTH, rounding=ROUND_HALF_EVEN)


class ClickhouseProjectionWriter:
    """`ProjectionWriter` over the ADR 0019 ClickHouse tables."""

    def __init__(
        self,
        client: Client,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._last_stamp: datetime | None = None

    # --- Reads ---------------------------------------------------------

    def load_dimensions(self, tenant_id: UUID, employee_id: UUID) -> DimensionState:
        employee_rows = self._rows(
            "SELECT hired_at, terminated_at FROM dim_employee FINAL "
            "WHERE tenant_id = {tenant_id:UUID} "
            "AND employee_id = {employee_id:UUID}",
            {"tenant_id": tenant_id, "employee_id": employee_id},
        )
        employee = None
        if employee_rows:
            hired_at, terminated_at = employee_rows[0]
            employee = EmployeeRow(
                tenant_id=tenant_id,
                employee_id=employee_id,
                hired_at=hired_at,
                terminated_at=terminated_at,
            )

        assignment_rows = self._rows(
            "SELECT effective_from, effective_to, department_id, position_id, "
            "location_id FROM dim_assignment FINAL "
            "WHERE tenant_id = {tenant_id:UUID} "
            "AND employee_id = {employee_id:UUID} "
            "ORDER BY effective_from",
            {"tenant_id": tenant_id, "employee_id": employee_id},
        )
        assignments = tuple(
            AssignmentRow(
                tenant_id=tenant_id,
                employee_id=employee_id,
                effective_from=row[0],
                effective_to=row[1],
                department_id=row[2],
                position_id=row[3],
                location_id=row[4],
            )
            for row in assignment_rows
        )
        return DimensionState(employee=employee, assignments=assignments)

    def get_overtime(self, tenant_id: UUID, overtime_id: UUID) -> OvertimeFact | None:
        rows = self._rows(
            "SELECT employee_id, work_date, hours, status, department_id, "
            "position_id, location_id, hired_at FROM fact_overtime FINAL "
            "WHERE tenant_id = {tenant_id:UUID} "
            "AND overtime_id = {overtime_id:UUID}",
            {"tenant_id": tenant_id, "overtime_id": overtime_id},
        )
        if not rows:
            return None
        row = rows[0]
        return OvertimeFact(
            tenant_id=tenant_id,
            overtime_id=overtime_id,
            employee_id=row[0],
            work_date=row[1],
            hours=_decimal(row[2]),
            status=row[3],
            department_id=row[4],
            position_id=row[5],
            location_id=row[6],
            hired_at=row[7],
        )

    def get_attendance(
        self, tenant_id: UUID, attendance_id: UUID
    ) -> AttendanceFact | None:
        rows = self._rows(
            "SELECT employee_id, workday, duration_minutes, late, minutes_late, "
            "department_id, position_id, location_id, hired_at "
            "FROM fact_attendance FINAL "
            "WHERE tenant_id = {tenant_id:UUID} "
            "AND attendance_id = {attendance_id:UUID}",
            {"tenant_id": tenant_id, "attendance_id": attendance_id},
        )
        if not rows:
            return None
        row = rows[0]
        return AttendanceFact(
            tenant_id=tenant_id,
            attendance_id=attendance_id,
            employee_id=row[0],
            workday=row[1],
            duration_minutes=row[2],
            late=row[3],
            minutes_late=row[4],
            department_id=row[5],
            position_id=row[6],
            location_id=row[7],
            hired_at=row[8],
        )

    def get_leave(self, tenant_id: UUID, leave_request_id: UUID) -> LeaveFact | None:
        rows = self._rows(
            "SELECT employee_id, start_date, leave_type_code, "
            "approved_day_count, department_id, position_id, location_id, "
            "hired_at FROM fact_leave FINAL "
            "WHERE tenant_id = {tenant_id:UUID} "
            "AND leave_request_id = {leave_request_id:UUID}",
            {"tenant_id": tenant_id, "leave_request_id": leave_request_id},
        )
        if not rows:
            return None
        row = rows[0]
        return LeaveFact(
            tenant_id=tenant_id,
            leave_request_id=leave_request_id,
            employee_id=row[0],
            start_date=row[1],
            leave_type_code=row[2],
            approved_day_count=row[3],
            department_id=row[4],
            position_id=row[5],
            location_id=row[6],
            hired_at=row[7],
        )

    # --- Dimension writes + transfer correction ------------------------

    def apply_dimension_fold(self, fold: DimensionFold) -> None:
        if fold.employee is not None:
            employee = fold.employee
            self._write(
                "dim_employee",
                _EMPLOYEE_COLUMNS,
                (
                    employee.tenant_id,
                    employee.employee_id,
                    employee.hired_at,
                    employee.terminated_at,
                ),
            )
        for assignment in fold.assignments:
            self._write(
                "dim_assignment",
                _ASSIGNMENT_COLUMNS,
                (
                    assignment.tenant_id,
                    assignment.employee_id,
                    assignment.effective_from,
                    assignment.effective_to,
                    assignment.department_id,
                    assignment.position_id,
                    assignment.location_id,
                ),
            )

        if fold.restamp_from is not None:
            identity = _fold_identity(fold)
            if identity is not None:
                self._restamp_facts(identity[0], identity[1], since=fold.restamp_from)

    # --- Fact writes ---------------------------------------------------

    def upsert_overtime(self, fact: OvertimeFact) -> None:
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
        )

    def upsert_attendance(self, fact: AttendanceFact) -> None:
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
        )

    def upsert_leave(self, fact: LeaveFact) -> None:
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
        )

    # --- Internals -----------------------------------------------------

    def _restamp_facts(
        self, tenant_id: UUID, employee_id: UUID, *, since: date
    ) -> None:
        state = self.load_dimensions(tenant_id, employee_id)
        self._restamp_overtime(tenant_id, employee_id, state, since)
        self._restamp_attendance(tenant_id, employee_id, state, since)
        self._restamp_leave(tenant_id, employee_id, state, since)

    def _restamp_overtime(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        state: DimensionState,
        since: date,
    ) -> None:
        rows = self._rows(
            "SELECT overtime_id, work_date, hours, status, department_id, "
            "position_id, location_id, hired_at FROM fact_overtime FINAL "
            "WHERE tenant_id = {tenant_id:UUID} "
            "AND employee_id = {employee_id:UUID} "
            "AND work_date >= {since:Date}",
            {"tenant_id": tenant_id, "employee_id": employee_id, "since": since},
        )
        for row in rows:
            fact = OvertimeFact(
                tenant_id=tenant_id,
                overtime_id=row[0],
                employee_id=employee_id,
                work_date=row[1],
                hours=_decimal(row[2]),
                status=row[3],
                department_id=row[4],
                position_id=row[5],
                location_id=row[6],
                hired_at=row[7],
            )
            stamp = stamp_for(state, fact.work_date)
            if _stamp_changed(fact, stamp):
                self.upsert_overtime(
                    OvertimeFact(
                        tenant_id=fact.tenant_id,
                        overtime_id=fact.overtime_id,
                        employee_id=fact.employee_id,
                        work_date=fact.work_date,
                        hours=fact.hours,
                        status=fact.status,
                        department_id=stamp.department_id,
                        position_id=stamp.position_id,
                        location_id=stamp.location_id,
                        hired_at=stamp.hired_at,
                    )
                )

    def _restamp_attendance(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        state: DimensionState,
        since: date,
    ) -> None:
        rows = self._rows(
            "SELECT attendance_id, workday, duration_minutes, late, minutes_late, "
            "department_id, position_id, location_id, hired_at "
            "FROM fact_attendance FINAL "
            "WHERE tenant_id = {tenant_id:UUID} "
            "AND employee_id = {employee_id:UUID} "
            "AND workday >= {since:Date}",
            {"tenant_id": tenant_id, "employee_id": employee_id, "since": since},
        )
        for row in rows:
            fact = AttendanceFact(
                tenant_id=tenant_id,
                attendance_id=row[0],
                employee_id=employee_id,
                workday=row[1],
                duration_minutes=row[2],
                late=row[3],
                minutes_late=row[4],
                department_id=row[5],
                position_id=row[6],
                location_id=row[7],
                hired_at=row[8],
            )
            stamp = stamp_for(state, fact.workday)
            if _stamp_changed(fact, stamp):
                self.upsert_attendance(
                    AttendanceFact(
                        tenant_id=fact.tenant_id,
                        attendance_id=fact.attendance_id,
                        employee_id=fact.employee_id,
                        workday=fact.workday,
                        duration_minutes=fact.duration_minutes,
                        late=fact.late,
                        minutes_late=fact.minutes_late,
                        department_id=stamp.department_id,
                        position_id=stamp.position_id,
                        location_id=stamp.location_id,
                        hired_at=stamp.hired_at,
                    )
                )

    def _restamp_leave(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        state: DimensionState,
        since: date,
    ) -> None:
        rows = self._rows(
            "SELECT leave_request_id, start_date, leave_type_code, "
            "approved_day_count, department_id, position_id, location_id, "
            "hired_at FROM fact_leave FINAL "
            "WHERE tenant_id = {tenant_id:UUID} "
            "AND employee_id = {employee_id:UUID} "
            "AND start_date >= {since:Date}",
            {"tenant_id": tenant_id, "employee_id": employee_id, "since": since},
        )
        for row in rows:
            fact = LeaveFact(
                tenant_id=tenant_id,
                leave_request_id=row[0],
                employee_id=employee_id,
                start_date=row[1],
                leave_type_code=row[2],
                approved_day_count=row[3],
                department_id=row[4],
                position_id=row[5],
                location_id=row[6],
                hired_at=row[7],
            )
            stamp = stamp_for(state, fact.start_date)
            if _stamp_changed(fact, stamp):
                self.upsert_leave(
                    LeaveFact(
                        tenant_id=fact.tenant_id,
                        leave_request_id=fact.leave_request_id,
                        employee_id=fact.employee_id,
                        start_date=fact.start_date,
                        leave_type_code=fact.leave_type_code,
                        approved_day_count=fact.approved_day_count,
                        department_id=stamp.department_id,
                        position_id=stamp.position_id,
                        location_id=stamp.location_id,
                        hired_at=stamp.hired_at,
                    )
                )

    def _write(
        self,
        table: str,
        columns: Sequence[str],
        values: Sequence[Any],
    ) -> None:
        self._client.insert(
            table,
            [[*values, self._next_stamp()]],
            column_names=[*columns, "updated_at"],
        )

    def _rows(self, sql: str, parameters: dict[str, Any]) -> Sequence[Sequence[Any]]:
        return self._client.query(sql, parameters=parameters).result_rows

    def _next_stamp(self) -> datetime:
        """A strictly increasing bookkeeping version for this process.

        ReplacingMergeTree resolves equal versions non-deterministically; the
        monotonic guard keeps a correction newer than the row it replaces even
        when two writes land in the same microsecond.
        """
        now = self._clock()
        if self._last_stamp is not None and now <= self._last_stamp:
            now = self._last_stamp + timedelta(microseconds=1)
        self._last_stamp = now
        return now


def _fold_identity(fold: DimensionFold) -> tuple[UUID, UUID] | None:
    if fold.employee is not None:
        return fold.employee.tenant_id, fold.employee.employee_id
    if fold.assignments:
        assignment = fold.assignments[0]
        return assignment.tenant_id, assignment.employee_id
    return None


def _stamp_changed(
    fact: OvertimeFact | AttendanceFact | LeaveFact, stamp: DimensionStamp
) -> bool:
    return (
        fact.department_id,
        fact.position_id,
        fact.location_id,
        fact.hired_at,
    ) != (
        stamp.department_id,
        stamp.position_id,
        stamp.location_id,
        stamp.hired_at,
    )
