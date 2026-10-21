"""payroll

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-21 08:00:00.000000

The payroll persistence layer (`.ai/features/payroll.md` T2, ADR 0017).

Eight tenant-scoped tables. Money is `numeric(12,2)` / `Decimal`; no float
appears anywhere (ADR 0017). The payroll-specific guarantees live in the
database so a writer that bypasses the service still cannot corrupt them:

- `uq_payroll_runs_open_period` — at most one non-finalized run per
  `(tenant, period)`; finalized runs are excluded so a correction run is
  allowed only after the previous run finalized.
- `uq_salary_records_tenant_employee_effective` — one salary record per
  employee/date (the `salary_effective_date_taken` backstop).
- `uq_payslips_run_employee` / `uq_payroll_timesheets_run_employee` — one
  calculated result per employee per run, which keeps recalculation an atomic
  replace rather than a duplicate.

Partial/covering indexes are declared identically in
`app/domains/payroll/models.py` (parity rule). `skipped` is a PII-free JSONB
summary; amount columns and bonus notes are PII and never leave the database
in events or audit `changes`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERIOD_CHECK = "period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'"
_RUN_STATUS_CHECK = (
    "status IN ('queued', 'calculating', 'calculated', 'finalized', 'failed')"
)
_PAYSLIP_STATUS_CHECK = "status IN ('provisional', 'issued', 'superseded')"
_DEDUCTION_KIND_CHECK = "kind IN ('percent_of_base', 'fixed_amount')"


def upgrade() -> None:
    op.create_table(
        "payroll_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("standard_monthly_hours", sa.Numeric(8, 2), nullable=False),
        sa.Column("overtime_multiplier", sa.Numeric(8, 4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "standard_monthly_hours > 0",
            name=op.f("ck_payroll_configs_standard_hours_positive"),
        ),
        sa.CheckConstraint(
            "overtime_multiplier > 0",
            name=op.f("ck_payroll_configs_multiplier_positive"),
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_payroll_configs_currency_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payroll_configs")),
        sa.UniqueConstraint("tenant_id", name="uq_payroll_configs_tenant_id"),
    )
    op.create_table(
        "payroll_deduction_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("value", sa.Numeric(12, 2), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _DEDUCTION_KIND_CHECK,
            name=op.f("ck_payroll_deduction_lines_kind_valid"),
        ),
        sa.CheckConstraint(
            "value >= 0.01",
            name=op.f("ck_payroll_deduction_lines_value_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payroll_deduction_lines")),
        sa.UniqueConstraint(
            "tenant_id",
            "code",
            name="uq_payroll_deduction_lines_tenant_code",
        ),
    )
    op.create_table(
        "salary_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount >= 0.01",
            name=op.f("ck_salary_records_amount_positive"),
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_salary_records_currency_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_salary_records_employee_id_employees"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_salary_records")),
        sa.UniqueConstraint(
            "tenant_id",
            "employee_id",
            "effective_from",
            name="uq_salary_records_tenant_employee_effective",
        ),
    )
    op.create_table(
        "payroll_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "employee_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "skipped_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "skipped",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("totals_base", sa.Numeric(12, 2), nullable=True),
        sa.Column("totals_overtime_pay", sa.Numeric(12, 2), nullable=True),
        sa.Column("totals_bonuses", sa.Numeric(12, 2), nullable=True),
        sa.Column("totals_deductions", sa.Numeric(12, 2), nullable=True),
        sa.Column("totals_net", sa.Numeric(12, 2), nullable=True),
        sa.Column("failed_code", sa.String(length=64), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_run_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_by_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _PERIOD_CHECK,
            name=op.f("ck_payroll_runs_period_valid"),
        ),
        sa.CheckConstraint(
            _RUN_STATUS_CHECK,
            name=op.f("ck_payroll_runs_status_valid"),
        ),
        sa.CheckConstraint(
            "employee_count >= 0",
            name=op.f("ck_payroll_runs_employee_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "skipped_count >= 0",
            name=op.f("ck_payroll_runs_skipped_count_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_run_id"],
            ["payroll_runs.id"],
            name=op.f("fk_payroll_runs_supersedes_run_id_payroll_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_run_id"],
            ["payroll_runs.id"],
            name=op.f("fk_payroll_runs_superseded_by_run_id_payroll_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payroll_runs")),
    )
    op.create_index(
        "uq_payroll_runs_open_period",
        "payroll_runs",
        ["tenant_id", "period"],
        unique=True,
        postgresql_where=sa.text("status <> 'finalized'"),
    )
    op.create_index(
        "ix_payroll_runs_tenant_period",
        "payroll_runs",
        ["tenant_id", "period"],
        unique=False,
    )
    op.create_index(
        "ix_payroll_runs_tenant_status",
        "payroll_runs",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_table(
        "payroll_bonuses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount >= 0.01",
            name=op.f("ck_payroll_bonuses_amount_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_payroll_bonuses_employee_id_employees"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["payroll_runs.id"],
            name=op.f("fk_payroll_bonuses_run_id_payroll_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payroll_bonuses")),
    )
    op.create_index(
        "ix_payroll_bonuses_tenant_run",
        "payroll_bonuses",
        ["tenant_id", "run_id", "employee_id"],
        unique=False,
    )
    op.create_table(
        "payroll_timesheets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("worked_hours", sa.Numeric(10, 2), nullable=False),
        sa.Column("overtime_hours", sa.Numeric(10, 2), nullable=False),
        sa.Column("leave_days", sa.Integer(), nullable=False),
        sa.Column("attendance_days", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _PERIOD_CHECK,
            name=op.f("ck_payroll_timesheets_period_valid"),
        ),
        sa.CheckConstraint(
            "worked_hours >= 0",
            name=op.f("ck_payroll_timesheets_worked_hours_nonnegative"),
        ),
        sa.CheckConstraint(
            "overtime_hours >= 0",
            name=op.f("ck_payroll_timesheets_overtime_hours_nonnegative"),
        ),
        sa.CheckConstraint(
            "leave_days >= 0",
            name=op.f("ck_payroll_timesheets_leave_days_nonnegative"),
        ),
        sa.CheckConstraint(
            "attendance_days >= 0",
            name=op.f("ck_payroll_timesheets_attendance_days_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_payroll_timesheets_employee_id_employees"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["payroll_runs.id"],
            name=op.f("fk_payroll_timesheets_run_id_payroll_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payroll_timesheets")),
        sa.UniqueConstraint(
            "run_id",
            "employee_id",
            name="uq_payroll_timesheets_run_employee",
        ),
    )
    op.create_index(
        "ix_payroll_timesheets_tenant_run",
        "payroll_timesheets",
        ["tenant_id", "run_id"],
        unique=False,
    )
    op.create_table(
        "payslips",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("base", sa.Numeric(12, 2), nullable=False),
        sa.Column("overtime_pay", sa.Numeric(12, 2), nullable=False),
        sa.Column("bonuses", sa.Numeric(12, 2), nullable=False),
        sa.Column("net", sa.Numeric(12, 2), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _PERIOD_CHECK,
            name=op.f("ck_payslips_period_valid"),
        ),
        sa.CheckConstraint(
            _PAYSLIP_STATUS_CHECK,
            name=op.f("ck_payslips_status_valid"),
        ),
        sa.CheckConstraint(
            "base >= 0",
            name=op.f("ck_payslips_base_nonnegative"),
        ),
        sa.CheckConstraint(
            "overtime_pay >= 0",
            name=op.f("ck_payslips_overtime_pay_nonnegative"),
        ),
        sa.CheckConstraint(
            "bonuses >= 0",
            name=op.f("ck_payslips_bonuses_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_payslips_employee_id_employees"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["payroll_runs.id"],
            name=op.f("fk_payslips_run_id_payroll_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payslips")),
        sa.UniqueConstraint("run_id", "employee_id", name="uq_payslips_run_employee"),
    )
    op.create_index(
        "ix_payslips_tenant_employee_period",
        "payslips",
        ["tenant_id", "employee_id", "period"],
        unique=False,
    )
    op.create_index(
        "ix_payslips_tenant_run",
        "payslips",
        ["tenant_id", "run_id"],
        unique=False,
    )
    op.create_table(
        "payslip_deduction_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("payslip_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("value", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _DEDUCTION_KIND_CHECK,
            name=op.f("ck_payslip_deduction_lines_kind_valid"),
        ),
        sa.CheckConstraint(
            "value >= 0.01",
            name=op.f("ck_payslip_deduction_lines_value_positive"),
        ),
        sa.CheckConstraint(
            "amount >= 0",
            name=op.f("ck_payslip_deduction_lines_amount_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["payslip_id"],
            ["payslips.id"],
            name=op.f("fk_payslip_deduction_lines_payslip_id_payslips"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payslip_deduction_lines")),
        sa.UniqueConstraint(
            "payslip_id",
            "code",
            name="uq_payslip_deduction_lines_payslip_code",
        ),
    )
    op.create_index(
        "ix_payslip_deduction_lines_tenant_payslip",
        "payslip_deduction_lines",
        ["tenant_id", "payslip_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payslip_deduction_lines_tenant_payslip",
        table_name="payslip_deduction_lines",
    )
    op.drop_table("payslip_deduction_lines")
    op.drop_index("ix_payslips_tenant_run", table_name="payslips")
    op.drop_index("ix_payslips_tenant_employee_period", table_name="payslips")
    op.drop_table("payslips")
    op.drop_index("ix_payroll_timesheets_tenant_run", table_name="payroll_timesheets")
    op.drop_table("payroll_timesheets")
    op.drop_index("ix_payroll_bonuses_tenant_run", table_name="payroll_bonuses")
    op.drop_table("payroll_bonuses")
    op.drop_index("ix_payroll_runs_tenant_status", table_name="payroll_runs")
    op.drop_index("ix_payroll_runs_tenant_period", table_name="payroll_runs")
    op.drop_index("uq_payroll_runs_open_period", table_name="payroll_runs")
    op.drop_table("payroll_runs")
    op.drop_table("salary_records")
    op.drop_table("payroll_deduction_lines")
    op.drop_table("payroll_configs")
