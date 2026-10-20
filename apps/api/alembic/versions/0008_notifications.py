"""notifications

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-20 11:00:00.000000

The notification worker's durable output (ADR 0015 decision 1): one
`notifications` row per `(event_id, recipient employee)` and one
`notification_deliveries` row per `(notification, channel)`. The unique keys
are the dedupe guarantees the delivery service relies on; `tenant_id` on both
tables is indexed for the worker's tenant-scoped reads.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("recipient_employee_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint(
            "event_id",
            "recipient_employee_id",
            name="uq_notifications_event_recipient",
        ),
    )
    op.create_index(
        "ix_notifications_tenant_id", "notifications", ["tenant_id"], unique=False
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("notification_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
            name=op.f("fk_notification_deliveries_notification_id_notifications"),
        ),
        sa.UniqueConstraint(
            "notification_id",
            "channel",
            name="uq_notification_deliveries_notification_channel",
        ),
    )
    op.create_index(
        "ix_notification_deliveries_tenant_id",
        "notification_deliveries",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_deliveries_tenant_id", table_name="notification_deliveries"
    )
    op.drop_table("notification_deliveries")
    op.drop_index("ix_notifications_tenant_id", table_name="notifications")
    op.drop_table("notifications")
