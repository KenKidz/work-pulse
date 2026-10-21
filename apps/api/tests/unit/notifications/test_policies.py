"""Unit tests for the Notifications own-only inbox policy (rule 1)."""

from uuid import UUID

import pytest

from app.domains.notifications.exceptions import NotificationNotFoundError
from app.domains.notifications.models import Notification
from app.domains.notifications.policies import ensure_own
from app.shared.identity import Role
from tests.unit.notifications.conftest import (
    EMPLOYEE,
    NOW,
    OTHER,
    TENANT_A,
    TENANT_B,
    identity,
)
from tests.unit.notifications.fakes import (
    FakeNotificationRepository,
    seed_notification,
)


def _row(
    *,
    tenant_id: UUID = TENANT_A,
    recipient_employee_id: UUID = EMPLOYEE,
) -> Notification:
    repo = FakeNotificationRepository()
    return seed_notification(
        repo,
        tenant_id=tenant_id,
        recipient_employee_id=recipient_employee_id,
        created_at=NOW,
    )


@pytest.mark.parametrize("role", list(Role))
def test_every_role_may_access_its_own_notification(role: Role) -> None:
    """There is no role gate: all six roles may read their own inbox."""
    ensure_own(identity(role=role), _row())


def test_foreign_recipient_is_not_found() -> None:
    """Another person's row is a `404`, not a revealing `403`."""
    with pytest.raises(NotificationNotFoundError) as caught:
        ensure_own(identity(employee_id=OTHER), _row())

    assert caught.value.status_code == 404
    assert caught.value.code == "notification_not_found"


def test_cross_tenant_row_is_not_found() -> None:
    """A matching employee id in another tenant is still not the caller's row."""
    with pytest.raises(NotificationNotFoundError):
        ensure_own(identity(tenant_id=TENANT_A), _row(tenant_id=TENANT_B))


def test_link_less_identity_never_passes() -> None:
    """Fail closed: no employee link means no own notification, even in-tenant."""
    with pytest.raises(NotificationNotFoundError):
        ensure_own(identity(employee_id=None), _row())
