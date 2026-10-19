"""leave coverage index

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19 11:20:00.000000

The attendance board reads APPROVED leave coverage for one date
(`tenant_id = ? AND status = 'approved' AND start_date <= date AND
end_date >= date`) on every board/snapshot call (Leave spec T5, ADR 0014).
The committed 0006 index leads with `employee_id`, which the board query does
not constrain, so this partial index leads with the date range that bounds the
scan (`end_date`) and includes `employee_id` for an index-only read.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_leave_requests_approved_coverage",
        "leave_requests",
        ["tenant_id", "end_date", "start_date"],
        unique=False,
        postgresql_where=sa.text("status = 'approved'"),
        postgresql_include=["employee_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_leave_requests_approved_coverage", table_name="leave_requests")
