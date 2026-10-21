"""Postgres adapter for the Notifications repository protocol (T3, ADR 0015).

Every statement is scoped by `tenant_id` — the worker is a trusted cross-tenant
consumer, but each row still belongs to (and is read with) the envelope's
tenant (`.ai/security.md`). The two uniqueness guarantees are implemented as
`INSERT … ON CONFLICT DO NOTHING` followed by a tenant-scoped read, so a
redelivered envelope, a racing worker or a crash between the notification and
delivery inserts converges on the existing row instead of failing:

- `get_or_create` dedupes on `(event_id, recipient_employee_id)`.
- `add_delivery` dedupes on `(notification_id, channel)`.

A conflict that the tenant-scoped read cannot see (a forged `event_id` from
another tenant) raises rather than leaking the other tenant's row.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.notifications.models import (
    DeliveryChannel,
    DeliveryStatus,
    Notification,
    NotificationDelivery,
    NotificationKind,
)
from app.domains.notifications.repository import NewDelivery, NewNotification
from app.infrastructure.postgres.models_notifications import (
    NotificationDeliveryRow,
    NotificationRow,
)
from app.shared.pagination import PageParams


def _notification(row: NotificationRow) -> Notification:
    """ORM row → the immutable domain value."""
    return Notification(
        id=row.id,
        tenant_id=row.tenant_id,
        event_id=row.event_id,
        event_type=row.event_type,
        kind=NotificationKind(row.kind),
        recipient_employee_id=row.recipient_employee_id,
        recipient_user_id=row.recipient_user_id,
        subject=row.subject,
        body=row.body,
        created_at=row.created_at,
        read_at=row.read_at,
    )


def _delivery(row: NotificationDeliveryRow) -> NotificationDelivery:
    """ORM row → the immutable domain value."""
    return NotificationDelivery(
        id=row.id,
        tenant_id=row.tenant_id,
        notification_id=row.notification_id,
        channel=DeliveryChannel(row.channel),
        status=DeliveryStatus(row.status),
        attempts=row.attempts,
        last_error=row.last_error,
        sent_at=row.sent_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresNotificationRepository:
    """Tenant-scoped persistence for the notification worker's rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(
        self, notification: NewNotification
    ) -> tuple[Notification, bool]:
        stmt = (
            pg_insert(NotificationRow)
            .values(
                id=notification.id,
                tenant_id=notification.tenant_id,
                event_id=notification.event_id,
                event_type=notification.event_type,
                kind=notification.kind.value,
                recipient_employee_id=notification.recipient_employee_id,
                recipient_user_id=notification.recipient_user_id,
                subject=notification.subject,
                body=notification.body,
                created_at=notification.created_at,
            )
            .on_conflict_do_nothing(
                index_elements=["event_id", "recipient_employee_id"]
            )
            .returning(NotificationRow.id)
        )
        inserted_id = (await self._session.execute(stmt)).scalar_one_or_none()

        row = (
            await self._session.execute(
                select(NotificationRow).where(
                    NotificationRow.tenant_id == notification.tenant_id,
                    NotificationRow.event_id == notification.event_id,
                    NotificationRow.recipient_employee_id
                    == notification.recipient_employee_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:  # pragma: no cover — the unique key is global
            # The conflict belongs to another tenant: raise instead of
            # returning a row the caller has no claim to.
            raise LookupError("notification not visible for tenant")
        return _notification(row), inserted_id is not None

    async def add_delivery(self, delivery: NewDelivery) -> NotificationDelivery:
        stmt = (
            pg_insert(NotificationDeliveryRow)
            .values(
                id=delivery.id,
                tenant_id=delivery.tenant_id,
                notification_id=delivery.notification_id,
                channel=delivery.channel.value,
                status=DeliveryStatus.PENDING.value,
                attempts=0,
                last_error=None,
                sent_at=None,
                created_at=delivery.created_at,
                updated_at=delivery.created_at,
            )
            .on_conflict_do_nothing(index_elements=["notification_id", "channel"])
        )
        await self._session.execute(stmt)

        row = (
            await self._session.execute(
                select(NotificationDeliveryRow).where(
                    NotificationDeliveryRow.tenant_id == delivery.tenant_id,
                    NotificationDeliveryRow.notification_id == delivery.notification_id,
                    NotificationDeliveryRow.channel == delivery.channel.value,
                )
            )
        ).scalar_one_or_none()
        if row is None:  # pragma: no cover — the unique key is global
            raise LookupError("delivery not visible for tenant")
        return _delivery(row)

    async def list_deliveries(
        self, tenant_id: UUID, notification_id: UUID
    ) -> Sequence[NotificationDelivery]:
        stmt = (
            select(NotificationDeliveryRow)
            .where(
                NotificationDeliveryRow.tenant_id == tenant_id,
                NotificationDeliveryRow.notification_id == notification_id,
            )
            .order_by(NotificationDeliveryRow.channel.asc())
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [_delivery(row) for row in rows]

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
        stmt = (
            update(NotificationDeliveryRow)
            .where(
                NotificationDeliveryRow.tenant_id == tenant_id,
                NotificationDeliveryRow.id == delivery_id,
            )
            .values(
                status=status.value,
                attempts=attempts,
                last_error=last_error,
                sent_at=sent_at,
                updated_at=updated_at,
            )
            .returning(NotificationDeliveryRow)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise LookupError("delivery not visible for tenant")
        return _delivery(row)

    async def list_for_recipient(
        self,
        tenant_id: UUID,
        recipient_employee_id: UUID,
        page: PageParams,
    ) -> tuple[Sequence[Notification], int]:
        conditions = (
            NotificationRow.tenant_id == tenant_id,
            NotificationRow.recipient_employee_id == recipient_employee_id,
        )
        total_items = (
            await self._session.execute(
                select(func.count()).select_from(NotificationRow).where(*conditions)
            )
        ).scalar_one()
        stmt = (
            select(NotificationRow)
            .where(*conditions)
            .order_by(
                NotificationRow.created_at.desc(),
                NotificationRow.id.desc(),
            )
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [_notification(row) for row in rows], total_items

    async def count_unread(self, tenant_id: UUID, recipient_employee_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(NotificationRow)
            .where(
                NotificationRow.tenant_id == tenant_id,
                NotificationRow.recipient_employee_id == recipient_employee_id,
                NotificationRow.read_at.is_(None),
            )
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def get(self, tenant_id: UUID, notification_id: UUID) -> Notification | None:
        stmt = select(NotificationRow).where(
            NotificationRow.tenant_id == tenant_id,
            NotificationRow.id == notification_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _notification(row) if row is not None else None

    async def mark_read(
        self, tenant_id: UUID, notification_id: UUID, *, read_at: datetime
    ) -> bool:
        stmt = (
            update(NotificationRow)
            .where(
                NotificationRow.tenant_id == tenant_id,
                NotificationRow.id == notification_id,
                NotificationRow.read_at.is_(None),
            )
            .values(read_at=read_at)
            .returning(NotificationRow.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def mark_all_read(
        self,
        tenant_id: UUID,
        recipient_employee_id: UUID,
        *,
        read_at: datetime,
    ) -> int:
        stmt = (
            update(NotificationRow)
            .where(
                NotificationRow.tenant_id == tenant_id,
                NotificationRow.recipient_employee_id == recipient_employee_id,
                NotificationRow.read_at.is_(None),
            )
            .values(read_at=read_at)
            .returning(NotificationRow.id)
        )
        updated = (await self._session.execute(stmt)).scalars().all()
        return len(updated)
