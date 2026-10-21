"""SQLAlchemy models for the Payroll domain (T2, ADR 0017).

Payroll is a deterministic simulation (ADR 0003), not a compliance engine:
money is stored as `numeric(12,2)` and read as `Decimal` (ADR 0017), every row
is tenant-scoped (`.ai/security.md`), and amount-bearing columns are PII — they
never appear in logs, error messages, audit `changes` values or events.

Tables:

- `payroll_configs` — one row per tenant: single currency, standard monthly
  hours and overtime multiplier.
- `payroll_deduction_lines` — transparent tenant-level deduction rules; each
  active rule lands on every payslip of a run as its own line.
- `salary_records` — append-only, effective-dated salary history. The unique
  `(tenant_id, employee_id, effective_from)` key is the
  `salary_effective_date_taken` backstop.
- `payroll_runs` — one lifecycle row per attempt. The partial unique index on
  `(tenant_id, period) WHERE status <> 'finalized'` is the one-open-run-per-
  period guarantee; a finalized period can only be corrected by a new run.
  `skipped` is the run summary's `no_salary` list (identifiers and a stable
  reason only); `totals_*` are set at calculation and never carry PII out of
  the database.
- `payroll_bonuses` — per-run, per-employee bonus entries; multiple rows are
  allowed and summed by the calculator.
- `payroll_timesheets` — informational per `(run, employee)` facts; they never
  alter base pay (no proration, PO Decision 5).
- `payslips` — one provisional/issued/superseded payslip per `(run, employee)`.
  `net` may be negative when configured deductions exceed pay (ADR 0017); it is
  a visible simulation outcome, not an error.
- `payslip_deduction_lines` — the inspectable lines of one payslip, ordered by
  `code` at read time for deterministic output.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from uuid_utils.compat import uuid7

from app.infrastructure.postgres.base import Base

# Period and status vocabularies are duplicated as SQL check constraints on
# purpose: the DB is the backstop for writers that bypass the domain service.
_PERIOD_CHECK = "period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'"
_RUN_STATUS_CHECK = (
    "status IN ('queued', 'calculating', 'calculated', 'finalized', 'failed')"
)
_PAYSLIP_STATUS_CHECK = "status IN ('provisional', 'issued', 'superseded')"
_DEDUCTION_KIND_CHECK = "kind IN ('percent_of_base', 'fixed_amount')"


class PayrollConfig(Base):
    """One payroll configuration per tenant (single currency, rate inputs)."""

    __tablename__ = "payroll_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_payroll_configs_tenant_id"),
        CheckConstraint(
            "standard_monthly_hours > 0",
            name="standard_hours_positive",
        ),
        CheckConstraint(
            "overtime_multiplier > 0",
            name="multiplier_positive",
        ),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="currency_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    standard_monthly_hours: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False
    )
    overtime_multiplier: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PayrollDeductionLine(Base):
    """One transparent tenant deduction rule; each active rule is a payslip line."""

    __tablename__ = "payroll_deduction_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "code", name="uq_payroll_deduction_lines_tenant_code"
        ),
        CheckConstraint(_DEDUCTION_KIND_CHECK, name="kind_valid"),
        CheckConstraint("value >= 0.01", name="value_positive"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SalaryRecord(Base):
    """Append-only effective-dated salary history (one record per date)."""

    __tablename__ = "salary_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "employee_id",
            "effective_from",
            name="uq_salary_records_tenant_employee_effective",
        ),
        CheckConstraint("amount >= 0.01", name="amount_positive"),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="currency_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PayrollRun(Base):
    """One monthly run attempt through the pinned lifecycle.

    `skipped` holds `[{employee_id, reason}]` only (reason is `no_salary` in
    v1). `totals_*` mirror the wire `totals` object; all five are null until
    calculation succeeds. `failed_code` is a stable code, never PII.
    """

    __tablename__ = "payroll_runs"
    __table_args__ = (
        # One open run per tenant/period. A finalized run is excluded, so a
        # correction run is allowed only after the previous one finalized.
        Index(
            "uq_payroll_runs_open_period",
            "tenant_id",
            "period",
            unique=True,
            postgresql_where=text("status <> 'finalized'"),
        ),
        Index("ix_payroll_runs_tenant_period", "tenant_id", "period"),
        Index("ix_payroll_runs_tenant_status", "tenant_id", "status"),
        CheckConstraint(_PERIOD_CHECK, name="period_valid"),
        CheckConstraint(_RUN_STATUS_CHECK, name="status_valid"),
        CheckConstraint("employee_count >= 0", name="employee_count_nonnegative"),
        CheckConstraint("skipped_count >= 0", name="skipped_count_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    employee_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    skipped_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # PII-free summary: employee ids plus the stable `no_salary` reason.
    skipped: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Run totals are PII (`.ai/security.md`); stored once at calculation.
    totals_base: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    totals_overtime_pay: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    totals_bonuses: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    totals_deductions: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    totals_net: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    failed_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calculated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    supersedes_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payroll_runs.id"), nullable=True
    )
    superseded_by_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payroll_runs.id"), nullable=True
    )
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PayrollBonus(Base):
    """A per-run, per-employee bonus entry (amount and note are PII)."""

    __tablename__ = "payroll_bonuses"
    __table_args__ = (
        CheckConstraint("amount >= 0.01", name="amount_positive"),
        Index(
            "ix_payroll_bonuses_tenant_run",
            "tenant_id",
            "run_id",
            "employee_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("payroll_runs.id"), nullable=False)
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PayrollTimesheet(Base):
    """Informational time facts per `(run, employee)` — the overtime input."""

    __tablename__ = "payroll_timesheets"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "employee_id",
            name="uq_payroll_timesheets_run_employee",
        ),
        Index("ix_payroll_timesheets_tenant_run", "tenant_id", "run_id"),
        CheckConstraint(_PERIOD_CHECK, name="period_valid"),
        CheckConstraint("worked_hours >= 0", name="worked_hours_nonnegative"),
        CheckConstraint("overtime_hours >= 0", name="overtime_hours_nonnegative"),
        CheckConstraint("leave_days >= 0", name="leave_days_nonnegative"),
        CheckConstraint("attendance_days >= 0", name="attendance_days_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("payroll_runs.id"), nullable=False)
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    worked_hours: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    overtime_hours: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    leave_days: Mapped[int] = mapped_column(Integer, nullable=False)
    attendance_days: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Payslip(Base):
    """One inspectable payslip per `(run, employee)` across its lifecycle."""

    __tablename__ = "payslips"
    __table_args__ = (
        UniqueConstraint("run_id", "employee_id", name="uq_payslips_run_employee"),
        Index(
            "ix_payslips_tenant_employee_period",
            "tenant_id",
            "employee_id",
            "period",
        ),
        Index("ix_payslips_tenant_run", "tenant_id", "run_id"),
        CheckConstraint(_PERIOD_CHECK, name="period_valid"),
        CheckConstraint(_PAYSLIP_STATUS_CHECK, name="status_valid"),
        CheckConstraint("base >= 0", name="base_nonnegative"),
        CheckConstraint("overtime_pay >= 0", name="overtime_pay_nonnegative"),
        CheckConstraint("bonuses >= 0", name="bonuses_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("payroll_runs.id"), nullable=False)
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    overtime_pay: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    bonuses: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    net: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(
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


class PayslipDeductionLine(Base):
    """One ordered-by-code deduction line of a payslip (value + amount are PII)."""

    __tablename__ = "payslip_deduction_lines"
    __table_args__ = (
        UniqueConstraint(
            "payslip_id", "code", name="uq_payslip_deduction_lines_payslip_code"
        ),
        Index(
            "ix_payslip_deduction_lines_tenant_payslip",
            "tenant_id",
            "payslip_id",
        ),
        CheckConstraint(_DEDUCTION_KIND_CHECK, name="kind_valid"),
        CheckConstraint("value >= 0.01", name="value_positive"),
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    payslip_id: Mapped[UUID] = mapped_column(ForeignKey("payslips.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
