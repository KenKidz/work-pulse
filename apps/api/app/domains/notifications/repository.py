"""Data-access abstractions for Notifications (T2, ADR 0015, ADR 0016).

Protocols only — the domain never imports infrastructure. Every method takes
`tenant_id` explicitly and implementations scope every statement by it; the
notification worker is a trusted cross-tenant process, but each row still
carries (and is queried by) the envelope's tenant.

The read-side methods back the person-addressed inbox. They write exactly one
column — `read_at` — and never touch projection fields or deliveries (ADR
0016); recipient ownership is enforced by the domain policy before a single
row is marked.

The repository owns the two uniqueness guarantees the design rests on:
`get_or_create` is a dedupe insert on `(tenant_id, event_id,
recipient_employee_id)`, and `add_delivery` is conflict-tolerant on
`(notification_id, channel)`. A duplicate is returned as the existing row, so
a redelivered envelope or a racing worker is a no-op rather than an error.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domains.notifications.models import (
    DeliveryChannel,
    DeliveryStatus,
    Notification,
    NotificationDelivery,
    NotificationKind,
)
from app.shared.pagination import PageParams


@dataclass(frozen=True, slots=True)
class NewNotification:
    """An insert candidate; the repository dedupes it on the unique key."""

    id: UUID
    tenant_id: UUID
    event_id: str
    event_type: str
    kind: NotificationKind
    recipient_employee_id: UUID
    recipient_user_id: UUID
    subject: str
    body: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewDelivery:
    """A pending delivery to insert; duplicates return the existing row."""

    id: UUID
    tenant_id: UUID
    notification_id: UUID
    channel: DeliveryChannel
    created_at: datetime


class NotificationRepository(Protocol):
    """Persistence boundary for notifications and their channel deliveries."""

    async def get_or_create(
        self, notification: NewNotification
    ) -> tuple[Notification, bool]:
        """Insert the notification unless `(event_id, recipient)` exists.

        Returns `(row, created)`; `created=False` means a redelivery/race lost
        the unique-key insertion and the existing row wins — the caller keeps
        the stored content and never re-renders it.
        """
        ...

    async def add_delivery(self, delivery: NewDelivery) -> NotificationDelivery:
        """Insert a `pending` delivery; a duplicate `(notification, channel)`
        returns the already-stored row (dedupe, never an error)."""
        ...

    async def list_deliveries(
        self, tenant_id: UUID, notification_id: UUID
    ) -> Sequence[NotificationDelivery]:
        """All deliveries of one notification, ordered by channel for stability."""
        ...

    async def set_delivery_status(
        self,
        tenant_id: UUID,
        delivery_id: UUID,
        *,
        status: DeliveryStatus,
        attempts: int,
        last_error: str | None,
        sent_at: datetime | None,
        updated_at: datetime,
    ) -> NotificationDelivery:
        """Persist one delivery transition and return the updated row.

        The service writes only terminal states (`sent`, `skipped`, `failed`);
        retry attempts are counted in memory and persisted with the terminal
        result. Store errors propagate — the consumer decides the backoff and
        never commits past an unhandled message (ADR 0015 decision 3).
        """
        ...

    async def list_for_recipient(
        self,
        tenant_id: UUID,
        recipient_employee_id: UUID,
        page: PageParams,
    ) -> tuple[Sequence[Notification], int]:
        """One recipient's rows, newest first, plus the unpaged total.

        Ordered `created_at` DESC with `id` DESC as the tiebreak so pagination
        is stable; both figures are tenant- and recipient-scoped.
        """
        ...

    async def count_unread(self, tenant_id: UUID, recipient_employee_id: UUID) -> int:
        """The recipient's unread rows (`read_at IS NULL`)."""
        ...

    async def get(self, tenant_id: UUID, notification_id: UUID) -> Notification | None:
        """One row by id within the tenant; `None` when foreign or unknown."""
        ...

    async def mark_read(
        self, tenant_id: UUID, notification_id: UUID, *, read_at: datetime
    ) -> bool:
        """Set `read_at` only while it is still null.

        Returns whether this call wrote. A second mark-read keeps the original
        timestamp, so the operation is idempotent in one statement.
        """
        ...

    async def mark_all_read(
        self,
        tenant_id: UUID,
        recipient_employee_id: UUID,
        *,
        read_at: datetime,
    ) -> int:
        """Mark the recipient's currently-unread rows; return how many changed.

        Already-read rows keep their timestamp, and rows created after the
        statement stay unread (rule 4's "at call time" semantics).
        """
        ...
