"""payroll queued-run polling index

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-21 10:00:00.000000

The T8 worker polls `payroll_runs` for `QUEUED` runs across tenants once per
second. Every existing index leads with `tenant_id`, so the cross-tenant claim
scan had no support. A partial `(created_at, id)` index (mirrored in
`PayrollRun.__table_args__`) keeps idle polling cheap as run history grows.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_payroll_runs_queued",
        "payroll_runs",
        ["created_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    op.drop_index("ix_payroll_runs_queued", table_name="payroll_runs")
