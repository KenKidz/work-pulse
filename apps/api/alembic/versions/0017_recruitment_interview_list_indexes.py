"""recruitment interview list indexes

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-26 03:45:00.000000

The interview list orders by `scheduled_at DESC, id DESC` and supports a
`job_id` filter plus inclusive `from`/`to` date bounds. The 0015 indexes put
`candidate_id`/`interviewer_id` between the tenant prefix and the sort column,
so the default management list, the date range and a job's interviews would
sort every matching tenant row on each page. These indexes serve the ordered
scans; they are mirrored in `Interview.__table_args__` (model/migration
parity).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_interviews_tenant_scheduled",
        "interviews",
        ["tenant_id", "scheduled_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_interviews_tenant_job_scheduled",
        "interviews",
        ["tenant_id", "job_id", "scheduled_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_interviews_tenant_job_scheduled",
        table_name="interviews",
    )
    op.drop_index(
        "ix_interviews_tenant_scheduled",
        table_name="interviews",
    )
