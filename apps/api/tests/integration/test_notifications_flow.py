"""Delivery service over the real database (T3).

The real `PostgresNotificationRepository` + `NotificationService` with
recording sender fakes and a recording sleeper: no provider is hit and no test
waits on the wall clock. Proves the adapter preserves the pinned semantics the
T2 unit tests pin in memory — dedupe, terminal-only re-attempts, bounded
retries with payload-free `last_error`, per-channel independence and the
commit gate (`DeliverySummary.terminal`).
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.domains.notifications.exceptions import NotConfiguredError
from app.domains.notifications.models import (
    SKIP_NOT_CONFIGURED,
    DeliveryChannel,
    DeliveryStatus,
    NotificationKind,
    RenderedMessage,
)
from app.domains.notifications.repository import NewDelivery, NewNotification
from app.domains.notifications.schemas import (
    NotificationContent,
    NotificationIntent,
    Recipient,
)
from app.domains.notifications.service import NotificationService
from app.infrastructure.postgres.models_notifications import NotificationRow
from app.infrastructure.postgres.repositories.notifications import (
    PostgresNotificationRepository,
)
from tests.integration.conftest import TENANT_A, TENANT_B
from tests.unit.notifications.fakes import (
    RecordingEmailSender,
    RecordingSlackSender,
    RecordingSleeper,
)

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
EMPLOYEE = uuid7()
MANAGER = uuid7()
RECIPIENT_USER = uuid7()

RECIPIENT = Recipient(
    employee_id=MANAGER,
    user_id=RECIPIENT_USER,
    display_name="Mary Manager",
    account_address="mary.manager@example.test",
)
MESSAGE = RenderedMessage(
    subject="Leave request from Ada Lovelace",
    body=(
        "Ada Lovelace requested annual leave from 2026-10-22 to 2026-10-24 (3 days)."
    ),
)


def _intent(
    *,
    tenant_id: UUID = TENANT_A,
    event_id: str = "evt_notifications_0001",
    recipient_employee_id: UUID = MANAGER,
) -> NotificationIntent:
    return NotificationIntent(
        kind=NotificationKind.LEAVE_REQUESTED,
        tenant_id=tenant_id,
        event_id=event_id,
        event_type="leave.requested",
        recipient_employee_id=recipient_employee_id,
        content=NotificationContent(subject_employee_id=EMPLOYEE),
    )


def _service(
    session: AsyncSession,
    *,
    email_sender: RecordingEmailSender | None = None,
    slack_sender: RecordingSlackSender | None = None,
) -> tuple[
    NotificationService, RecordingEmailSender, RecordingSlackSender, RecordingSleeper
]:
    email = email_sender if email_sender is not None else RecordingEmailSender()
    slack = slack_sender if slack_sender is not None else RecordingSlackSender()
    sleeper = RecordingSleeper()
    service = NotificationService(
        repo=PostgresNotificationRepository(session),
        email_sender=email,
        slack_sender=slack,
        commit=session.commit,
        sleep=sleeper,
        backoff_base_seconds=0.0,
    )
    return service, email, slack, sleeper


async def _rows(session: AsyncSession) -> list[NotificationRow]:
    stmt = select(NotificationRow).order_by(NotificationRow.id.asc())
    return list((await session.execute(stmt)).scalars())


async def test_handle_persists_notification_and_both_deliveries(
    session: AsyncSession,
) -> None:
    repo = PostgresNotificationRepository(session)
    service, email, slack, _ = _service(session)

    summary = await service.handle(_intent(), RECIPIENT, MESSAGE, NOW)

    assert summary.created is True
    assert summary.terminal is True
    assert {outcome.channel: outcome.status for outcome in summary.deliveries} == {
        DeliveryChannel.EMAIL: DeliveryStatus.SENT,
        DeliveryChannel.SLACK: DeliveryStatus.SENT,
    }

    rows = await _rows(session)
    assert len(rows) == 1
    stored = rows[0]
    assert stored.tenant_id == TENANT_A
    assert stored.event_id == "evt_notifications_0001"
    assert stored.kind == NotificationKind.LEAVE_REQUESTED.value
    assert stored.recipient_employee_id == MANAGER
    assert stored.recipient_user_id == RECIPIENT_USER
    assert stored.subject == MESSAGE.subject
    assert stored.body == MESSAGE.body

    deliveries = await repo.list_deliveries(TENANT_A, stored.id)
    by_channel = {row.channel: row for row in deliveries}
    assert set(by_channel) == {DeliveryChannel.EMAIL, DeliveryChannel.SLACK}
    assert by_channel[DeliveryChannel.EMAIL].sent_at == NOW
    assert by_channel[DeliveryChannel.SLACK].sent_at == NOW

    assert len(email.sent) == 1
    assert len(slack.sent) == 1
    # Email's idempotency key is the delivery id (crash-window protection).
    assert email.sent[0][2] == by_channel[DeliveryChannel.EMAIL].id
    assert slack.sent[0][0] == RECIPIENT


async def test_redelivery_creates_nothing_and_resends_no_sent_delivery(
    session: AsyncSession,
) -> None:
    service, email, slack, _ = _service(session)

    first = await service.handle(_intent(), RECIPIENT, MESSAGE, NOW)
    replay = await service.handle(_intent(), RECIPIENT, MESSAGE, NOW)

    assert first.created is True
    assert replay.created is False
    assert replay.notification_id == first.notification_id
    assert replay.terminal is True
    assert len(await _rows(session)) == 1
    assert len(email.calls) == 1
    assert len(slack.calls) == 1


async def test_email_failure_is_bounded_and_slack_still_completes(
    session: AsyncSession,
) -> None:
    """AC: ≤ 3 attempts, `failed` with payload-free `last_error`, Slack unaffected."""
    hostile = RuntimeError(
        f"provider echoed {MESSAGE.body} for {RECIPIENT.account_address} "
        f"({RECIPIENT.display_name})"
    )
    email = RecordingEmailSender(error=hostile)
    service, _, slack, sleeper = _service(session, email_sender=email)

    summary = await service.handle(_intent(), RECIPIENT, MESSAGE, NOW)

    outcomes = {outcome.channel: outcome for outcome in summary.deliveries}
    assert outcomes[DeliveryChannel.EMAIL].status is DeliveryStatus.FAILED
    assert outcomes[DeliveryChannel.EMAIL].attempts == 3
    assert outcomes[DeliveryChannel.SLACK].status is DeliveryStatus.SENT
    assert summary.terminal is True
    assert len(email.calls) == 3
    assert len(slack.sent) == 1
    # Two bounded backoffs between the three attempts, never a wall-clock wait.
    assert sleeper.delays == [0.0, 0.0]

    failed = outcomes[DeliveryChannel.EMAIL]
    assert failed.last_error is not None
    assert failed.last_error.startswith("RuntimeError: ")
    for secret in (
        MESSAGE.subject,
        MESSAGE.body,
        RECIPIENT.display_name,
        RECIPIENT.account_address,
    ):
        assert secret not in failed.last_error

    repo = PostgresNotificationRepository(session)
    stored = (await repo.list_deliveries(TENANT_A, summary.notification_id))[0]
    assert stored.status is DeliveryStatus.FAILED
    assert stored.attempts == 3
    assert stored.sent_at is None
    assert MESSAGE.body not in (stored.last_error or "")


async def test_not_configured_channel_skips_without_retrying(
    session: AsyncSession,
) -> None:
    email = RecordingEmailSender(error=NotConfiguredError())
    service, _, slack, _ = _service(session, email_sender=email)

    summary = await service.handle(_intent(), RECIPIENT, MESSAGE, NOW)

    outcomes = {outcome.channel: outcome for outcome in summary.deliveries}
    assert outcomes[DeliveryChannel.EMAIL].status is DeliveryStatus.SKIPPED
    assert outcomes[DeliveryChannel.EMAIL].last_error == SKIP_NOT_CONFIGURED
    assert outcomes[DeliveryChannel.EMAIL].attempts == 0
    assert outcomes[DeliveryChannel.SLACK].status is DeliveryStatus.SENT
    assert summary.terminal is True
    assert len(email.calls) == 1  # attempted once, never retried

    repo = PostgresNotificationRepository(session)
    stored = {
        row.channel: row
        for row in await repo.list_deliveries(TENANT_A, summary.notification_id)
    }
    assert stored[DeliveryChannel.EMAIL].status is DeliveryStatus.SKIPPED
    assert stored[DeliveryChannel.EMAIL].last_error == SKIP_NOT_CONFIGURED
    assert stored[DeliveryChannel.EMAIL].sent_at is None


async def test_redelivery_reattempts_only_pending_deliveries(
    session: AsyncSession,
) -> None:
    """A crash between the notification and a channel insert leaves work behind."""
    repo = PostgresNotificationRepository(session)
    service, email, slack, _ = _service(session)
    intent = _intent()

    stored_message = RenderedMessage(subject="Stored subject", body="Stored body")
    notification, created = await repo.get_or_create(
        NewNotification(
            id=uuid7(),
            tenant_id=intent.tenant_id,
            event_id=intent.event_id,
            event_type=intent.event_type,
            kind=intent.kind,
            recipient_employee_id=intent.recipient_employee_id,
            recipient_user_id=RECIPIENT.user_id,
            subject=stored_message.subject,
            body=stored_message.body,
            created_at=NOW,
        )
    )
    assert created is True
    pending_email = await repo.add_delivery(
        NewDelivery(
            id=uuid7(),
            tenant_id=intent.tenant_id,
            notification_id=notification.id,
            channel=DeliveryChannel.EMAIL,
            created_at=NOW,
        )
    )
    sent_slack = await repo.add_delivery(
        NewDelivery(
            id=uuid7(),
            tenant_id=intent.tenant_id,
            notification_id=notification.id,
            channel=DeliveryChannel.SLACK,
            created_at=NOW,
        )
    )
    await repo.set_delivery_status(
        intent.tenant_id,
        sent_slack.id,
        status=DeliveryStatus.SENT,
        attempts=1,
        last_error=None,
        sent_at=NOW,
        updated_at=NOW,
    )

    summary = await service.handle(intent, RECIPIENT, MESSAGE, NOW)

    assert summary.created is False
    assert summary.notification_id == notification.id
    outcomes = {outcome.channel: outcome for outcome in summary.deliveries}
    assert outcomes[DeliveryChannel.EMAIL].status is DeliveryStatus.SENT
    assert outcomes[DeliveryChannel.EMAIL].attempts == 1
    assert outcomes[DeliveryChannel.SLACK].status is DeliveryStatus.SENT
    assert outcomes[DeliveryChannel.SLACK].attempts == 1
    assert len(email.sent) == 1
    assert slack.calls == []  # already `sent` — never re-posted
    assert len(await _rows(session)) == 1
    assert pending_email.id != sent_slack.id
    # The pending re-attempt sends the stored message of record.
    assert email.sent[0][1] == stored_message


async def test_rows_carry_the_envelope_tenant(session: AsyncSession) -> None:
    service, _, _, _ = _service(session)

    await service.handle(
        _intent(tenant_id=TENANT_B, event_id="evt_notifications_0002"),
        RECIPIENT,
        MESSAGE,
        NOW,
    )

    rows = await _rows(session)
    assert [row.tenant_id for row in rows] == [TENANT_B]
    count = (
        await session.execute(select(func.count()).select_from(NotificationRow))
    ).scalar_one()
    assert count == 1
