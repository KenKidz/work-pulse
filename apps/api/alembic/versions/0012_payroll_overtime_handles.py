"""payroll timesheet overtime handles

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-21 10:00:00.000000

T7 persists the overtime row handles a calculation consumed beside each
timesheet row. Finalize flips exactly those rows to `INCLUDED_IN_PAYROLL`
(ADR 0018 decisions 2/3): re-reading all verified rows at finalize would mark
a row verified after the calculation as included without paying it.

Stored as JSONB `[{overtime_id, employee_id, hours}]` — identifiers and hours
only, no amounts. The column is internal (the wire `Timesheet` does not expose
it) and mirrored in `PayrollTimesheet.__table_args__`/columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payroll_timesheets",
        sa.Column(
            "overtime_rows",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("payroll_timesheets", "overtime_rows")
