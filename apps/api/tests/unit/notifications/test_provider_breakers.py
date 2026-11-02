"""T5 unit tests — notification provider circuit breakers (ADR 0025).

The Resend and Slack adapters run every outbound call through their own
breaker. These tests prove the pinned semantics end to end without a provider:

- consecutive provider failures open the breaker at the threshold;
- an open breaker raises `CircuitOpenError` before any SDK/thread-pool work;
- after the cooldown, the single trial succeeds and closes the breaker;
- through `NotificationService`, an open breaker fails fast into the existing
  bounded-retry policy (3 attempts, terminal `failed`), leaves `last_error`
  payload-free, and the consumer's work keeps processing subsequent messages.

Everything uses fake clients, a fake clock and a recording sleeper — no
network, no wall-clock waits (`.ai/testing.md`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest

from app.domains.notifications.exceptions import (
    NotConfiguredError,
    NotificationProviderError,
)
from app.domains.notifications.models import (
    DeliveryChannel,
    DeliveryStatus,
    NotificationKind,
    RenderedMessage,
)
from app.domains.notifications.schemas import (
    NotificationContent,
    NotificationIntent,
    Recipient,
)
from app.domains.notifications.service import NotificationService
from app.infrastructure.notifications.resend_email import ResendEmailSender
from app.infrastructure.notifications.slack_web import SlackWebSender
from app.shared.circuit_breaker import (
    BreakerState,
    CircuitBreaker,
    CircuitOpenError,
)
from tests.unit.notifications.fakes import (
    FakeNotificationRepository,
    RecordingCommitter,
    RecordingSleeper,
)

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000002")
MANAGER = UUID("00000000-0000-4000-8000-000000000003")
RECIPIENT_USER = UUID("00000000-0000-4000-8000-000000000031")
DELIVERY_ID = UUID("00000000-0000-4000-8000-0000000000d1")
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)

RESEND_KEY = "re_test_provider_secret"
FROM_ADDRESS = "WorkPulse <notifications@example.test>"
SLACK_TOKEN = "xoxb-provider-secret"
SLACK_CHANNEL = "C0123456789"

RECIPIENT = Recipient(
    employee_id=MANAGER,
    user_id=RECIPIENT_USER,
    display_name="Mary Manager",
    account_address="mary.manager@example.test",
)
MESSAGE = RenderedMessage(
    subject="Leave request from Alice Nguyen",
    body="Alice Nguyen requested annual leave from 2026-11-05 to 2026-11-07.",
)


class FakeClock:
    """Monotonic clock driven explicitly by tests — no wall-clock sleeps."""

    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeResendClient:
    """Counts SDK calls; `error` is a settable always-raise hook."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = 0

    def send(
        self, params: dict[str, Any], options: dict[str, Any] | None = None
    ) -> object:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"id": "email_test_id"}


class FakeSlackClient:
    """Counts `chat.postMessage` calls; `error` is a settable raise hook."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = 0

    def chat_postMessage(self, *, channel: str, text: str) -> object:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"ok": True}


def _breaker(
    dependency: str,
    clock: FakeClock,
    *,
    threshold: int = 3,
    cooldown: float = 30.0,
) -> CircuitBreaker:
    return CircuitBreaker(
        dependency,
        failure_threshold=threshold,
        cooldown_seconds=cooldown,
        clock=clock,
    )


def _email_sender(
    client: FakeResendClient,
    breaker: CircuitBreaker,
    *,
    configured: bool = True,
) -> ResendEmailSender:
    return ResendEmailSender(
        api_key=RESEND_KEY if configured else None,
        from_address=FROM_ADDRESS if configured else None,
        client=client,
        breaker=breaker,
    )


def _slack_sender(
    client: FakeSlackClient,
    breaker: CircuitBreaker,
    *,
    configured: bool = True,
) -> SlackWebSender:
    return SlackWebSender(
        bot_token=SLACK_TOKEN if configured else None,
        channel_id=SLACK_CHANNEL if configured else None,
        client=client,
        breaker=breaker,
    )


def _intent(*, event_id: str) -> NotificationIntent:
    return NotificationIntent(
        kind=NotificationKind.LEAVE_REQUESTED,
        tenant_id=TENANT,
        event_id=event_id,
        event_type="leave.requested",
        recipient_employee_id=MANAGER,
        content=NotificationContent(
            subject_employee_id=EMPLOYEE,
            leave_type_code="annual",
            start_date=date(2026, 11, 5),
            end_date=date(2026, 11, 7),
            days=3,
        ),
    )


# --- adapter level --------------------------------------------------------


async def test_email_breaker_opens_after_the_threshold_and_stops_provider_io() -> None:
    clock = FakeClock()
    breaker = _breaker("resend", clock, threshold=3)
    client = FakeResendClient(error=RuntimeError("resend down"))
    sender = _email_sender(client, breaker)

    for _ in range(3):
        with pytest.raises(NotificationProviderError):
            await sender.send(
                recipient=RECIPIENT,
                message=MESSAGE,
                idempotency_key=DELIVERY_ID,
            )

    assert breaker.state is BreakerState.OPEN
    assert client.calls == 3

    with pytest.raises(CircuitOpenError):
        await sender.send(
            recipient=RECIPIENT,
            message=MESSAGE,
            idempotency_key=DELIVERY_ID,
        )

    # No SDK call, no thread-pool work: the breaker rejected before the seam.
    assert client.calls == 3


async def test_slack_breaker_opens_after_the_threshold_and_stops_provider_io() -> None:
    clock = FakeClock()
    breaker = _breaker("slack", clock, threshold=2)
    client = FakeSlackClient(error=RuntimeError("slack down"))
    sender = _slack_sender(client, breaker)

    for _ in range(2):
        with pytest.raises(NotificationProviderError):
            await sender.send(recipient=RECIPIENT, message=MESSAGE)

    assert breaker.state is BreakerState.OPEN
    assert client.calls == 2

    with pytest.raises(CircuitOpenError):
        await sender.send(recipient=RECIPIENT, message=MESSAGE)
    assert client.calls == 2


async def test_email_breaker_trial_closes_after_the_cooldown() -> None:
    clock = FakeClock()
    breaker = _breaker("resend", clock, threshold=1)
    client = FakeResendClient(error=RuntimeError("resend down"))
    sender = _email_sender(client, breaker)

    with pytest.raises(NotificationProviderError):
        await sender.send(
            recipient=RECIPIENT,
            message=MESSAGE,
            idempotency_key=DELIVERY_ID,
        )
    assert breaker.state is BreakerState.OPEN

    client.error = None
    clock.advance(30.0)
    await sender.send(
        recipient=RECIPIENT,
        message=MESSAGE,
        idempotency_key=DELIVERY_ID,
    )

    assert breaker.state is BreakerState.CLOSED
    assert client.calls == 2


async def test_unconfigured_sender_skips_even_with_an_open_breaker() -> None:
    """Missing configuration is not dependency health: no breaker I/O."""
    clock = FakeClock()
    breaker = _breaker("resend", clock, threshold=1)

    def fail() -> None:
        raise RuntimeError("resend down")

    with pytest.raises(RuntimeError):
        breaker.call_sync(fail)
    assert breaker.state is BreakerState.OPEN

    client = FakeResendClient()
    sender = _email_sender(client, breaker, configured=False)

    with pytest.raises(NotConfiguredError):
        await sender.send(
            recipient=RECIPIENT,
            message=MESSAGE,
            idempotency_key=DELIVERY_ID,
        )

    assert client.calls == 0
    assert breaker.state is BreakerState.OPEN


# --- service level --------------------------------------------------------


def _service(
    repo: FakeNotificationRepository,
    email_sender: ResendEmailSender,
    slack_sender: SlackWebSender,
    sleeper: RecordingSleeper,
) -> NotificationService:
    return NotificationService(
        repo,
        email_sender,
        slack_sender,
        commit=RecordingCommitter(),
        max_attempts=3,
        backoff_base_seconds=0.5,
        sleep=sleeper,
    )


async def test_open_breaker_fails_delivery_fast_into_bounded_retries() -> None:
    """AC — provider down: fast terminal `failed`, loop keeps processing.

    The first two provider attempts open the breaker; the third is rejected
    before any I/O. `last_error` is payload-free, the other channel still
    reaches its terminal skip, and a subsequent message is handled (and fails
    fast) without a hang. After the provider recovers and the cooldown elapses,
    the next message succeeds and the trial closes the breaker.
    """
    clock = FakeClock()
    breaker = _breaker("resend", clock, threshold=2)
    client = FakeResendClient(
        error=RuntimeError(
            f"rejected to={RECIPIENT.account_address} key={RESEND_KEY} "
            f"subject={MESSAGE.subject} body={MESSAGE.body}"
        )
    )
    email_sender = _email_sender(client, breaker)
    slack_sender = _slack_sender(
        FakeSlackClient(), _breaker("slack", clock), configured=False
    )
    repo = FakeNotificationRepository()
    sleeper = RecordingSleeper()
    service = _service(repo, email_sender, slack_sender, sleeper)

    summary = await service.handle(
        _intent(event_id="evt_breaker_1"), RECIPIENT, MESSAGE, NOW
    )
    outcomes = {item.channel: item for item in summary.deliveries}

    email = outcomes[DeliveryChannel.EMAIL]
    assert email.status is DeliveryStatus.FAILED
    assert email.attempts == 3
    assert email.last_error is not None
    for secret in (
        RESEND_KEY,
        RECIPIENT.account_address,
        MESSAGE.subject,
        MESSAGE.body,
    ):
        assert secret not in email.last_error

    # Two provider calls (the threshold); the third attempt failed fast.
    assert client.calls == 2
    assert sleeper.delays == [0.5, 1.0]
    assert outcomes[DeliveryChannel.SLACK].status is DeliveryStatus.SKIPPED
    assert breaker.state is BreakerState.OPEN

    # The consumer keeps processing: the next message is terminal without I/O.
    second = await service.handle(
        _intent(event_id="evt_breaker_2"), RECIPIENT, MESSAGE, NOW
    )
    second_email = next(
        item for item in second.deliveries if item.channel is DeliveryChannel.EMAIL
    )
    assert second_email.status is DeliveryStatus.FAILED
    assert second_email.attempts == 3
    assert client.calls == 2
    assert sleeper.delays == [0.5, 1.0, 0.5, 1.0]

    # Recovery: the half-open trial is a real send and closes the breaker.
    client.error = None
    clock.advance(30.0)
    third = await service.handle(
        _intent(event_id="evt_breaker_3"), RECIPIENT, MESSAGE, NOW
    )
    third_email = next(
        item for item in third.deliveries if item.channel is DeliveryChannel.EMAIL
    )
    assert third_email.status is DeliveryStatus.SENT
    assert third_email.attempts == 1
    assert client.calls == 3
    assert breaker.state is BreakerState.CLOSED
