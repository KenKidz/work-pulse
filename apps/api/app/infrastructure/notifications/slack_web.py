"""Slack sender — the `SlackSender` port over the Web API (T4).

ADR 0015 decision 5/7: `chat.postMessage` targets the one env-configured
channel id and posts the rendered message of record, which names the affected
person. There is no per-user Slack identity mapping and no idempotency key —
the accepted v1 duplicate-post window is documented in the ADR. The SDK is
synchronous, so the call crosses `anyio.to_thread.run_sync`.

Missing bot token or channel id raises `NotConfiguredError` before any SDK
work; provider failures are translated to a payload-free
`NotificationProviderError`. Tests inject a fake SDK client, so Slack is never
called (`.ai/testing.md`).

Every outbound post runs through the `slack` circuit breaker (Phase 7, ADR
0025): once the breaker is open, `send` raises `CircuitOpenError` before any SDK
or thread-pool work, so the service's bounded-retry policy fails fast instead
of waiting on a dead provider.
"""

from functools import partial
from typing import Protocol

import anyio
from slack_sdk import WebClient

from app.domains.notifications.exceptions import NotConfiguredError
from app.domains.notifications.models import RenderedMessage
from app.domains.notifications.schemas import Recipient
from app.infrastructure.notifications.errors import provider_error
from app.shared.circuit_breaker import CircuitBreaker, CircuitOpenError


class SlackClient(Protocol):
    """The one Web API call the adapter makes; injectable for unit tests."""

    def chat_postMessage(self, *, channel: str, text: str) -> object:
        """Post one message; raise on provider failure."""
        ...


class _SlackSdkClient:
    """Production client: the official `slack_sdk` WebClient."""

    def __init__(self, token: str) -> None:
        self._client = WebClient(token=token)

    def chat_postMessage(self, *, channel: str, text: str) -> object:
        return self._client.chat_postMessage(channel=channel, text=text)


class SlackWebSender:
    """`SlackSender` posting the rendered notification to the tenant channel."""

    def __init__(
        self,
        *,
        bot_token: str | None,
        channel_id: str | None,
        client: SlackClient | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._bot_token = bot_token.strip() if bot_token else None
        self._channel_id = channel_id.strip() if channel_id else None
        self._client = client
        self._breaker = breaker or CircuitBreaker.from_settings("slack")

    async def send(self, *, recipient: Recipient, message: RenderedMessage) -> None:
        token = self._bot_token
        channel = self._channel_id
        if not token or not channel:
            # Terminal skip, never retried: the other channel still proceeds.
            # Checked before the breaker: missing configuration is not
            # dependency health.
            raise NotConfiguredError()
        client = self._client if self._client is not None else _SlackSdkClient(token)

        # The rendered message is the message of record; the adapter adds no
        # fields (no address, no free text) beyond a blank line between the
        # subject line and the body.
        text = f"{message.subject}\n\n{message.body}"
        try:
            await self._breaker.call(
                lambda: anyio.to_thread.run_sync(
                    partial(client.chat_postMessage, channel=channel, text=text)
                )
            )
        except CircuitOpenError:
            # Fail fast into the service's bounded-retry policy; the error
            # carries no provider payload and must not be re-wrapped.
            raise
        except Exception as exc:
            raise provider_error(
                exc,
                sensitive=(
                    token,
                    recipient.display_name,
                    recipient.account_address,
                    message.subject,
                    message.body,
                ),
            ) from exc
