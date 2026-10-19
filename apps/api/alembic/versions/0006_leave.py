"""leave

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19 10:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leave_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("counts_against_balance", sa.Boolean(), nullable=False),
        sa.Column("default_entitlement_days", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "default_entitlement_days >= 0",
            name=op.f("ck_leave_types_entitlement_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leave_types")),
        sa.UniqueConstraint("tenant_id", "code", name="uq_leave_types_tenant_code"),
    )
    op.create_table(
        "leave_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("leave_type_id", sa.Uuid(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
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
            "end_date >= start_date",
            name=op.f("ck_leave_requests_dates_ordered"),
        ),
        sa.CheckConstraint("days >= 1", name=op.f("ck_leave_requests_days_positive")),
        sa.CheckConstraint(
            "days = (end_date - start_date) + 1",
            name=op.f("ck_leave_requests_days_match_range"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'withdrawn')",
            name=op.f("ck_leave_requests_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_leave_requests_employee_id_employees"),
        ),
        sa.ForeignKeyConstraint(
            ["leave_type_id"],
            ["leave_types.id"],
            name=op.f("fk_leave_requests_leave_type_id_leave_types"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leave_requests")),
    )
    op.create_index(
        "ix_leave_requests_tenant_employee_status_start",
        "leave_requests",
        ["tenant_id", "employee_id", "status", "start_date"],
        unique=False,
    )
    op.create_table(
        "leave_balances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("leave_type_id", sa.Uuid(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("entitlement_days", sa.Integer(), nullable=False),
        sa.Column("used_days", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "entitlement_days >= 0",
            name=op.f("ck_leave_balances_entitlement_nonnegative"),
        ),
        sa.CheckConstraint(
            "used_days >= 0",
            name=op.f("ck_leave_balances_used_nonnegative"),
        ),
        sa.CheckConstraint(
            "used_days <= entitlement_days",
            name=op.f("ck_leave_balances_used_within_entitlement"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_leave_balances_employee_id_employees"),
        ),
        sa.ForeignKeyConstraint(
            ["leave_type_id"],
            ["leave_types.id"],
            name=op.f("fk_leave_balances_leave_type_id_leave_types"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leave_balances")),
        sa.UniqueConstraint(
            "tenant_id",
            "employee_id",
            "leave_type_id",
            "year",
            name="uq_leave_balances_tenant_employee_type_year",
        ),
    )
    op.create_index(
        "ix_leave_balances_tenant_employee_year",
        "leave_balances",
        ["tenant_id", "employee_id", "year"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_leave_balances_tenant_employee_year", table_name="leave_balances")
    op.drop_table("leave_balances")
    op.drop_index(
        "ix_leave_requests_tenant_employee_status_start",
        table_name="leave_requests",
    )
    op.drop_table("leave_requests")
    op.drop_table("leave_types")
