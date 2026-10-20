"""Ports the notification delivery path depends on (I/O boundaries).

The domain owns these structural protocols; adapters live outside it. The
recipient directory is read-only reference lookup (events carry no PII, so
addresses and display names cannot come from the stream — ADR 0015 decision
1/8): it is never a projection input and never another context's write path.
Sender ports raise on provider failure; `NotConfiguredError` is the one
distinguishable signal the service treats as a terminal skip.

Exactly two channels exist in v1. Email additionally carries an idempotency
key equal to the delivery id, so the provider collapses a crash-window retry
(ADR 0015 decision 7); Slack has no equivalent.
"""

from typing import Protocol
from uuid import UUID

from app.domains.notifications.models import RenderedMessage
from app.domains.notifications.schemas import Recipient, SubjectFacts


class RecipientDirectory(Protocol):
    """Read-only reference lookup over identity/employees (per tenant).

    Implementations may read the `employees` and `users` tables directly (the
    documented cross-domain seam, T3), but must never mutate them. `None` means
    unresolvable — no manager, no linked user account — and the consumer skips
    the message with ids-only logging.
    """

    async def subject_facts(
        self, tenant_id: UUID, employee_id: UUID
    ) -> SubjectFacts | None:
        """Display name and direct manager of the affected employee."""
        ...

    async def recipient(self, tenant_id: UUID, employee_id: UUID) -> Recipient | None:
        """The employee's linked user account, or `None` when unlinked."""
        ...

    async def actor_display_name(self, tenant_id: UUID, actor_id: UUID) -> str | None:
        """Display name of a deciding actor user (ADR 0010), when resolvable."""
        ...


class EmailSender(Protocol):
    """Sends one notification email through the configured provider."""

    async def send(
        self,
        *,
        recipient: Recipient,
        message: RenderedMessage,
        idempotency_key: UUID,
    ) -> None:
        """Deliver the message; raise on failure.

        `NotConfiguredError` when the provider identity is not configured.
        The idempotency key is the delivery id: a retry after a crash between
        provider accept and the `sent` write must collapse at the provider.
        """
        ...


class SlackSender(Protocol):
    """Posts one notification message to the tenant's configured channel."""

    async def send(self, *, recipient: Recipient, message: RenderedMessage) -> None:
        """Post the message; raise on failure.

        `NotConfiguredError` when the bot token or channel is not configured.
        Slack has no idempotency key — a crash window may double-post, an
        accepted v1 trade-off (ADR 0015 decision 7).
        """
        ...
