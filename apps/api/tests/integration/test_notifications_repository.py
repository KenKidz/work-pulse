"""Repository integration tests for Notifications (T3).

Real PostgreSQL: the two unique keys are dedupe guarantees in both directions
(same key → one row, another recipient → its own row), every statement is
tenant-scoped, and delivery status transitions persist. No provider or broker
is involved.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.domains.notifications.models import (
    SKIP_NOT_CONFIGURED,
    DeliveryChannel,
    DeliveryStatus,
    NotificationKind,
)
from app.domains.notifications.repository import NewDelivery, NewNotification
from app.infrastructure.postgres.models_notifications import (
    NotificationDeliveryRow,
    NotificationRow,
)
from app.infrastructure.postgres.repositories.notifications import (
    PostgresNotificationRepository,
)
from tests.integration.conftest import TENANT_A, TENANT_B

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
EMPLOYEE = uuid7()
MANAGER = uuid7()
RECIPIENT_USER = uuid7()


def _new_notification(
    tenant_id: UUID,
    *,
    event_id: str = "evt_notifications_0001",
    recipient_employee_id: UUID = MANAGER,
    subject: str = "Leave request from Ada Lovelace",
    body: str = "Ada Lovelace requested annual leave (3 days).",
) -> NewNotification:
    return NewNotification(
        id=uuid7(),
        tenant_id=tenant_id,
        event_id=event_id,
        event_type="leave.requested",
        kind=NotificationKind.LEAVE_REQUESTED,
        recipient_employee_id=recipient_employee_id,
        recipient_user_id=RECIPIENT_USER,
        subject=subject,
        body=body,
        created_at=NOW,
    )


async def _notification_rows(session: AsyncSession) -> int:
    stmt = select(func.count()).select_from(NotificationRow)
    return (await session.execute(stmt)).scalar_one()


async def test_get_or_create_dedupes_per_event_and_recipient(
    session: AsyncSession,
) -> None:
    """AC: `(event_id, recipient)` is the key; a different person is a new row."""
    repo = PostgresNotificationRepository(session)

    first, created = await repo.get_or_create(_new_notification(TENANT_A))
    assert created is True
    assert first.event_id == "evt_notifications_0001"
    assert first.kind is NotificationKind.LEAVE_REQUESTED
    assert first.recipient_employee_id == MANAGER
    assert first.tenant_id == TENANT_A

    # Redelivery: the existing row wins, content included — never a second row.
    replay, created = await repo.get_or_create(
        _new_notification(
            TENANT_A,
            subject="A re-render that must not win",
            body="A re-rendered body that must not win.",
        )
    )
    assert created is False
    assert replay.id == first.id
    assert replay.subject == first.subject
    assert replay.body == first.body
    assert await _notification_rows(session) == 1

    # The same event addressed to a different person fans out to its own row.
    other, created = await repo.get_or_create(
        _new_notification(TENANT_A, recipient_employee_id=EMPLOYEE)
    )
    assert created is True
    assert other.id != first.id
    assert await _notification_rows(session) == 2


async def test_tenant_scoping_hides_rows_in_both_directions(
    session: AsyncSession,
) -> None:
    """AC: tenant B sees none of tenant A's rows; a forged event id leaks nothing."""
    repo = PostgresNotificationRepository(session)
    notification, _ = await repo.get_or_create(_new_notification(TENANT_A))
    delivery = await repo.add_delivery(
        NewDelivery(
            id=uuid7(),
            tenant_id=TENANT_A,
            notification_id=notification.id,
            channel=DeliveryChannel.EMAIL,
            created_at=NOW,
        )
    )

    # Reads and writes scoped to tenant B cannot reach A's delivery.
    assert await repo.list_deliveries(TENANT_B, notification.id) == []
    with pytest.raises(LookupError):
        await repo.set_delivery_status(
            TENANT_B,
            delivery.id,
            status=DeliveryStatus.SENT,
            attempts=1,
            last_error=None,
            sent_at=NOW,
            updated_at=NOW,
        )
    unchanged = await repo.list_deliveries(TENANT_A, notification.id)
    assert [row.status for row in unchanged] == [DeliveryStatus.PENDING]

    # A replayed `event_id` from another tenant must raise, not return A's row.
    with pytest.raises(LookupError):
        await repo.get_or_create(_new_notification(TENANT_B))
    assert await _notification_rows(session) == 1

    # Tenant B's own event is stored under B and is invisible to A's reads.
    other, created = await repo.get_or_create(
        _new_notification(TENANT_B, event_id="evt_notifications_0002")
    )
    assert created is True
    assert other.tenant_id == TENANT_B
    assert await repo.list_deliveries(TENANT_A, other.id) == []


async def test_delivery_unique_key_and_status_transitions(
    session: AsyncSession,
) -> None:
    """One row per `(notification, channel)`; terminal transitions persist."""
    repo = PostgresNotificationRepository(session)
    notification, _ = await repo.get_or_create(_new_notification(TENANT_A))

    email = await repo.add_delivery(
        NewDelivery(
            id=uuid7(),
            tenant_id=TENANT_A,
            notification_id=notification.id,
            channel=DeliveryChannel.EMAIL,
            created_at=NOW,
        )
    )
    duplicate = await repo.add_delivery(
        NewDelivery(
            id=uuid7(),
            tenant_id=TENANT_A,
            notification_id=notification.id,
            channel=DeliveryChannel.EMAIL,
            created_at=NOW,
        )
    )
    assert duplicate.id == email.id  # conflict-tolerant dedupe, not an error

    slack = await repo.add_delivery(
        NewDelivery(
            id=uuid7(),
            tenant_id=TENANT_A,
            notification_id=notification.id,
            channel=DeliveryChannel.SLACK,
            created_at=NOW,
        )
    )
    rows = await repo.list_deliveries(TENANT_A, notification.id)
    assert [row.channel for row in rows] == [
        DeliveryChannel.EMAIL,
        DeliveryChannel.SLACK,
    ]
    assert all(row.status is DeliveryStatus.PENDING for row in rows)
    assert all(row.attempts == 0 and row.sent_at is None for row in rows)

    sent = await repo.set_delivery_status(
        TENANT_A,
        email.id,
        status=DeliveryStatus.SENT,
        attempts=1,
        last_error=None,
        sent_at=NOW,
        updated_at=NOW,
    )
    assert sent.status is DeliveryStatus.SENT
    assert sent.attempts == 1
    assert sent.sent_at == NOW
    assert sent.last_error is None

    skipped = await repo.set_delivery_status(
        TENANT_A,
        slack.id,
        status=DeliveryStatus.SKIPPED,
        attempts=0,
        last_error=SKIP_NOT_CONFIGURED,
        sent_at=None,
        updated_at=NOW,
    )
    assert skipped.status is DeliveryStatus.SKIPPED
    assert skipped.last_error == SKIP_NOT_CONFIGURED
    assert skipped.sent_at is None

    stored = {
        row.channel: row
        for row in await repo.list_deliveries(TENANT_A, notification.id)
    }
    assert stored[DeliveryChannel.EMAIL].status is DeliveryStatus.SENT
    assert stored[DeliveryChannel.SLACK].status is DeliveryStatus.SKIPPED
    count = (
        await session.execute(select(func.count()).select_from(NotificationDeliveryRow))
    ).scalar_one()
    assert count == 2
