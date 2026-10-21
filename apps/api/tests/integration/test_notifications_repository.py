"""Repository integration tests for Notifications (T3, T2 inbox).

Real PostgreSQL: the two unique keys are dedupe guarantees in both directions
(same key → one row, another recipient → its own row), every statement is
tenant-scoped, delivery status transitions persist, and the inbox read side
orders/paginates one recipient's rows while writing only `read_at`. No
provider or broker is involved.
"""

from datetime import UTC, datetime, timedelta
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
from app.shared.pagination import PageParams
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
    created_at: datetime = NOW,
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
        created_at=created_at,
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


async def test_list_for_recipient_orders_and_paginates_one_inbox(
    session: AsyncSession,
) -> None:
    """AC: own rows only, newest first, paginated, with `read_at` mapped."""
    repo = PostgresNotificationRepository(session)
    oldest, _ = await repo.get_or_create(
        _new_notification(TENANT_A, event_id="evt_inbox_0001")
    )
    middle, _ = await repo.get_or_create(
        _new_notification(
            TENANT_A,
            event_id="evt_inbox_0002",
            created_at=NOW + timedelta(minutes=1),
        )
    )
    newest, _ = await repo.get_or_create(
        _new_notification(
            TENANT_A,
            event_id="evt_inbox_0003",
            created_at=NOW + timedelta(minutes=2),
        )
    )
    # Another person in the same tenant and the same person in another tenant
    # must never appear in this inbox.
    await repo.get_or_create(
        _new_notification(
            TENANT_A,
            event_id="evt_inbox_0004",
            recipient_employee_id=EMPLOYEE,
            created_at=NOW + timedelta(minutes=3),
        )
    )
    await repo.get_or_create(
        _new_notification(
            TENANT_B,
            event_id="evt_inbox_0005",
            created_at=NOW + timedelta(minutes=4),
        )
    )

    first_page, total = await repo.list_for_recipient(
        TENANT_A, MANAGER, PageParams(page=1, page_size=2)
    )
    assert total == 3
    assert [row.id for row in first_page] == [newest.id, middle.id]
    assert all(row.read_at is None for row in first_page)

    second_page, total = await repo.list_for_recipient(
        TENANT_A, MANAGER, PageParams(page=2, page_size=2)
    )
    assert total == 3
    assert [row.id for row in second_page] == [oldest.id]


async def test_mark_read_is_idempotent_and_updates_unread_count(
    session: AsyncSession,
) -> None:
    """AC: the first mark sets `read_at`; the second keeps the timestamp."""
    repo = PostgresNotificationRepository(session)
    notification, _ = await repo.get_or_create(
        _new_notification(TENANT_A, event_id="evt_inbox_0001")
    )
    other, _ = await repo.get_or_create(
        _new_notification(TENANT_A, event_id="evt_inbox_0002")
    )
    assert await repo.count_unread(TENANT_A, MANAGER) == 2

    assert await repo.mark_read(TENANT_A, notification.id, read_at=NOW) is True
    stored = await repo.get(TENANT_A, notification.id)
    assert stored is not None
    assert stored.read_at == NOW
    assert await repo.count_unread(TENANT_A, MANAGER) == 1

    later = NOW + timedelta(hours=1)
    assert await repo.mark_read(TENANT_A, notification.id, read_at=later) is False
    reread = await repo.get(TENANT_A, notification.id)
    assert reread is not None
    assert reread.read_at == NOW  # the original timestamp wins
    assert await repo.count_unread(TENANT_A, MANAGER) == 1

    unchanged = await repo.get(TENANT_A, other.id)
    assert unchanged is not None
    assert unchanged.read_at is None

    assert await repo.mark_read(TENANT_A, uuid7(), read_at=NOW) is False


async def test_mark_all_read_marks_only_the_currently_unread_rows(
    session: AsyncSession,
) -> None:
    """AC: k unread → k writes; read timestamps are kept; a later row stays unread."""
    repo = PostgresNotificationRepository(session)
    already_read, _ = await repo.get_or_create(
        _new_notification(TENANT_A, event_id="evt_inbox_0001")
    )
    first_unread, _ = await repo.get_or_create(
        _new_notification(
            TENANT_A,
            event_id="evt_inbox_0002",
            created_at=NOW + timedelta(minutes=1),
        )
    )
    second_unread, _ = await repo.get_or_create(
        _new_notification(
            TENANT_A,
            event_id="evt_inbox_0003",
            created_at=NOW + timedelta(minutes=2),
        )
    )
    original_read_at = NOW + timedelta(minutes=30)
    await repo.mark_read(TENANT_A, already_read.id, read_at=original_read_at)

    marked_at = NOW + timedelta(hours=1)
    changed = await repo.mark_all_read(TENANT_A, MANAGER, read_at=marked_at)
    assert changed == 2

    rows, _ = await repo.list_for_recipient(
        TENANT_A, MANAGER, PageParams(page=1, page_size=10)
    )
    by_id = {row.id: row for row in rows}
    assert by_id[already_read.id].read_at == original_read_at
    assert by_id[first_unread.id].read_at == marked_at
    assert by_id[second_unread.id].read_at == marked_at
    assert await repo.count_unread(TENANT_A, MANAGER) == 0

    # A row created after the statement is not covered by it.
    late, _ = await repo.get_or_create(
        _new_notification(
            TENANT_A,
            event_id="evt_inbox_0004",
            created_at=NOW + timedelta(hours=2),
        )
    )
    late_row = await repo.get(TENANT_A, late.id)
    assert late_row is not None
    assert late_row.read_at is None
    assert await repo.count_unread(TENANT_A, MANAGER) == 1


async def test_read_side_is_tenant_and_recipient_scoped(
    session: AsyncSession,
) -> None:
    """AC: foreign tenants and other recipients cannot see or mark an inbox."""
    repo = PostgresNotificationRepository(session)
    own, _ = await repo.get_or_create(
        _new_notification(TENANT_A, event_id="evt_inbox_0001")
    )
    other_person, _ = await repo.get_or_create(
        _new_notification(
            TENANT_A,
            event_id="evt_inbox_0002",
            recipient_employee_id=EMPLOYEE,
        )
    )
    foreign_tenant, _ = await repo.get_or_create(
        _new_notification(TENANT_B, event_id="evt_inbox_0003")
    )

    # Tenant scoping: another tenant cannot load or mark the row.
    assert await repo.get(TENANT_B, own.id) is None
    assert await repo.mark_read(TENANT_B, own.id, read_at=NOW) is False
    stored = await repo.get(TENANT_A, own.id)
    assert stored is not None and stored.read_at is None

    # Recipient scoping: list/count/read-all see exactly one inbox each.
    page, total = await repo.list_for_recipient(
        TENANT_A, MANAGER, PageParams(page=1, page_size=10)
    )
    assert total == 1
    assert [row.id for row in page] == [own.id]
    assert await repo.count_unread(TENANT_A, MANAGER) == 1
    assert await repo.count_unread(TENANT_A, EMPLOYEE) == 1
    assert await repo.count_unread(TENANT_B, MANAGER) == 1

    # Read-all is recipient-scoped: the other person stays unread.
    assert await repo.mark_all_read(TENANT_A, MANAGER, read_at=NOW) == 1
    own_after = await repo.get(TENANT_A, own.id)
    other_after = await repo.get(TENANT_A, other_person.id)
    foreign_after = await repo.get(TENANT_B, foreign_tenant.id)
    assert own_after is not None and own_after.read_at == NOW
    assert other_after is not None and other_after.read_at is None
    assert foreign_after is not None and foreign_after.read_at is None

    # Foreign tenant read-all is a no-op.
    assert await repo.mark_all_read(TENANT_B, EMPLOYEE, read_at=NOW) == 0
    assert await repo.count_unread(TENANT_B, MANAGER) == 1
