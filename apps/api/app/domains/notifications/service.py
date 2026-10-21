"""Use cases for Notifications — delivery (worker) and inbox (HTTP read side).

`NotificationService.handle` is the worker's single application operation. It
implements the pinned delivery semantics (ADR 0015 decision 2/3):

- **Dedupe** — `get_or_create` on `(event_id, recipient employee)`; a
  redelivered envelope returns the stored notification (content included) and
  creates nothing new.
- **Independent channels** — an `email` and a `slack` delivery row per
  notification. Each is driven separately: one failing or being unconfigured
  never stops the other.
- **Terminal states only** — `sent` (provider accepted, `sent_at` set),
  `skipped(not_configured)` (no provider call attempted, never retried), or
  `failed` after at most `max_attempts` provider errors. Only `pending` rows
  are attempted, so a redelivery never re-sends an already-`sent` delivery.
- **Message of record** — deliveries always send the notification row's stored
  `subject`/`body`, never a freshly rendered message supplied by a redelivery
  (ADR 0015 decision 6). A pending re-attempt and the row therefore agree on
  what was sent.
- **Durable boundaries** — the injected `commit` persists the notification and
  its pending delivery rows *before* the first provider call, and each terminal
  transition is committed immediately after it is written (ADR 0015 decision
  2/7). A crash after a provider accepts therefore leaves the same `pending`
  delivery id for the retry — which is also Resend's idempotency key.
- **Bounded retries** — exponential backoff (`base`, `2 × base`, …) through an
  injected sleeper, so unit tests assert the schedule without wall-clock waits.
  Backoff runs outside the store transaction because the intent is already
  committed.
- **PII-free errors** — `last_error` is the exception type plus redacted
  infrastructure text, capped at 300 characters. Rendered subject/body,
  recipient name and account address are scrubbed before storing; payload
  contents never reach the column (`.ai/security.md`).

Store (repository) or commit errors deliberately propagate: the consumer backs
off and leaves the offset uncommitted, preserving at-least-once toward the
store.

`NotificationInboxService` is the HTTP read side over the stored rows: own-only
listing and counts, idempotent mark-read and mark-all-read. Read state is
user-owned state layered on the projection (ADR 0016); these methods write only
`read_at`, wire no audit writer, no outbox and no commit, and emit no event
(rule 7 — the request session owns the transaction). `now` is caller-supplied
like every other domain mutation.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID

from uuid_utils.compat import uuid7

from app.domains.notifications.exceptions import (
    NotConfiguredError,
    NotificationNotFoundError,
)
from app.domains.notifications.models import (
    SKIP_NOT_CONFIGURED,
    DeliveryChannel,
    DeliveryOutcome,
    DeliveryStatus,
    DeliverySummary,
    Notification,
    NotificationDelivery,
    RenderedMessage,
)
from app.domains.notifications.policies import ensure_own
from app.domains.notifications.ports import EmailSender, SlackSender
from app.domains.notifications.repository import (
    NewDelivery,
    NewNotification,
    NotificationRepository,
)
from app.domains.notifications.schemas import (
    NotificationIntent,
    NotificationItem,
    NotificationListPage,
    Recipient,
)
from app.shared.identity import Identity
from app.shared.pagination import PageParams
from app.shared.redaction import redaction_needles, safe_error_text

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE_SECONDS = 0.5

# `last_error` discipline: the shared scrubber (ADR 0012's row rule) redacts
# every rendered/recipient string and caps the result. The backoff exponent is
# bounded so an unbounded `2 ** n` can never overflow mid-outage.
_MAX_BACKOFF_EXPONENT = 32


async def _default_sleep(seconds: float) -> None:
    """The production sleeper; tests inject a recording callable instead."""
    await asyncio.sleep(seconds)


class NotificationService:
    """Persist one notification and drive its channel deliveries to terminal."""

    def __init__(
        self,
        repo: NotificationRepository,
        email_sender: EmailSender,
        slack_sender: SlackSender,
        *,
        commit: Callable[[], Awaitable[None]],
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = _default_sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if backoff_base_seconds < 0:
            raise ValueError("backoff_base_seconds must be >= 0")
        self._repo = repo
        self._email_sender = email_sender
        self._slack_sender = slack_sender
        self._commit = commit
        self._max_attempts = max_attempts
        self._backoff_base_seconds = backoff_base_seconds
        self._sleep = sleep

    async def handle(
        self,
        intent: NotificationIntent,
        recipient: Recipient,
        message: RenderedMessage,
        now: datetime,
    ) -> DeliverySummary:
        """Store the notification and drive every non-terminal delivery.

        `now` is the handling instant supplied by the worker (the only
        wall-clock reader); it becomes the row's `created_at`, `updated_at` and
        `sent_at`. `commit` persists the dedupe intent and its pending delivery
        rows before the first provider call and every terminal transition
        immediately after it is written (ADR 0015 decision 2/7). A mismatched
        recipient is a programming error, not a runtime condition — the
        consumer resolves the recipient by the intent's id.
        """
        if recipient.employee_id != intent.recipient_employee_id:
            raise ValueError("recipient does not match the notification intent")

        notification, created = await self._repo.get_or_create(
            NewNotification(
                id=uuid7(),
                tenant_id=intent.tenant_id,
                event_id=intent.event_id,
                event_type=intent.event_type,
                kind=intent.kind,
                recipient_employee_id=intent.recipient_employee_id,
                recipient_user_id=recipient.user_id,
                subject=message.subject,
                body=message.body,
                created_at=now,
            )
        )
        deliveries = await self._deliveries(notification, now)

        # Persist the intent and every pending delivery before any provider
        # call: a crash after a provider accepts then leaves the same pending
        # delivery id (Resend's idempotency key) for the redelivery (R2).
        await self._commit()

        # The stored row is the message of record; a redelivery must not send a
        # freshly rendered variant that the row does not describe (ADR 0015
        # decision 6). On a first insert the two are identical.
        delivered = RenderedMessage(
            subject=notification.subject, body=notification.body
        )

        outcomes: list[DeliveryOutcome] = []
        for delivery in sorted(deliveries, key=lambda row: row.channel.value):
            if delivery.status is not DeliveryStatus.PENDING:
                # Terminal rows are never re-sent: a redelivery only re-attempts
                # what a crash left pending (ADR 0015 decision 2).
                outcomes.append(_outcome(delivery))
                continue
            outcome = await self._deliver(delivery, recipient, delivered, now=now)
            # Each channel is durable before the next one runs: a later crash
            # can never re-send a channel that already reached a terminal state.
            await self._commit()
            outcomes.append(outcome)
        return DeliverySummary(
            notification_id=notification.id,
            created=created,
            deliveries=tuple(outcomes),
        )

    # --- deliveries ----------------------------------------------------------

    async def _deliveries(
        self, notification: Notification, now: datetime
    ) -> list[NotificationDelivery]:
        """List the notification's deliveries, reconciling missing channels.

        A crash between the notification insert and the delivery inserts leaves
        a notification without all rows; the next handling adds them. The
        repository's insert is conflict-tolerant, so a concurrent worker
        racing the same channel is a no-op.
        """
        rows = list(
            await self._repo.list_deliveries(notification.tenant_id, notification.id)
        )
        known = {row.channel for row in rows}
        for channel in DeliveryChannel:
            if channel in known:
                continue
            rows.append(
                await self._repo.add_delivery(
                    NewDelivery(
                        id=uuid7(),
                        tenant_id=notification.tenant_id,
                        notification_id=notification.id,
                        channel=channel,
                        created_at=now,
                    )
                )
            )
        return rows

    async def _deliver(
        self,
        delivery: NotificationDelivery,
        recipient: Recipient,
        message: RenderedMessage,
        *,
        now: datetime,
    ) -> DeliveryOutcome:
        """Drive one `pending` delivery to `sent`, `skipped` or `failed`."""
        attempts = delivery.attempts
        last_error: str | None = None
        while attempts < self._max_attempts:
            try:
                await self._send(delivery, recipient, message)
            except NotConfiguredError:
                # No provider call was attempted; retrying cannot create
                # configuration. Terminal skip, other channel unaffected.
                updated = await self._record(
                    delivery,
                    status=DeliveryStatus.SKIPPED,
                    attempts=attempts,
                    last_error=SKIP_NOT_CONFIGURED,
                    sent_at=None,
                    now=now,
                )
                return _outcome(updated)
            except Exception as exc:
                attempts += 1
                last_error = _safe_error(exc, message, recipient)
                if attempts >= self._max_attempts:
                    break
                await self._sleep(self._backoff_seconds(attempts))
                continue

            attempts += 1
            updated = await self._record(
                delivery,
                status=DeliveryStatus.SENT,
                attempts=attempts,
                last_error=None,
                sent_at=now,
                now=now,
            )
            return _outcome(updated)

        updated = await self._record(
            delivery,
            status=DeliveryStatus.FAILED,
            attempts=attempts,
            last_error=last_error if last_error is not None else delivery.last_error,
            sent_at=None,
            now=now,
        )
        return _outcome(updated)

    async def _send(
        self,
        delivery: NotificationDelivery,
        recipient: Recipient,
        message: RenderedMessage,
    ) -> None:
        """Dispatch to the channel adapter; each channel keeps its own port."""
        if delivery.channel is DeliveryChannel.EMAIL:
            await self._email_sender.send(
                recipient=recipient,
                message=message,
                idempotency_key=delivery.id,
            )
        elif delivery.channel is DeliveryChannel.SLACK:
            await self._slack_sender.send(recipient=recipient, message=message)
        else:  # pragma: no cover — the enum has exactly two values
            raise ValueError(f"unsupported delivery channel: {delivery.channel}")

    async def _record(
        self,
        delivery: NotificationDelivery,
        *,
        status: DeliveryStatus,
        attempts: int,
        last_error: str | None,
        sent_at: datetime | None,
        now: datetime,
    ) -> NotificationDelivery:
        return await self._repo.set_delivery_status(
            delivery.tenant_id,
            delivery.id,
            status=status,
            attempts=attempts,
            last_error=last_error,
            sent_at=sent_at,
            updated_at=now,
        )

    def _backoff_seconds(self, attempts: int) -> float:
        """Doubling between attempts: base after #1, 2× after #2, … (bounded)."""
        exponent = min(attempts - 1, _MAX_BACKOFF_EXPONENT)
        return self._backoff_base_seconds * 2**exponent


class NotificationInboxService:
    """Read and mark the caller's own notification inbox (ADR 0016).

    Every operation is scoped to `identity.employee_id`; there is no capability
    to read another person's inbox, whatever the role (rule 1). A link-less
    identity has an empty inbox and its mutations are no-ops. Read state is
    user-owned UI state, so no audit entry, no event and no commit are wired
    here (rule 7) — the request session owns the transaction.
    """

    def __init__(self, repo: NotificationRepository) -> None:
        self._repo = repo

    async def list(self, identity: Identity, page: PageParams) -> NotificationListPage:
        """One page of the caller's inbox, newest first, with the stored body."""
        employee_id = identity.employee_id
        if employee_id is None:
            return NotificationListPage(items=[], page=page.make_page(0))
        rows, total = await self._repo.list_for_recipient(
            identity.tenant_id, employee_id, page
        )
        return NotificationListPage(
            items=[NotificationItem.from_notification(row) for row in rows],
            page=page.make_page(total),
        )

    async def unread_count(self, identity: Identity) -> int:
        """The caller's unread total (0 for an identity without a link)."""
        if identity.employee_id is None:
            return 0
        return await self._repo.count_unread(identity.tenant_id, identity.employee_id)

    async def mark_read(
        self, identity: Identity, notification_id: UUID, now: datetime
    ) -> None:
        """Mark one own notification read; a repeated call keeps `read_at`.

        The ownership guard runs before the write; the repository's conditional
        `WHERE read_at IS NULL` update supplies idempotence under races. An
        unknown, foreign or cross-tenant id is the same `404`.
        """
        notification = await self._repo.get(identity.tenant_id, notification_id)
        if notification is None:
            raise NotificationNotFoundError()
        ensure_own(identity, notification)
        await self._repo.mark_read(identity.tenant_id, notification_id, read_at=now)

    async def mark_all_read(self, identity: Identity, now: datetime) -> int:
        """Mark the caller's currently-unread rows; return how many changed.

        Already-read rows keep their timestamp and rows created after the
        statement stay unread (rule 4); a link-less identity is a no-op.
        """
        if identity.employee_id is None:
            return 0
        return await self._repo.mark_all_read(
            identity.tenant_id, identity.employee_id, read_at=now
        )


def _outcome(delivery: NotificationDelivery) -> DeliveryOutcome:
    """A delivery row's final state as the summary value."""
    return DeliveryOutcome(
        channel=delivery.channel,
        status=delivery.status,
        attempts=delivery.attempts,
        last_error=delivery.last_error,
    )


def _safe_error(
    exc: BaseException, message: RenderedMessage, recipient: Recipient
) -> str:
    """`Type: message` diagnostics for `last_error` — payload- and PII-free.

    The shared scrubber redacts the rendered message, its lines, the recipient's
    display name and the account address, raw or JSON-escaped, then caps the
    result (ADR 0012 row rule).
    """
    return safe_error_text(
        exc,
        redaction_needles(
            (
                message.subject,
                message.body,
                recipient.display_name,
                recipient.account_address,
            )
        ),
    )
