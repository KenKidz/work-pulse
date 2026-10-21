"""salary records list index

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-21 09:00:00.000000

T5's salary history list orders a tenant's records by `effective_from` (then
`created_at`, `id`) with no employee filter. The unique
`(tenant_id, employee_id, effective_from)` index cannot satisfy that ordering
because `employee_id` sits between the tenant prefix and the sort column, so
every page would sort the tenant's full history. This index serves the ordered
scan; it is mirrored in `SalaryRecord.__table_args__` (model/migration parity).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_salary_records_tenant_effective",
        "salary_records",
        ["tenant_id", "effective_from", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_salary_records_tenant_effective",
        table_name="salary_records",
    )
