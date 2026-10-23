"""recruitment candidate list indexes

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-23 11:00:00.000000

The candidate list orders by `created_at DESC, id DESC`. The 0015 indexes put
`status` (or `job_id` + `status`) between the tenant prefix and the sort column,
so the default unfiltered list and a job's pipeline without a status filter
would sort every matching tenant row on each page. These indexes serve the
ordered scans; they are mirrored in `Candidate.__table_args__` (model/migration
parity).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_candidates_tenant_created",
        "candidates",
        ["tenant_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_candidates_tenant_job_created",
        "candidates",
        ["tenant_id", "job_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidates_tenant_job_created",
        table_name="candidates",
    )
    op.drop_index(
        "ix_candidates_tenant_created",
        table_name="candidates",
    )
