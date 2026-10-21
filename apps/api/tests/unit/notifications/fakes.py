"""In-memory fakes for the Notifications unit tests (no I/O).

`FakeNotificationRepository` mirrors the repository protocol's two uniqueness
guarantees (dedupe on the event/recipient key, conflict-tolerant delivery
insert), so the service is exercised against the same rules as the Postgres
adapter without a database. The recording senders and sleeper make retry
bookkeeping observable without wall-clock waits or provider calls
(`.ai/testing.md`).
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.domains.notifications.models import (
    DeliveryStatus,
    Notification,
    NotificationDelivery,
    RenderedMessage,
)
from app.domains.notifications.repository import NewDelivery, NewNotification
from app.domains.notifications.schemas import Recipient
from app.shared.pagination import PageParams


class FakeNotificationRepository:
    """Tenant-scoped in-memory store with the pinned unique keys."""

    def __init__(self) -> None:
        self.notifications: dict[tuple[UUID, UUID], Notification] = {}
        self.deliveries: dict[tuple[UUID, UUID], NotificationDelivery] = {}
        self.calls: list[str] = []
        self.status_error: Exception | None = None
        self._by_event: dict[tuple[UUID, str, UUID], Notification] = {}

    async def get_or_create(
        self, notification: NewNotification
    ) -> tuple[Notification, bool]:
        self.calls.append("get_or_create")
        key = (
            notification.tenant_id,
            notification.event_id,
            notification.recipient_employee_id,
        )
        existing = self._by_event.get(key)
        if existing is not None:
            return existing, False
        row = Notification(
            id=notification.id,
            tenant_id=notification.tenant_id,
            event_id=notification.event_id,
            event_type=notification.event_type,
            kind=notification.kind,
            recipient_employee_id=notification.recipient_employee_id,
            recipient_user_id=notification.recipient_user_id,
            subject=notification.subject,
            body=notification.body,
            created_at=notification.created_at,
        )
        self.notifications[(row.tenant_id, row.id)] = row
        self._by_event[key] = row
        return row, True

    async def add_delivery(self, delivery: NewDelivery) -> NotificationDelivery:
        self.calls.append("add_delivery")
        for row in self.deliveries.values():
            if (
                row.tenant_id == delivery.tenant_id
                and row.notification_id == delivery.notification_id
                and row.channel == delivery.channel
            ):
                return row
        row = NotificationDelivery(
            id=delivery.id,
            tenant_id=delivery.tenant_id,
            notification_id=delivery.notification_id,
            channel=delivery.channel,
            status=DeliveryStatus.PENDING,
            attempts=0,
            last_error=None,
            sent_at=None,
            created_at=delivery.created_at,
            updated_at=delivery.created_at,
        )
        self.deliveries[(row.tenant_id, row.id)] = row
        return row

    async def list_deliveries(
        self, tenant_id: UUID, notification_id: UUID
    ) -> Sequence[NotificationDelivery]:
        self.calls.append("list_deliveries")
        return [
            row
            for row in self.deliveries.values()
            if row.tenant_id == tenant_id and row.notification_id == notification_id
        ]

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
        self.calls.append("set_delivery_status")
        if self.status_error is not None:
            raise self.status_error
        key = (tenant_id, delivery_id)
        row = self.deliveries.get(key)
        if row is None:
            raise KeyError(f"unknown delivery {delivery_id}")
        updated = replace(
            row,
            status=status,
            attempts=attempts,
            last_error=last_error,
            sent_at=sent_at,
            updated_at=updated_at,
        )
        self.deliveries[key] = updated
        return updated

    async def list_for_recipient(
        self,
        tenant_id: UUID,
        recipient_employee_id: UUID,
        page: PageParams,
    ) -> tuple[Sequence[Notification], int]:
        self.calls.append("list_for_recipient")
        rows = sorted(
            (
                row
                for row in self.notifications.values()
                if row.tenant_id == tenant_id
                and row.recipient_employee_id == recipient_employee_id
            ),
            key=lambda row: (row.created_at, row.id),
            reverse=True,
        )
        return rows[page.offset : page.offset + page.limit], len(rows)

    async def count_unread(self, tenant_id: UUID, recipient_employee_id: UUID) -> int:
        self.calls.append("count_unread")
        return sum(
            1
            for row in self.notifications.values()
            if row.tenant_id == tenant_id
            and row.recipient_employee_id == recipient_employee_id
            and row.read_at is None
        )

    async def get(self, tenant_id: UUID, notification_id: UUID) -> Notification | None:
        self.calls.append("get")
        return self.notifications.get((tenant_id, notification_id))

    async def mark_read(
        self, tenant_id: UUID, notification_id: UUID, *, read_at: datetime
    ) -> bool:
        self.calls.append("mark_read")
        key = (tenant_id, notification_id)
        row = self.notifications.get(key)
        if row is None or row.read_at is not None:
            return False
        self.notifications[key] = replace(row, read_at=read_at)
        return True

    async def mark_all_read(
        self,
        tenant_id: UUID,
        recipient_employee_id: UUID,
        *,
        read_at: datetime,
    ) -> int:
        self.calls.append("mark_all_read")
        changed = 0
        for key, row in list(self.notifications.items()):
            if (
                row.tenant_id == tenant_id
                and row.recipient_employee_id == recipient_employee_id
                and row.read_at is None
            ):
                self.notifications[key] = replace(row, read_at=read_at)
                changed += 1
        return changed


class RecordingEmailSender:
    """Records email sends; `errors` is a queue, `error` an always-raise hook."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.calls: list[tuple[Recipient, RenderedMessage, UUID]] = []
        self.sent: list[tuple[Recipient, RenderedMessage, UUID]] = []
        self.errors: list[BaseException] = []
        self.error = error

    async def send(
        self,
        *,
        recipient: Recipient,
        message: RenderedMessage,
        idempotency_key: UUID,
    ) -> None:
        self.calls.append((recipient, message, idempotency_key))
        error = self.errors.pop(0) if self.errors else self.error
        if error is not None:
            raise error
        self.sent.append((recipient, message, idempotency_key))


class RecordingSlackSender:
    """Records Slack posts; `errors` is a queue, `error` an always-raise hook."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.calls: list[tuple[Recipient, RenderedMessage]] = []
        self.sent: list[tuple[Recipient, RenderedMessage]] = []
        self.errors: list[BaseException] = []
        self.error = error

    async def send(self, *, recipient: Recipient, message: RenderedMessage) -> None:
        self.calls.append((recipient, message))
        error = self.errors.pop(0) if self.errors else self.error
        if error is not None:
            raise error
        self.sent.append((recipient, message))


class RecordingSleeper:
    """Records backoff delays instead of sleeping (no wall-clock waits)."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class RecordingCommitter:
    """Counts transaction-boundary commits (the crash-window guarantee)."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1
