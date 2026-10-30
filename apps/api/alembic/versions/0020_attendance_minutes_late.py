"""attendance minutes_late

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-30 03:00:00.000000

Adds the persisted lateness minutes to `attendance_records`. The value was
already computed once at check-in (`_minutes_past_boundary`) but only carried
by the `attendance.checked_in`/`attendance.late` event payloads; the Phase-5
CDC projection reads row state, so the OLTP row becomes the source (feature
rule 5). Nullable: not-late records and rows written before this migration
carry `null`; check-out never rewrites it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attendance_records",
        sa.Column("minutes_late", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("attendance_records", "minutes_late")
