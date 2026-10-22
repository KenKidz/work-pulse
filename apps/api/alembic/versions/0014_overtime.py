"""overtime requests

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-22 04:27:00.000000

Creates `overtime_requests` — the Phase-4 overtime claim aggregate behind the
ADR 0018 payroll seam. `hours` is `Numeric(4, 2)` (ADR 0017, no floats) with
the pinned `0 < hours <= 12` guard; statuses are lowercase stored values.

`ix_overtime_requests_verified_seam` is the seam's month read over
`hr_verified` rows; it is declared in both this migration and the model's
`__table_args__` (parity rule). `included_in_run_id` is a bare UUID — no FK
across the payroll seam boundary (ADR 0018 decision 6).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "overtime_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("hours", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("manager_note", sa.String(length=500), nullable=True),
        sa.Column("hr_note", sa.String(length=500), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.Uuid(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by", sa.Uuid(), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_from", sa.String(length=20), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("included_in_run_id", sa.Uuid(), nullable=True),
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
            "hours > 0 AND hours <= 12",
            name=op.f("ck_overtime_requests_hours_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'manager_approved', 'hr_verified', "
            "'included_in_payroll', 'rejected', 'withdrawn')",
            name=op.f("ck_overtime_requests_status_valid"),
        ),
        sa.CheckConstraint(
            "rejected_from IS NULL OR rejected_from IN "
            "('requested', 'manager_approved', 'hr_verified')",
            name=op.f("ck_overtime_requests_rejected_from_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_overtime_requests_employee_id_employees"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_overtime_requests")),
    )
    op.create_index(
        "ix_overtime_requests_tenant_employee_status_work_date",
        "overtime_requests",
        ["tenant_id", "employee_id", "status", "work_date"],
        unique=False,
    )
    op.create_index(
        "ix_overtime_requests_verified_seam",
        "overtime_requests",
        ["tenant_id", "work_date"],
        unique=False,
        postgresql_where=sa.text("status = 'hr_verified'"),
        postgresql_include=["employee_id", "hours"],
    )


def downgrade() -> None:
    op.drop_index("ix_overtime_requests_verified_seam", table_name="overtime_requests")
    op.drop_index(
        "ix_overtime_requests_tenant_employee_status_work_date",
        table_name="overtime_requests",
    )
    op.drop_table("overtime_requests")
