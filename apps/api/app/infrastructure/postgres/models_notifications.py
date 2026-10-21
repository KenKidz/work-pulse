"""Notification projection ORM models (ADR 0015, ADR 0016).

The notification worker's durable output: `notifications` (one row per
`(event_id, recipient employee)` — the person-addressed intent, with the
rendered message of record) and `notification_deliveries` (one row per
`(notification, channel)` — the independent email/Slack attempt history).

Rows are a projection of the event stream, so every column is written only by
the worker — with one deliberate carve-out: `notifications.read_at` is
user-owned mutable state written only by the inbox HTTP path (ADR 0016). A
rebuild from the event stream may drop it. Nothing else on either table is
writable by an HTTP path; delivery status remains worker-owned ops data.

Both tables are tenant-scoped; the unique keys are the dedupe guarantees the
delivery service rests on:

- `uq_notifications_event_recipient` — a redelivered envelope is a no-op.
- `uq_notification_deliveries_notification_channel` — every channel has exactly
  one row, so a crash between notification and delivery inserts reconciles
  instead of duplicating.

`last_error` holds redacted infrastructure text only; rendered bodies carry
personal names by design (message of record, ADR 0015 decision 6), but never
free text such as `reason`/`decision_note` (`.ai/security.md`).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid_utils.compat import uuid7

from app.infrastructure.postgres.base import Base


class NotificationRow(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # The pinned dedupe key (ADR 0015 decision 1): one notification per
        # event per addressed person. `event_id` is globally unique in the
        # outbox, so this key also prevents a forged cross-tenant replay from
        # creating a second row.
        UniqueConstraint(
            "event_id",
            "recipient_employee_id",
            name="uq_notifications_event_recipient",
        ),
        Index("ix_notifications_tenant_id", "tenant_id"),
        # The inbox's only access path (ADR 0016): one recipient's rows in
        # newest-first order, plus the unread filter on the same prefix.
        Index(
            "ix_notifications_inbox",
            "tenant_id",
            "recipient_employee_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_employee_id: Mapped[UUID] = mapped_column(nullable=False)
    recipient_user_id: Mapped[UUID] = mapped_column(nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # The only HTTP-writable column (ADR 0016): user-owned read state layered
    # on the worker-written projection; a rebuild may drop it.
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class NotificationDeliveryRow(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        # One row per channel: the service reconciles missing channels and a
        # racing duplicate insert is a no-op rather than an error.
        UniqueConstraint(
            "notification_id",
            "channel",
            name="uq_notification_deliveries_notification_channel",
        ),
        Index("ix_notification_deliveries_tenant_id", "tenant_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    notification_id: Mapped[UUID] = mapped_column(
        ForeignKey("notifications.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
