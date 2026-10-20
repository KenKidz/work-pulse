"""Worker ↔ domain seam DTOs for Notifications (T2).

These are internal value types, not a wire contract: there is no route and
`make gen-api` stays diff-free. The worker's pure mapper builds a
`NotificationIntent`; the read-only recipient directory resolves
`SubjectFacts` and `Recipient`; templates render a `RenderedMessage`. The
service persists the rendered message and never resolves reference data
itself.

`NotificationContent` is a deliberately **closed** allowlist: it can carry
only the trigger map's content fields (identifiers, leave type, inclusive
dates, counts). It has no `reason`, `decision_note`, address, salary or free
text field, so sensitive event data cannot reach a template by accident.
Display names are resolved through the directory at render time, never stored
on the intent.
"""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.domains.notifications.models import NotificationKind


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
