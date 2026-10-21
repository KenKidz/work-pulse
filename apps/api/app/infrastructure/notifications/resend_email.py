"""Resend email sender — the `EmailSender` port over the official SDK (T4).

ADR 0015 decisions 5/7: one env-configured Resend identity, an idempotency
key equal to the delivery id (a crash-window retry collapses at the provider),
and the recipient's **account** address (`users.email`, never
`employees.email`). The SDK is synchronous, so the call crosses
`anyio.to_thread.run_sync`.

The SDK reads its API key from the `resend.api_key` module global at request
time; the production client sets it from the injected setting. Tests inject a
fake SDK client, so providers are never called (`.ai/testing.md`). Missing key
or from-address raises `NotConfiguredError` before any SDK work; a provider
failure is translated to a payload-free `NotificationProviderError`.
"""

from functools import partial
from typing import Any, Protocol, cast
from uuid import UUID

import anyio
import resend

from app.domains.notifications.exceptions import NotConfiguredError
from app.domains.notifications.models import RenderedMessage
from app.domains.notifications.schemas import Recipient
from app.infrastructure.notifications.errors import provider_error


class ResendClient(Protocol):
    """The one SDK call the adapter makes; injectable for unit tests."""

    def send(
        self, params: dict[str, Any], options: dict[str, Any] | None = None
    ) -> object:
        """Send one email through Resend; raise on provider failure."""
        ...


class _ResendSdkClient:
    """Production client: the official `resend` SDK (`Emails.send`)."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def send(
        self, params: dict[str, Any], options: dict[str, Any] | None = None
    ) -> object:
        # `Emails.send` resolves the credential from this module global at
        # request time; there is no per-client key in the SDK's public API.
        resend.api_key = self._api_key
        return resend.Emails.send(cast(Any, params), cast(Any, options))


class ResendEmailSender:
    """`EmailSender` over Resend (`POST /emails`, text body)."""

    def __init__(
        self,
        *,
        api_key: str | None,
        from_address: str | None,
        client: ResendClient | None = None,
    ) -> None:
        self._api_key = api_key.strip() if api_key else None
        self._from_address = from_address.strip() if from_address else None
        self._client = client

    async def send(
        self,
        *,
        recipient: Recipient,
        message: RenderedMessage,
        idempotency_key: UUID,
    ) -> None:
        api_key = self._api_key
        from_address = self._from_address
        if not api_key or not from_address:
            # Terminal skip, never retried and never named in a log: the
            # service maps this to `skipped(not_configured)`.
            raise NotConfiguredError()
        client = self._client if self._client is not None else _ResendSdkClient(api_key)

        params = {
            "from": from_address,
            "to": [recipient.account_address],
            "subject": message.subject,
            "text": message.body,
        }
        options = {"idempotency_key": str(idempotency_key)}
        try:
            await anyio.to_thread.run_sync(partial(client.send, params, options))
        except Exception as exc:
            raise provider_error(
                exc,
                sensitive=(
                    api_key,
                    from_address,
                    recipient.account_address,
                    recipient.display_name,
                    message.subject,
                    message.body,
                ),
            ) from exc
