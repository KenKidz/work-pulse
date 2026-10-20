"""Unit tests for `NotificationService` — delivery bookkeeping (T2).

Pure: in-memory repository, recording senders, recording sleeper. No database,
broker or provider is touched, and no test waits on the wall clock
(`.ai/testing.md`). The fake repository enforces the same dedupe keys as the
Postgres adapter, so these tests pin the semantics the worker relies on:
dedupe, terminal-only re-attempts, bounded retries, unconfigured skips, PII-free
`last_error`, and store-error propagation (the commit gate).
"""

from dataclasses import fields
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest

from app.domains.notifications.exceptions import NotConfiguredError
from app.domains.notifications.models import (
    SKIP_NOT_CONFIGURED,
    DeliveryChannel,
    DeliveryOutcome,
    DeliveryStatus,
    DeliverySummary,
    NotificationDelivery,
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
from tests.unit.notifications.fakes import (
    FakeNotificationRepository,
    RecordingCommitter,
    RecordingEmailSender,
    RecordingSlackSender,
    RecordingSleeper,
)

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000002")
MANAGER = UUID("00000000-0000-4000-8000-000000000003")
RECIPIENT_USER = UUID("00000000-0000-4000-8000-000000000031")
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

MESSAGE = RenderedMessage(
    subject="Leave request from Alice Nguyen",
    body=(
        "Alice Nguyen requested annual leave from 2026-10-22 to 2026-10-24 (3 days)."
    ),
)

RECIPIENT = Recipient(
    employee_id=MANAGER,
    user_id=RECIPIENT_USER,
    display_name="Mary Manager",
    account_address="mary.manager@example.com",
)


def notification_intent(
    *,
    kind: NotificationKind = NotificationKind.LEAVE_REQUESTED,
    event_id: str = "evt_01",
    event_type: str = "leave.requested",
    recipient_employee_id: UUID = MANAGER,
    content: NotificationContent | None = None,
) -> NotificationIntent:
    return NotificationIntent(
        kind=kind,
        tenant_id=TENANT,
        event_id=event_id,
        event_type=event_type,
        recipient_employee_id=recipient_employee_id,
        content=content
        if content is not None
        else NotificationContent(
            subject_employee_id=EMPLOYEE,
            leave_type_code="annual",
            start_date=date(2026, 10, 22),
            end_date=date(2026, 10, 24),
            days=3,
        ),
    )


def build_service(
    repo: FakeNotificationRepository,
    email: RecordingEmailSender,
    slack: RecordingSlackSender,
    sleeper: RecordingSleeper,
    *,
    max_attempts: int = 3,
    backoff_base_seconds: float = 0.5,
) -> NotificationService:
    return NotificationService(
        repo,
        email,
        slack,
        commit=RecordingCommitter(),
        max_attempts=max_attempts,
        backoff_base_seconds=backoff_base_seconds,
        sleep=sleeper,
    )


def _outcome(summary: DeliverySummary, channel: DeliveryChannel) -> DeliveryOutcome:
    return next(item for item in summary.deliveries if item.channel is channel)


def _delivery(
    repo: FakeNotificationRepository,
    notification_id: UUID,
    channel: DeliveryChannel,
) -> NotificationDelivery:
    return next(
        row
        for row in repo.deliveries.values()
        if row.notification_id == notification_id and row.channel is channel
    )


class _CommitObservingEmailSender(RecordingEmailSender):
    """Asserts the intent and pending rows were committed before the send."""

    def __init__(self, committer: RecordingCommitter) -> None:
        super().__init__()
        self._committer = committer

    async def send(
        self,
        *,
        recipient: Recipient,
        message: RenderedMessage,
        idempotency_key: UUID,
    ) -> None:
        assert self._committer.calls == 1
        await super().send(
            recipient=recipient,
            message=message,
            idempotency_key=idempotency_key,
        )


class _CommitObservingSlackSender(RecordingSlackSender):
    """Asserts email's terminal state was committed before Slack runs."""

    def __init__(self, committer: RecordingCommitter) -> None:
        super().__init__()
        self._committer = committer

    async def send(self, *, recipient: Recipient, message: RenderedMessage) -> None:
        assert self._committer.calls == 2
        await super().send(recipient=recipient, message=message)


# --- happy path + dedupe -----------------------------------------------------


async def test_intent_and_each_terminal_state_are_committed_before_next_step() -> None:
    repo = FakeNotificationRepository()
    committer = RecordingCommitter()
    email = _CommitObservingEmailSender(committer)
    slack = _CommitObservingSlackSender(committer)
    service = NotificationService(
        repo,
        email,
        slack,
        commit=committer,
        sleep=RecordingSleeper(),
    )

    summary = await service.handle(notification_intent(), RECIPIENT, MESSAGE, NOW)

    assert summary.terminal is True
    # pending rows, then email, then Slack terminal transitions
    assert committer.calls == 3


async def test_handle_stores_the_notification_and_sends_both_channels() -> None:
    repo = FakeNotificationRepository()
    email = RecordingEmailSender()
    slack = RecordingSlackSender()
    sleeper = RecordingSleeper()
    service = build_service(repo, email, slack, sleeper)

    summary = await service.handle(notification_intent(), RECIPIENT, MESSAGE, NOW)

    assert summary.created is True
    assert summary.terminal is True
    assert [item.channel for item in summary.deliveries] == [
        DeliveryChannel.EMAIL,
        DeliveryChannel.SLACK,
    ]
    assert all(item.status is DeliveryStatus.SENT for item in summary.deliveries)
    assert all(item.attempts == 1 for item in summary.deliveries)
    assert sleeper.delays == []

    stored = repo.notifications[(TENANT, summary.notification_id)]
    assert stored.event_id == "evt_01"
    assert stored.event_type == "leave.requested"
    assert stored.kind is NotificationKind.LEAVE_REQUESTED
    assert stored.recipient_employee_id == MANAGER
    assert stored.recipient_user_id == RECIPIENT_USER
    assert stored.subject == MESSAGE.subject
    assert stored.body == MESSAGE.body

    email_row = _delivery(repo, summary.notification_id, DeliveryChannel.EMAIL)
    slack_row = _delivery(repo, summary.notification_id, DeliveryChannel.SLACK)
    assert email_row.sent_at == NOW
    assert slack_row.sent_at == NOW
    # The email idempotency key is the delivery row id (ADR 0015 decision 7).
    assert email.sent[0][2] == email_row.id
    assert slack.sent[0][1] == MESSAGE


async def test_duplicate_handle_is_a_dedupe_no_op() -> None:
    repo = FakeNotificationRepository()
    email = RecordingEmailSender()
    slack = RecordingSlackSender()
    service = build_service(repo, email, slack, RecordingSleeper())
    intent = notification_intent()

    first = await service.handle(intent, RECIPIENT, MESSAGE, NOW)
    calls_after_first = (len(email.calls), len(slack.calls))
    second = await service.handle(intent, RECIPIENT, MESSAGE, NOW)

    assert second.created is False
    assert second.notification_id == first.notification_id
    assert (len(email.calls), len(slack.calls)) == calls_after_first
    assert len(repo.notifications) == 1
    assert [item.status for item in second.deliveries] == [
        DeliveryStatus.SENT,
        DeliveryStatus.SENT,
    ]
    assert second.terminal is True


async def test_redelivery_attempts_only_pending_deliveries() -> None:
    """A `sent` delivery is never re-sent; a crash-left `pending` one is."""
    repo = FakeNotificationRepository()
    email = RecordingEmailSender()
    slack = RecordingSlackSender()
    service = build_service(repo, email, slack, RecordingSleeper())
    intent = notification_intent()

    notification, created = await repo.get_or_create(
        NewNotification(
            id=uuid4(),
            tenant_id=TENANT,
            event_id=intent.event_id,
            event_type=intent.event_type,
            kind=intent.kind,
            recipient_employee_id=intent.recipient_employee_id,
            recipient_user_id=RECIPIENT_USER,
            subject="Stored subject",
            body="Stored body",
            created_at=NOW,
        )
    )
    assert created
    sent_email = await repo.add_delivery(
        NewDelivery(
            id=uuid4(),
            tenant_id=TENANT,
            notification_id=notification.id,
            channel=DeliveryChannel.EMAIL,
            created_at=NOW,
        )
    )
    await repo.add_delivery(
        NewDelivery(
            id=uuid4(),
            tenant_id=TENANT,
            notification_id=notification.id,
            channel=DeliveryChannel.SLACK,
            created_at=NOW,
        )
    )
    await repo.set_delivery_status(
        TENANT,
        sent_email.id,
        status=DeliveryStatus.SENT,
        attempts=1,
        last_error=None,
        sent_at=NOW,
        updated_at=NOW,
    )

    summary = await service.handle(intent, RECIPIENT, MESSAGE, NOW)

    assert summary.created is False
    assert summary.notification_id == notification.id
    assert email.calls == []
    assert len(slack.calls) == 1
    assert _outcome(summary, DeliveryChannel.EMAIL).status is DeliveryStatus.SENT
    assert _outcome(summary, DeliveryChannel.SLACK).status is DeliveryStatus.SENT
    # The stored message of record is never overwritten by a redelivery.
    assert repo.notifications[(TENANT, notification.id)].subject == "Stored subject"
    # The re-attempt sends the stored content, not the fresh argument.
    assert slack.sent[0][1] == RenderedMessage(
        subject="Stored subject", body="Stored body"
    )


# --- retries + failure -------------------------------------------------------


async def test_transient_error_is_retried_with_doubling_backoff() -> None:
    repo = FakeNotificationRepository()
    email = RecordingEmailSender()
    email.errors = [RuntimeError("temporary glitch")]
    slack = RecordingSlackSender()
    sleeper = RecordingSleeper()
    service = build_service(repo, email, slack, sleeper)

    summary = await service.handle(notification_intent(), RECIPIENT, MESSAGE, NOW)

    outcome = _outcome(summary, DeliveryChannel.EMAIL)
    assert outcome.status is DeliveryStatus.SENT
    assert outcome.attempts == 2
    assert len(email.calls) == 2
    assert sleeper.delays == [0.5]


async def test_email_fails_after_three_attempts_while_slack_completes() -> None:
    repo = FakeNotificationRepository()
    email = RecordingEmailSender(error=RuntimeError("provider exploded"))
    slack = RecordingSlackSender()
    sleeper = RecordingSleeper()
    service = build_service(repo, email, slack, sleeper)

    summary = await service.handle(notification_intent(), RECIPIENT, MESSAGE, NOW)

    assert len(email.calls) == 3
    assert email.sent == []
    assert sleeper.delays == [0.5, 1.0]

    email_outcome = _outcome(summary, DeliveryChannel.EMAIL)
    assert email_outcome.status is DeliveryStatus.FAILED
    assert email_outcome.attempts == 3
    assert email_outcome.last_error is not None
    assert email_outcome.last_error.startswith("RuntimeError:")
    assert "provider exploded" in email_outcome.last_error

    slack_outcome = _outcome(summary, DeliveryChannel.SLACK)
    assert slack_outcome.status is DeliveryStatus.SENT
    assert len(slack.calls) == 1
    assert summary.terminal is True

    email_row = _delivery(repo, summary.notification_id, DeliveryChannel.EMAIL)
    assert email_row.sent_at is None


async def test_last_error_is_capped_at_300_characters() -> None:
    repo = FakeNotificationRepository()
    email = RecordingEmailSender(error=RuntimeError("x" * 500))
    service = build_service(repo, email, RecordingSlackSender(), RecordingSleeper())

    summary = await service.handle(notification_intent(), RECIPIENT, MESSAGE, NOW)

    last_error = _outcome(summary, DeliveryChannel.EMAIL).last_error
    assert last_error is not None
    assert len(last_error) == 300
    assert last_error.endswith("…")


# --- configuration + PII -----------------------------------------------------


@pytest.mark.parametrize("channel", [DeliveryChannel.EMAIL, DeliveryChannel.SLACK])
async def test_unconfigured_channel_is_skipped_without_retry(
    channel: DeliveryChannel,
) -> None:
    repo = FakeNotificationRepository()
    email = RecordingEmailSender(
        error=NotConfiguredError() if channel is DeliveryChannel.EMAIL else None
    )
    slack = RecordingSlackSender(
        error=NotConfiguredError() if channel is DeliveryChannel.SLACK else None
    )
    sleeper = RecordingSleeper()
    service = build_service(repo, email, slack, sleeper)

    summary = await service.handle(notification_intent(), RECIPIENT, MESSAGE, NOW)

    skipped = _outcome(summary, channel)
    assert skipped.status is DeliveryStatus.SKIPPED
    assert skipped.attempts == 0  # no provider call was attempted
    assert skipped.last_error == SKIP_NOT_CONFIGURED
    assert sleeper.delays == []

    calls = email.calls if channel is DeliveryChannel.EMAIL else slack.calls
    assert len(calls) == 1  # never retried: retrying cannot create config

    other = (
        DeliveryChannel.SLACK
        if channel is DeliveryChannel.EMAIL
        else DeliveryChannel.EMAIL
    )
    assert _outcome(summary, other).status is DeliveryStatus.SENT
    assert summary.terminal is True


async def test_hostile_provider_error_never_leaks_message_or_recipient() -> None:
    repo = FakeNotificationRepository()
    message = RenderedMessage(
        subject="Leave request from Alice Nguyen",
        body="Alice Nguyen requested annual leave from 2026-10-22 to 2026-10-24.",
    )
    leaked = (
        f"{message.body} for {RECIPIENT.account_address} ({RECIPIENT.display_name})"
    )
    email = RecordingEmailSender(error=RuntimeError(f"provider rejected: {leaked}"))
    service = build_service(repo, email, RecordingSlackSender(), RecordingSleeper())

    summary = await service.handle(notification_intent(), RECIPIENT, message, NOW)

    last_error = _outcome(summary, DeliveryChannel.EMAIL).last_error
    assert last_error is not None
    assert message.body not in last_error
    assert "Alice Nguyen" not in last_error
    assert RECIPIENT.account_address not in last_error
    assert RECIPIENT.display_name not in last_error
    assert "[redacted]" in last_error
    assert "provider rejected" in last_error


def test_content_bag_exposes_only_trigger_table_fields() -> None:
    """The closed allowlist: no `reason`, `decision_note` or free text."""
    names = {field.name for field in fields(NotificationContent)}

    assert names == {
        "subject_employee_id",
        "leave_type_code",
        "start_date",
        "end_date",
        "days",
        "workday",
        "minutes_late",
        "decided_by",
    }
    assert "reason" not in names
    assert "decision_note" not in names


# --- store errors ------------------------------------------------------------


async def test_store_error_propagates_and_leaves_the_delivery_pending() -> None:
    """The consumer must not commit: the provider accepted but the mark failed."""
    repo = FakeNotificationRepository()
    repo.status_error = RuntimeError("store down")
    email = RecordingEmailSender()
    slack = RecordingSlackSender()
    service = build_service(repo, email, slack, RecordingSleeper())

    with pytest.raises(RuntimeError, match="store down"):
        await service.handle(notification_intent(), RECIPIENT, MESSAGE, NOW)

    assert len(email.calls) == 1
    assert slack.calls == []
    notification = next(iter(repo.notifications.values()))
    email_row = _delivery(repo, notification.id, DeliveryChannel.EMAIL)
    assert email_row.status is DeliveryStatus.PENDING
