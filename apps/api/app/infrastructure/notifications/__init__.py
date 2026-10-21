"""Provider adapters for the Notifications delivery capability (T4, ADR 0015).

Both provider SDKs are synchronous and every call crosses
`anyio.to_thread.run_sync`, so the consumer loop is never blocked
(`.ai/conventions.md`). Missing provider configuration raises
`NotConfiguredError` — the service maps it to `skipped(not_configured)` without
retrying — while provider failures are translated into a payload-free
`NotificationProviderError` before they leave the adapter.
"""

from app.infrastructure.notifications.resend_email import ResendEmailSender
from app.infrastructure.notifications.slack_web import SlackWebSender

__all__ = ["ResendEmailSender", "SlackWebSender"]
