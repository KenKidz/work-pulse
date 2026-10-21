"""Domain values for Notifications (pinned by ADR 0015, ADR 0016).

Notifications are a pure projection of `workpulse.events`: one `Notification`
per `(event_id, recipient employee)` and one `NotificationDelivery` per
channel. Both are immutable values — the Postgres adapter translates rows into
them and every delivery transition is written back through the repository,
never mutated in place.

`Notification.read_at` is the one user-owned overlay on the projection (ADR
0016): the inbox HTTP path may set it, the worker never sees it, and a rebuild
from the event stream may drop it. It is not event-derived data.

`DeliveryStatus` is the pinned state machine
(`pending → sent | skipped | failed`), all three targets terminal. A skipped
delivery carries its stable reason in `last_error` (`not_configured`); a failed
one carries redacted infrastructure text only. `NotificationKind` is exactly
the five v1 trigger kinds — adding one is a new template + mapping entry, not a
schema change.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class NotificationKind(StrEnum):
    """The five v1 trigger kinds of the pinned trigger map (ADR 0015)."""

    LEAVE_REQUESTED = "leave_requested"
    LEAVE_APPROVED = "leave_approved"
    LEAVE_REJECTED = "leave_rejected"
    LEAVE_WITHDRAWN = "leave_withdrawn"
    ATTENDANCE_LATE = "attendance_late"


class DeliveryChannel(StrEnum):
    """Independent deliveries of the same notification (both fire in v1)."""

    EMAIL = "email"
    SLACK = "slack"


class DeliveryStatus(StrEnum):
    """Pinned delivery state machine; every non-`pending` state is terminal."""

    PENDING = "pending"
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"


TERMINAL_DELIVERY_STATUSES = frozenset(
    {DeliveryStatus.SENT, DeliveryStatus.SKIPPED, DeliveryStatus.FAILED}
)

SKIP_NOT_CONFIGURED = "not_configured"
"""`last_error` value for a skipped delivery whose channel has no credentials.

A skip is not a provider error (no send was attempted), but the delivery table
has no separate reason column (ADR 0015); the stable code is stored here so
dev inspection can tell "unconfigured" from "failed".
"""


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    """The server-side template output — the message of record (PO decision 11).

    Rendered at consume time from allowlisted content fields only; stored on
    the notification row. Personal names are the one PII that intentionally
    persists here (ADR 0015 decision 6); free text such as `reason` or
    `decision_note` never reaches it.
    """

    subject: str
    body: str


@dataclass(frozen=True, slots=True)
class Notification:
    """One delivery intent addressed to a person, deduplicated on the event.

    `read_at` is the user-owned overlay on the projection (ADR 0016); `None`
    means unread. The worker never writes it and a rebuild may drop it.
    """

    id: UUID
    tenant_id: UUID
    event_id: str
    event_type: str
    kind: NotificationKind
    recipient_employee_id: UUID
    recipient_user_id: UUID
    subject: str
    body: str
    created_at: datetime
    read_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NotificationDelivery:
    """One channel's attempt history for a notification."""

    id: UUID
    tenant_id: UUID
    notification_id: UUID
    channel: DeliveryChannel
    status: DeliveryStatus
    attempts: int
    last_error: str | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """The final state of one channel after `NotificationService.handle`."""

    channel: DeliveryChannel
    status: DeliveryStatus
    attempts: int
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class DeliverySummary:
    """Terminal summary returned to the consumer for its commit decision.

    `terminal` is the commit gate: the consumer advances the offset only when
    every delivery reached `sent`, `skipped` or `failed` (ADR 0015 decision 3).
    Store errors raise instead of producing a summary, so a non-terminal
    summary never authorizes a commit.
    """

    notification_id: UUID
    created: bool
    deliveries: tuple[DeliveryOutcome, ...]

    @property
    def terminal(self) -> bool:
        """Whether every delivery is in a terminal state (safe to commit)."""
        return all(
            delivery.status in TERMINAL_DELIVERY_STATUSES
            for delivery in self.deliveries
        )
