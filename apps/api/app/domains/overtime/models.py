"""SQLAlchemy models for the Overtime domain (Phase 4, T3).

`overtime_requests` is the single aggregate: one claim of hours already worked
on one `work_date`, moving through the pinned two-step state machine
(`requested → manager_approved → hr_verified → included_in_payroll`, with
`rejected` and `withdrawn` terminal side exits). `included_in_payroll` is
written only by payroll's finalize through the ADR 0018 write port — no
overtime endpoint reaches it, hence no FK on `included_in_run_id` (the seam is
cross-context; the id is a handle, not a relational dependency).

`hours` is `Numeric(4, 2)` (ADR 0017 — no floats) with the pinned
`0 < hours <= 12` DB backstop. The partial covering index serves the seam's
month read (`WHERE status = 'hr_verified'`) and is declared in both this model
and migration 0014 (parity rule).

`reason`, `manager_note` and `hr_note` are sensitive free text
(`.ai/security.md`) and are never logged, emitted in events or written into
audit `changes` values.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid_utils.compat import uuid7

from app.infrastructure.postgres.base import Base


class OvertimeRequest(Base):
    """One overtime claim moving through the pinned approval state machine."""

    __tablename__ = "overtime_requests"
    __table_args__ = (
        CheckConstraint("hours > 0 AND hours <= 12", name="hours_valid"),
        CheckConstraint(
            "status IN ('requested', 'manager_approved', 'hr_verified', "
            "'included_in_payroll', 'rejected', 'withdrawn')",
            name="status_valid",
        ),
        CheckConstraint(
            "rejected_from IS NULL OR rejected_from IN "
            "('requested', 'manager_approved', 'hr_verified')",
            name="rejected_from_valid",
        ),
        # Submit's open-request overlap check and the role-scoped list read.
        Index(
            "ix_overtime_requests_tenant_employee_status_work_date",
            "tenant_id",
            "employee_id",
            "status",
            "work_date",
        ),
        # Payroll's seam read (ADR 0018): the month's `hr_verified` rows only,
        # index-only via the included handle columns.
        Index(
            "ix_overtime_requests_verified_seam",
            "tenant_id",
            "work_date",
            postgresql_where=text("status = 'hr_verified'"),
            postgresql_include=["employee_id", "hours"],
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), nullable=False
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    hours: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # PII — never logged, never in events, never in audit `changes` values.
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    manager_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    hr_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_by: Mapped[UUID | None] = mapped_column(nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_by: Mapped[UUID | None] = mapped_column(nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_from: Mapped[str | None] = mapped_column(String(20), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Payroll's accounting handle (ADR 0018 decision 6) — deliberately no FK
    # across the seam boundary.
    included_in_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
