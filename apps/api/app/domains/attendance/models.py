"""SQLAlchemy models for the Attendance domain.

`shifts` is minimal shift reference data (Decision #1): one shift per scope per
tenant — at most one per location, plus at most one tenant default
(`location_id IS NULL`) — enforced by partial unique indexes (the
`shift_scope_taken` backstop). `attendance_records` appends a row per check-in
and closes it at check-out; at most one OPEN record per employee is guaranteed
by a partial unique index (the concurrent double check-in backstop). `late` is
set once at check-in and never cleared; `duration_minutes` is `null` until
check-out, then floor whole minutes. Notes are free text treated like PII and
are never logged or emitted in events (`.ai/security.md`).
"""

from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid_utils.compat import uuid7

from app.infrastructure.postgres.base import Base


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = (
        # At most one shift per location scope …
        Index(
            "uq_shifts_tenant_location",
            "tenant_id",
            "location_id",
            unique=True,
            postgresql_where=text("location_id IS NOT NULL"),
        ),
        # … and at most one tenant-default shift (the null scope).
        Index(
            "uq_shifts_tenant_default",
            "tenant_id",
            unique=True,
            postgresql_where=text("location_id IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    grace_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    location_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("locations.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    __table_args__ = (
        # At most one open record per employee — the DB backstop behind the
        # check-in pre-check and the concurrent two-tab race
        # (`attendance_already_checked_in`).
        Index(
            "uq_attendance_records_open_per_employee",
            "tenant_id",
            "employee_id",
            unique=True,
            postgresql_where=text("checked_out_at IS NULL"),
        ),
        Index("ix_attendance_records_tenant_workday", "tenant_id", "workday"),
        Index(
            "ix_attendance_records_tenant_employee_workday",
            "tenant_id",
            "employee_id",
            "workday",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), nullable=False
    )
    workday: Mapped[date] = mapped_column(Date, nullable=False)
    checked_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    checked_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    work_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    late: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Check-in lateness minutes (rule 5): the CDC projection's source. Null
    # when not late or when the row predates migration 0020; check-out never
    # rewrites it.
    minutes_late: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shift_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("shifts.id"), nullable=True
    )
    check_in_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    check_out_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
