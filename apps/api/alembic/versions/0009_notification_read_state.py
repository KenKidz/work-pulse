"""notification read state

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-21 04:10:00.000000

Read state on the person-addressed notification row (ADR 0016): a nullable
`read_at` written only by the inbox HTTP path, never by the notification
worker. A rebuild from the event stream may drop it. The composite index
serves the inbox's only access path — one recipient's rows newest-first — and
its unread filter; it is declared identically in `NotificationRow.__table_args__`
(partial/covering parity rule).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_notifications_inbox",
        "notifications",
        ["tenant_id", "recipient_employee_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_inbox", table_name="notifications")
    op.drop_column("notifications", "read_at")
