"""SQLAlchemy models for the Leave domain (Phase 3, T3).

`leave_types` is per-tenant reference data: the three seeded codes
(`annual`, `sick`, `unpaid`) are provisioned idempotently by
`ensure_leave_types`; `unpaid` never counts against a balance. `leave_requests`
starts at `PENDING` and moves through the pinned state machine
(`pending → approved|rejected|withdrawn`, all terminal), with the decision
actor columns left null for pending/withdrawn rows. `leave_balances` is the
per `(tenant, employee, type, year)` entitlement/usage pair; the `used_days <=
entitlement_days` check is the DB backstop behind the service's race-safe
conditional deduction, so a balance can never go negative.

`reason` and `decision_note` are sensitive free text (`.ai/security.md`) and
are never logged, emitted in events or written into audit `changes` values.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid_utils.compat import uuid7

from app.infrastructure.postgres.base import Base


class LeaveType(Base):
    """Per-tenant leave-type reference row (seeded, never CRUD-managed in v1)."""

    __tablename__ = "leave_types"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_leave_types_tenant_code"),
        CheckConstraint(
            "default_entitlement_days >= 0",
            name="entitlement_nonnegative",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    counts_against_balance: Mapped[bool] = mapped_column(Boolean, nullable=False)
    default_entitlement_days: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LeaveRequest(Base):
    """One time-off ask moving through the pinned approval state machine."""

    __tablename__ = "leave_requests"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="dates_ordered"),
        CheckConstraint("days >= 1", name="days_positive"),
        # `days` is derived (whole inclusive calendar days) — keep the stored
        # value honest even if a producer is ever bypassed.
        CheckConstraint(
            "days = (end_date - start_date) + 1",
            name="days_match_range",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'withdrawn')",
            name="status_valid",
        ),
        Index(
            "ix_leave_requests_tenant_employee_status_start",
            "tenant_id",
            "employee_id",
            "status",
            "start_date",
        ),
        # Board coverage read (ADR 0014): `tenant_id = ? AND start_date <= d
        # AND end_date >= d` over APPROVED rows only. `end_date` leads the
        # range (a board date only looks at leave still running/future, not
        # the tenant's whole history) and `employee_id` is included so the
        # read is index-only.
        Index(
            "ix_leave_requests_approved_coverage",
            "tenant_id",
            "end_date",
            "start_date",
            postgresql_where=text("status = 'approved'"),
            postgresql_include=["employee_id"],
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), nullable=False
    )
    leave_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("leave_types.id"), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # PII — never logged, never in events, never in audit `changes` values.
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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


class LeaveBalance(Base):
    """Entitlement/usage for one employee, type and year."""

    __tablename__ = "leave_balances"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "employee_id",
            "leave_type_id",
            "year",
            name="uq_leave_balances_tenant_employee_type_year",
        ),
        CheckConstraint(
            "entitlement_days >= 0",
            name="entitlement_nonnegative",
        ),
        CheckConstraint("used_days >= 0", name="used_nonnegative"),
        # The never-negative guard; also the DB backstop for the race-safe
        # `used_days + :days <= entitlement_days` conditional deduction.
        CheckConstraint(
            "used_days <= entitlement_days",
            name="used_within_entitlement",
        ),
        Index(
            "ix_leave_balances_tenant_employee_year",
            "tenant_id",
            "employee_id",
            "year",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), nullable=False
    )
    leave_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("leave_types.id"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    entitlement_days: Mapped[int] = mapped_column(Integer, nullable=False)
    used_days: Mapped[int] = mapped_column(Integer, nullable=False)
