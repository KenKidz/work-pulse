"""Worker ↔ domain seam DTOs and inbox read models for Notifications.

The delivery seam DTOs (`NotificationContent`, `NotificationIntent`,
`SubjectFacts`, `Recipient`) are internal value types. The worker's pure mapper
builds a `NotificationIntent`; the read-only recipient directory resolves
`SubjectFacts` and `Recipient`; templates render a `RenderedMessage`. The
service persists the rendered message and never resolves reference data
itself.

The inbox read models (`NotificationItem`, `NotificationListPage`,
`NotificationUnreadCount`) are the pinned HTTP wire contract
(`.ai/features/notification-inbox.md`, T1). `NotificationItem` is an explicit
projection: it exposes exactly the stored message-of-record fields and
`read_at`, never `event_*`, `recipient_*` or any delivery field (rule 6).

`NotificationContent` is a deliberately **closed** allowlist: it can carry
only the trigger map's content fields (identifiers, leave type, inclusive
dates, counts). It has no `reason`, `decision_note`, address, salary or free
text field, so sensitive event data cannot reach a template by accident.
Display names are resolved through the directory at render time, never stored
on the intent.
"""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel

from app.domains.notifications.models import Notification, NotificationKind
from app.shared.pagination import Page


@dataclass(frozen=True, slots=True)
class NotificationContent:
    """Allowlisted rendering inputs of one trigger (no PII values)."""

    # The affected person (requester / late employee); the manager-addressed
    # kinds use their display name, the requester-addressed kinds greet the
    # recipient instead. Always an identifier, never a name.
    subject_employee_id: UUID
    leave_type_code: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = None
    workday: date | None = None
    minutes_late: int | None = None
    # Actor *user* id (ADR 0010); templates render the directory display name
    # only when it resolves (spec R3), never the raw id.
    decided_by: UUID | None = None


@dataclass(frozen=True, slots=True)
class NotificationIntent:
    """One mapped trigger: who to address, and the content its template may use."""

    kind: NotificationKind
    tenant_id: UUID
    event_id: str
    event_type: str
    recipient_employee_id: UUID
    content: NotificationContent


@dataclass(frozen=True, slots=True)
class SubjectFacts:
    """Read-only facts about the affected employee (directory port result)."""

    display_name: str
    manager_id: UUID | None


@dataclass(frozen=True, slots=True)
class Recipient:
    """The resolved account a notification is delivered to.

    `employee_id` is the person addressed (the dedupe key's counterpart);
    `user_id` is the linked account snapshot stored on the notification row;
    `account_address` is `users.email` — deliberately not `employees.email`
    (ADR 0015 decision 6).
    """

    employee_id: UUID
    user_id: UUID
    display_name: str
    account_address: str


class NotificationItem(BaseModel):
    """One inbox row — the stored message of record plus its read state.

    Built by explicit mapping from `Notification`; the worker's projection
    fields `event_id`/`event_type` and the row's `recipient_*` snapshot are
    deliberately absent, as is every delivery field (rule 6).
    """

    id: UUID
    tenant_id: UUID
    kind: NotificationKind
    subject: str
    body: str
    created_at: datetime
    read_at: datetime | None = None

    @classmethod
    def from_notification(cls, notification: Notification) -> "NotificationItem":
        """Project a stored notification row onto the pinned wire shape."""
        return cls(
            id=notification.id,
            tenant_id=notification.tenant_id,
            kind=notification.kind,
            subject=notification.subject,
            body=notification.body,
            created_at=notification.created_at,
            read_at=notification.read_at,
        )


class NotificationListPage(BaseModel):
    """Paged inbox response, newest first (house `Page` envelope)."""

    items: list[NotificationItem]
    page: Page


class NotificationUnreadCount(BaseModel):
    """The caller's own unread total — the header badge's only input."""

    unread_count: int
