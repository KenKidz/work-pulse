"""Notification inbox routes: the four routes pinned in
`.ai/features/notification-inbox.md`.

Thin by rule: identity → service; `now` is taken at this edge only (`_now()` is
the single wall-clock read, every service method takes it as its argument).
Every route is own-only for every role — the service scopes by
`identity.employee_id` and the domain policy answers one `404
notification_not_found` for unknown, foreign and cross-tenant ids, so the API
never leaks existence. operationIds are pinned and drive the orval-generated
web hooks.

The literal paths (`/notifications/unread-count`, `/notifications/read-all`)
are declared before the `/{id}/read` param route so they can never be parsed as
notification ids.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter

from app.dependencies import IdentityDep, NotificationInboxServiceDep
from app.domains.notifications.schemas import (
    NotificationListPage,
    NotificationUnreadCount,
)
from app.shared.pagination import PageParamsDep

router = APIRouter()


def _now() -> datetime:
    """The single place wall-clock time is read (time is a parameter below)."""
    return datetime.now(UTC)


@router.get(
    "/notifications",
    response_model=NotificationListPage,
    operation_id="listNotifications",
)
async def list_notifications(
    identity: IdentityDep,
    service: NotificationInboxServiceDep,
    page: PageParamsDep,
) -> NotificationListPage:
    """The caller's own inbox, newest first, with the stored message of record."""
    return await service.list(identity, page)


@router.get(
    "/notifications/unread-count",
    response_model=NotificationUnreadCount,
    operation_id="getNotificationUnreadCount",
)
async def get_notification_unread_count(
    identity: IdentityDep,
    service: NotificationInboxServiceDep,
) -> NotificationUnreadCount:
    """The caller's unread total — the header badge's only input."""
    return NotificationUnreadCount(unread_count=await service.unread_count(identity))


@router.post(
    "/notifications/read-all",
    status_code=204,
    operation_id="markAllNotificationsRead",
)
async def mark_all_notifications_read(
    identity: IdentityDep,
    service: NotificationInboxServiceDep,
) -> None:
    """Mark the caller's currently-unread rows; idempotent and never partial."""
    await service.mark_all_read(identity, _now())


@router.post(
    "/notifications/{id}/read",
    status_code=204,
    operation_id="markNotificationRead",
)
async def mark_notification_read(
    id: UUID,
    identity: IdentityDep,
    service: NotificationInboxServiceDep,
) -> None:
    """Mark one own notification read; a repeated call keeps the timestamp."""
    await service.mark_read(identity, id, _now())
