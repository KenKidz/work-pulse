"""Pure server-side notification templates (ADR 0015 decision 6).

English in v1. A template may use exactly the trigger table's content fields —
display names, leave type, inclusive dates, `days`, `workday`, `minutes_late`
and the decider name — plus the recipient's own name. It must never render
`reason`, `decision_note`, salaries, addresses or any other free text
(`.ai/security.md`). The `NotificationContent` allowlist makes the forbidden
fields structurally unreachable; these helpers only format the allowed ones.

`decided_by` is a user id (ADR 0010) and its display name is resolved through
the directory by the consumer. When it does not resolve (a non-employee admin
account), the decider clause is omitted rather than printing an id (spec R3).
"""

from datetime import date

from app.domains.notifications.models import NotificationKind, RenderedMessage
from app.domains.notifications.schemas import NotificationContent, NotificationIntent

_LEAVE_LABELS: dict[str, str] = {
    "annual": "annual leave",
    "sick": "sick leave",
    "unpaid": "unpaid leave",
}


def render(
    intent: NotificationIntent,
    *,
    subject_name: str,
    recipient_name: str,
    actor_name: str | None = None,
) -> RenderedMessage:
    """Render the message of record for one intent.

    `subject_name` is the affected employee's display name (the person the
    trigger table names); `recipient_name` is the resolved account holder the
    message greets; `actor_name` is the decider's display name when resolvable.
    Raises `ValueError` only for a malformed intent (a mapping/render bug), never
    for hostile event content — the allowlist has already discarded it.
    """
    content = intent.content
    if intent.kind is NotificationKind.LEAVE_REQUESTED:
        leave = _leave_label(content.leave_type_code)
        window = _window(content)
        days = _required_int(content.days, "days")
        return RenderedMessage(
            subject=f"Leave request from {subject_name}",
            body=(
                f"Hello {recipient_name},\n\n"
                f"{subject_name} requested {leave} from {window} "
                f"({days} {_plural(days, 'day')})."
            ),
        )
    if intent.kind is NotificationKind.LEAVE_APPROVED:
        leave = _leave_label(content.leave_type_code)
        window = _window(content)
        days = _required_int(content.days, "days")
        return RenderedMessage(
            subject=f"Leave approved: {leave}",
            body=(
                f"Hello {recipient_name},\n\n"
                f"Your {leave} from {window} ({days} {_plural(days, 'day')}) "
                f"was approved{_decider_clause(actor_name)}."
            ),
        )
    if intent.kind is NotificationKind.LEAVE_REJECTED:
        leave = _leave_label(content.leave_type_code)
        window = _window(content)
        return RenderedMessage(
            subject=f"Leave rejected: {leave}",
            body=(
                f"Hello {recipient_name},\n\n"
                f"Your {leave} from {window} was rejected"
                f"{_decider_clause(actor_name)}."
            ),
        )
    if intent.kind is NotificationKind.LEAVE_WITHDRAWN:
        return RenderedMessage(
            subject="Leave request withdrawn",
            body=(
                f"Hello {recipient_name},\n\n"
                f"Your leave request from {_window(content)} was withdrawn."
            ),
        )
    # `NotificationKind` has exactly five values; the last is attendance lateness.
    workday = _required_date(content.workday, "workday")
    minutes = _required_int(content.minutes_late, "minutes_late")
    return RenderedMessage(
        subject=f"Late check-in: {subject_name}",
        body=(
            f"Hello {recipient_name},\n\n"
            f"{subject_name} checked in late on {workday.isoformat()}: "
            f"{minutes} {_plural(minutes, 'minute')} late."
        ),
    )


def _leave_label(code: str | None) -> str:
    """The human label for an allowlisted leave type; anything else is a bug."""
    if code not in _LEAVE_LABELS:
        raise ValueError("leave_type_code is required")
    return _LEAVE_LABELS[code]


def _window(content: NotificationContent) -> str:
    """Inclusive date range in ISO form (the contract's date fields)."""
    start = _required_date(content.start_date, "start_date")
    end = _required_date(content.end_date, "end_date")
    return f"{start.isoformat()} to {end.isoformat()}"


def _required_date(value: date | None, field: str) -> date:
    if value is None:
        raise ValueError(f"{field} is required")
    return value


def _required_int(value: int | None, field: str) -> int:
    if value is None:
        raise ValueError(f"{field} is required")
    return value


def _decider_clause(actor_name: str | None) -> str:
    """` by <name>` only when the actor resolved; never a raw user id."""
    return f" by {actor_name}" if actor_name else ""


def _plural(count: int, singular: str) -> str:
    """`day`/`days`, `minute`/`minutes` — no free-text pluralization engine."""
    return singular if count == 1 else f"{singular}s"
