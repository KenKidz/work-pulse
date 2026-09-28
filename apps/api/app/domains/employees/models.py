"""SQLAlchemy models for the Employees domain.

`employees` holds the aggregate plus the denormalized *current* assignment
refs (`department_id`/`position_id`/`location_id`, `manager_id`) used for list
filters and reads; the append-only history lives in `employments` as half-open
`[effective_from, effective_to)` periods (`effective_to IS NULL` = open).

Uniqueness is per tenant: case-insensitive `email` (functional unique index)
and `employee_code`, across ALL rows (terminated employees keep theirs — no
re-hire in Phase 1). `employments` allows at most one open period per employee
(partial unique index) — the DB backstop behind close+append transfers.
Archived organization rows are a *service* rule (archiving is soft): there is
deliberately no DB constraint tying assignment refs to non-archived rows.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid_utils.compat import uuid7

from app.infrastructure.postgres.base import Base


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        # Case-insensitive per-tenant email uniqueness: a functional unique
        # index so the DB backstops the service's `lower()` pre-check exactly.
        Index(
            "uq_employees_tenant_email_lower",
            "tenant_id",
            text("lower(email)"),
            unique=True,
        ),
        UniqueConstraint(
            "tenant_id", "employee_code", name="uq_employees_tenant_employee_code"
        ),
        Index("ix_employees_tenant_id", "tenant_id"),
        Index("ix_employees_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    employee_code: Mapped[str] = mapped_column(String(50), nullable=False)
    first_name: Mapped[str] = mapped_column(String(200), nullable=False)
    last_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    department_id: Mapped[UUID] = mapped_column(
        ForeignKey("departments.id"), nullable=False
    )
    position_id: Mapped[UUID] = mapped_column(
        ForeignKey("positions.id"), nullable=False
    )
    location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id"), nullable=False
    )
    manager_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    employment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    hired_at: Mapped[date] = mapped_column(Date, nullable=False)
    terminated_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Employment(Base):
    """One employment period — rows are appended, never rewritten.

    The only mutation ever applied to a period is closing it (setting
    `effective_to`); the transfer use case appends a new open period instead
    of rewriting the old one (PO Decision #1, ADR 0008).
    """

    __tablename__ = "employments"
    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="period_valid",
        ),
        # At most one open period per employee — the DB backstop behind the
        # transfer/termination close+append flow (no two periods may overlap).
        Index(
            "uq_employments_open_per_employee",
            "tenant_id",
            "employee_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
        Index("ix_employments_tenant_employee_id", "tenant_id", "employee_id"),
        Index("ix_employments_tenant_department_id", "tenant_id", "department_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), nullable=False
    )
    department_id: Mapped[UUID] = mapped_column(
        ForeignKey("departments.id"), nullable=False
    )
    position_id: Mapped[UUID] = mapped_column(
        ForeignKey("positions.id"), nullable=False
    )
    location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id"), nullable=False
    )
    employment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
