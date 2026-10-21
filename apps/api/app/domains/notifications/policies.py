"""Access policies for the Notifications inbox (`.ai/security.md`).

Notifications are person-addressed (ADR 0015): every role may read and mark
**its own** inbox and nothing else. There is no team, HR or admin scope — a
future team-inbox capability would be a new spec, not a widening of this guard.

The guard lives here and is called by the domain service before a row is
returned or marked (AGENTS.md rule 12). Unknown, foreign-recipient and
cross-tenant ids all raise the same `404 notification_not_found`, so the API
never reveals whether a row exists (rule 1). Fail closed: an identity without
an `employee_id` link can never match an addressed row.
"""

from app.domains.notifications.exceptions import NotificationNotFoundError
from app.domains.notifications.models import Notification
from app.shared.identity import Identity


def ensure_own(identity: Identity, notification: Notification) -> None:
    """Allow only the row's addressed employee inside the identity's tenant.

    Raises `NotificationNotFoundError` (not `ForbiddenError`) for every other
    case — including a link-less account — because the response must not
    distinguish "not yours" from "does not exist".
    """
    if (
        identity.employee_id is not None
        and notification.tenant_id == identity.tenant_id
        and notification.recipient_employee_id == identity.employee_id
    ):
        return
    raise NotificationNotFoundError()
