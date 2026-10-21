"""T4 unit tests — provider adapters against fake SDK clients (no network).

The adapters' whole job is the boundary: configured sends cross the
`anyio.to_thread` bridge with exactly the allowlisted SDK fields, missing
configuration becomes a terminal `NotConfiguredError` without an SDK call, and
a hostile provider error is scrubbed of message content, recipient details and
credentials before it leaves the adapter (`.ai/testing.md`: providers are
never hit, gates run with every container stopped).
"""

import threading
from typing import Any
from uuid import UUID

import pytest

from app.domains.notifications.exceptions import (
    NotConfiguredError,
    NotificationProviderError,
)
from app.domains.notifications.models import RenderedMessage
from app.domains.notifications.schemas import Recipient
from app.infrastructure.notifications.resend_email import ResendEmailSender
from app.infrastructure.notifications.slack_web import SlackWebSender

EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000001")
USER_ID = UUID("00000000-0000-4000-8000-000000000031")
DELIVERY_ID = UUID("00000000-0000-4000-8000-0000000000d1")

RECIPIENT = Recipient(
    employee_id=EMPLOYEE_ID,
    user_id=USER_ID,
    display_name="Mara Quinn",
    account_address="mara.quinn@example.test",
)
MESSAGE = RenderedMessage(
    subject="Leave request from Zorblax Qqweird",
    body=(
        "Zorblax Qqweird requested annual leave from 2026-10-26 to 2026-10-28 (3 days)."
    ),
)

RESEND_KEY = "re_test_51H8secret"
FROM_ADDRESS = "WorkPulse <notifications@example.test>"
SLACK_TOKEN = "xoxb-test-secret-token"
SLACK_CHANNEL = "C0123456789"


class FakeResendClient:
    """Records the SDK call; optional raise hook simulates a provider error."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.calls: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        self.thread_id: int | None = None
        self.error = error

    def send(
        self, params: dict[str, Any], options: dict[str, Any] | None = None
    ) -> object:
        self.thread_id = threading.get_ident()
        self.calls.append((params, options))
        if self.error is not None:
            raise self.error
        return {"id": "email_test_id"}


class FakeSlackClient:
    """Records `chat.postMessage`; optional raise hook simulates a failure."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.thread_id: int | None = None
        self.error = error

    def chat_postMessage(self, *, channel: str, text: str) -> object:
        self.thread_id = threading.get_ident()
        self.calls.append((channel, text))
        if self.error is not None:
            raise self.error
        return {"ok": True}


def _email_sender(client: FakeResendClient) -> ResendEmailSender:
    return ResendEmailSender(
        api_key=RESEND_KEY,
        from_address=FROM_ADDRESS,
        client=client,
    )


def _slack_sender(client: FakeSlackClient) -> SlackWebSender:
    return SlackWebSender(
        bot_token=SLACK_TOKEN,
        channel_id=SLACK_CHANNEL,
        client=client,
    )


# --- Resend email ---------------------------------------------------------


async def test_email_send_crosses_the_thread_bridge_with_allowlisted_fields() -> None:
    client = FakeResendClient()
    sender = _email_sender(client)
    caller_thread = threading.get_ident()

    await sender.send(
        recipient=RECIPIENT,
        message=MESSAGE,
        idempotency_key=DELIVERY_ID,
    )

    assert client.thread_id is not None
    assert client.thread_id != caller_thread  # the sync SDK ran off-loop
    assert len(client.calls) == 1
    params, options = client.calls[0]
    assert params == {
        "from": FROM_ADDRESS,
        "to": [RECIPIENT.account_address],
        "subject": MESSAGE.subject,
        "text": MESSAGE.body,
    }
    # Resend's idempotency key is the delivery id: a crash-window retry
    # collapses at the provider (ADR 0015 decision 7).
    assert options == {"idempotency_key": str(DELIVERY_ID)}


@pytest.mark.parametrize(
    ("api_key", "from_address"),
    [
        (None, FROM_ADDRESS),
        ("", FROM_ADDRESS),
        ("   ", FROM_ADDRESS),
        (RESEND_KEY, None),
        (RESEND_KEY, ""),
        (RESEND_KEY, "   "),
    ],
)
async def test_email_without_configuration_skips_without_touching_the_provider(
    api_key: str | None, from_address: str | None
) -> None:
    client = FakeResendClient()
    sender = ResendEmailSender(
        api_key=api_key,
        from_address=from_address,
        client=client,
    )

    with pytest.raises(NotConfiguredError):
        await sender.send(
            recipient=RECIPIENT,
            message=MESSAGE,
            idempotency_key=DELIVERY_ID,
        )

    assert client.calls == []


async def test_email_provider_error_is_redacted_before_it_leaves_the_adapter() -> None:
    hostile = RuntimeError(
        f"rejected from={FROM_ADDRESS} to={RECIPIENT.account_address} "
        f"key={RESEND_KEY} subject={MESSAGE.subject} body={MESSAGE.body}"
    )
    sender = _email_sender(FakeResendClient(error=hostile))

    with pytest.raises(NotificationProviderError) as excinfo:
        await sender.send(
            recipient=RECIPIENT,
            message=MESSAGE,
            idempotency_key=DELIVERY_ID,
        )

    text = str(excinfo.value)
    assert "RuntimeError" in text  # provider type survives for diagnosis
    for secret in (
        RESEND_KEY,
        FROM_ADDRESS,
        RECIPIENT.account_address,
        RECIPIENT.display_name,
        MESSAGE.subject,
        MESSAGE.body,
    ):
        assert secret not in text


# --- Slack ----------------------------------------------------------------


async def test_slack_send_posts_the_rendered_message_to_the_tenant_channel() -> None:
    client = FakeSlackClient()
    sender = _slack_sender(client)
    caller_thread = threading.get_ident()

    await sender.send(recipient=RECIPIENT, message=MESSAGE)

    assert client.thread_id is not None
    assert client.thread_id != caller_thread  # the sync SDK ran off-loop
    assert len(client.calls) == 1
    channel, text = client.calls[0]
    assert channel == SLACK_CHANNEL
    assert text == f"{MESSAGE.subject}\n\n{MESSAGE.body}"
    # The channel is shared: the affected person is named, but the recipient's
    # account address and sensitive free text never appear (ADR 0015
    # decision 4/6).
    assert "Zorblax Qqweird" in text
    assert RECIPIENT.account_address not in text
    assert "reason" not in text.lower()
    assert "decision_note" not in text.lower()


@pytest.mark.parametrize(
    ("bot_token", "channel_id"),
    [
        (None, SLACK_CHANNEL),
        ("", SLACK_CHANNEL),
        ("   ", SLACK_CHANNEL),
        (SLACK_TOKEN, None),
        (SLACK_TOKEN, ""),
        (SLACK_TOKEN, "   "),
    ],
)
async def test_slack_without_configuration_skips_without_touching_the_provider(
    bot_token: str | None, channel_id: str | None
) -> None:
    client = FakeSlackClient()
    sender = SlackWebSender(
        bot_token=bot_token,
        channel_id=channel_id,
        client=client,
    )

    with pytest.raises(NotConfiguredError):
        await sender.send(recipient=RECIPIENT, message=MESSAGE)

    assert client.calls == []


async def test_slack_provider_error_is_redacted_before_it_leaves_the_adapter() -> None:
    hostile = RuntimeError(
        f"rejected token={SLACK_TOKEN} user={RECIPIENT.display_name} "
        f"subject={MESSAGE.subject} body={MESSAGE.body}"
    )
    sender = _slack_sender(FakeSlackClient(error=hostile))

    with pytest.raises(NotificationProviderError) as excinfo:
        await sender.send(recipient=RECIPIENT, message=MESSAGE)

    text = str(excinfo.value)
    assert "RuntimeError" in text
    for secret in (
        SLACK_TOKEN,
        RECIPIENT.account_address,
        RECIPIENT.display_name,
        MESSAGE.subject,
        MESSAGE.body,
    ):
        assert secret not in text
