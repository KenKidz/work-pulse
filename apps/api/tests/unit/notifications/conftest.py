"""Shared fixtures for the Notifications inbox unit tests (no database).

`NOW` is the injected clock every test passes explicitly — the inbox service
never reads the wall clock (`.ai/testing.md`: no wall-time assertions).
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domains.notifications.service import NotificationInboxService
from app.shared.identity import Identity, Role
from tests.unit.notifications.fakes import FakeNotificationRepository

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
USER = UUID("00000000-0000-4000-8000-000000000031")  # the acting user id
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000002")  # the addressed person
OTHER = UUID("00000000-0000-4000-8000-000000000003")

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


def identity(
    tenant_id: UUID = TENANT_A,
    role: Role = Role.EMPLOYEE,
    *,
    employee_id: UUID | None = EMPLOYEE,
    actor_id: UUID = USER,
) -> Identity:
    """The acting user (`actor_id`) plus its optional employee link."""
    return Identity(
        tenant_id=tenant_id,
        actor_id=actor_id,
        role=role,
        employee_id=employee_id,
    )


@pytest.fixture
def repo() -> FakeNotificationRepository:
    return FakeNotificationRepository()


@pytest.fixture
def service(repo: FakeNotificationRepository) -> NotificationInboxService:
    return NotificationInboxService(repo)
