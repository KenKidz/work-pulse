"""Notifications domain errors.

The worker has no HTTP surface, so these are not request errors: the family
exists for stable codes and one port-level signal. `NotConfiguredError` is how
a channel adapter says "no credentials/channel configured" — the service maps
it to `skipped(not_configured)` without retrying and without failing the
message (ADR 0015 decision 2). Messages are static and never echo rendered
content or recipient data (`.ai/security.md`).
"""

from app.shared.errors import WorkPulseError


class NotificationError(WorkPulseError):
    """Base class for expected notification errors (no HTTP surface in v1)."""

    status_code = 500
    code = "notification_error"


class NotConfiguredError(NotificationError):
    """The channel has no credentials or target configured (a terminal skip).

    Raised by `EmailSender`/`SlackSender` adapters, caught by the service. It
    must never be retried: retrying cannot create configuration, and the other
    channel still proceeds.
    """

    status_code = 503
    code = "notification_channel_not_configured"

    def __init__(self, message: str = "Notification channel is not configured") -> None:
        super().__init__(message)


class NotificationProviderError(NotificationError):
    """A channel provider rejected or failed the send (retryable infrastructure).

    Adapters raise it with **payload-free** message text: provider error type
    plus redacted infrastructure detail, never the rendered message, recipient
    address, display name or credential (ADR 0012's `last_error` discipline).
    The service still redacts as defence in depth before storing `last_error`.
    """

    status_code = 502
    code = "notification_provider_error"
