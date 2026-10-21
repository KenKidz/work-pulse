"""Unit tests for `NotificationInboxService` — own-only inbox (T3).

Pure: in-memory repository, injected `NOW`, no sleeps and no wall clock
(`.ai/testing.md`). These tests pin the read-side rules from
`.ai/features/notification-inbox.md`: own-only listing/counts, idempotent
mark-read, mark-all's "currently unread" set semantics, and the fail-closed
handling of foreign, cross-tenant, unknown and link-less identities.
"""

from datetime import timedelta

import pytest
from uuid_utils.compat import uuid7

from app.domains.notifications.exceptions import NotificationNotFoundError
from app.domains.notifications.models import NotificationKind
from app.domains.notifications.service import NotificationInboxService
from app.shared.pagination import PageParams
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


async def test_list_returns_own_rows_newest_first_with_stored_content(
    repo: FakeNotificationRepository,
    service: NotificationInboxService,
) -> None:
    """AC: own rows only, newest first, paginated, stored body/kind/read_at."""
    oldest = seed_notification(
        repo,
        tenant_id=TENANT_A,
        recipient_employee_id=EMPLOYEE,
        created_at=NOW,
        subject="Leave request from Ada Lovelace",
        body="Ada Lovelace requested annual leave (3 days).",
    )
    newest = seed_notification(
        repo,
        tenant_id=TENANT_A,
        recipient_employee_id=EMPLOYEE,
        created_at=NOW + timedelta(minutes=5),
        kind=NotificationKind.LEAVE_APPROVED,
        subject="Your leave request was approved",
        body="Ada Lovelace approved your leave request.",
        read_at=NOW + timedelta(minutes=6),
    )
    # Another person in the same tenant and the same person in another tenant
    # must never appear in this inbox.
    seed_notification(
        repo,
        tenant_id=TENANT_A,
        recipient_employee_id=OTHER,
        created_at=NOW + timedelta(minutes=10),
    )
    seed_notification(
        repo,
        tenant_id=TENANT_B,
        recipient_employee_id=EMPLOYEE,
        created_at=NOW + timedelta(minutes=15),
    )

    first = await service.list(identity(), PageParams(page=1, page_size=1))

    assert [item.id for item in first.items] == [newest.id]
    assert first.page.page == 1
    assert first.page.page_size == 1
    assert first.page.total_items == 2
    assert first.page.total_pages == 2
    item = first.items[0]
    assert item.tenant_id == TENANT_A
    assert item.kind is NotificationKind.LEAVE_APPROVED
    assert item.subject == newest.subject
    assert item.body == newest.body
    assert item.created_at == newest.created_at
    assert item.read_at == NOW + timedelta(minutes=6)
    # The pinned wire shape exposes no event, recipient or delivery data.
    assert set(item.model_dump()) == {
        "id",
        "tenant_id",
        "kind",
        "subject",
        "body",
        "created_at",
        "read_at",
    }

    second = await service.list(identity(), PageParams(page=2, page_size=1))
    assert [item.id for item in second.items] == [oldest.id]
    assert second.items[0].read_at is None


async def test_list_is_empty_for_a_link_less_identity(
    repo: FakeNotificationRepository,
    service: NotificationInboxService,
) -> None:
    """A user without an employee link has an empty inbox, without a query."""
    seed_notification(
        repo,
        tenant_id=TENANT_A,
        recipient_employee_id=EMPLOYEE,
        created_at=NOW,
    )

    page = await service.list(identity(employee_id=None), PageParams())

    assert page.items == []
    assert page.page.total_items == 0
    assert page.page.total_pages == 0
    assert "list_for_recipient" not in repo.calls


async def test_unread_count_scopes_to_the_callers_inbox(
    repo: FakeNotificationRepository,
    service: NotificationInboxService,
) -> None:
    """Count is tenant- and recipient-scoped; read rows do not count."""
    seed_notification(
        repo, tenant_id=TENANT_A, recipient_employee_id=EMPLOYEE, created_at=NOW
    )
    seed_notification(
        repo,
        tenant_id=TENANT_A,
        recipient_employee_id=EMPLOYEE,
        created_at=NOW + timedelta(minutes=1),
    )
    seed_notification(
        repo,
        tenant_id=TENANT_A,
        recipient_employee_id=EMPLOYEE,
        created_at=NOW + timedelta(minutes=2),
        read_at=NOW,
    )
    seed_notification(
        repo,
        tenant_id=TENANT_A,
        recipient_employee_id=OTHER,
        created_at=NOW + timedelta(minutes=3),
    )
    seed_notification(
        repo,
        tenant_id=TENANT_B,
        recipient_employee_id=EMPLOYEE,
        created_at=NOW + timedelta(minutes=4),
    )

    assert await service.unread_count(identity()) == 2
    assert await service.unread_count(identity(employee_id=None)) == 0


async def test_mark_read_is_idempotent_and_drops_the_count_once(
    repo: FakeNotificationRepository,
    service: NotificationInboxService,
) -> None:
    """AC: the first call sets `read_at`; the second keeps the timestamp."""
    row = seed_notification(
        repo, tenant_id=TENANT_A, recipient_employee_id=EMPLOYEE, created_at=NOW
    )
    assert await service.unread_count(identity()) == 1

    await service.mark_read(identity(), row.id, NOW)
    stored = repo.notifications[(TENANT_A, row.id)]
    assert stored.read_at == NOW
    assert await service.unread_count(identity()) == 0

    later = NOW + timedelta(hours=1)
    await service.mark_read(identity(), row.id, later)
    reread = repo.notifications[(TENANT_A, row.id)]
    assert reread.read_at == NOW  # the original timestamp wins
    assert await service.unread_count(identity()) == 0


async def test_mark_read_hides_a_foreign_recipient_without_changing_state(
    repo: FakeNotificationRepository,
    service: NotificationInboxService,
) -> None:
    """AC: another person's id is `404` and their row stays unread."""
    foreign = seed_notification(
        repo, tenant_id=TENANT_A, recipient_employee_id=OTHER, created_at=NOW
    )

    with pytest.raises(NotificationNotFoundError) as caught:
        await service.mark_read(identity(), foreign.id, NOW)

    assert caught.value.status_code == 404
    assert caught.value.code == "notification_not_found"
    assert caught.value.message == "Notification not found"
    assert repo.notifications[(TENANT_A, foreign.id)].read_at is None
    assert "mark_read" not in repo.calls


async def test_mark_read_hides_unknown_and_cross_tenant_ids(
    repo: FakeNotificationRepository,
    service: NotificationInboxService,
) -> None:
    """AC: unknown and another tenant's ids are the same `404` with no write."""
    cross_tenant = seed_notification(
        repo, tenant_id=TENANT_B, recipient_employee_id=EMPLOYEE, created_at=NOW
    )

    for notification_id in (uuid7(), cross_tenant.id):
        with pytest.raises(NotificationNotFoundError):
            await service.mark_read(identity(), notification_id, NOW)

    assert repo.notifications[(TENANT_B, cross_tenant.id)].read_at is None
    assert "mark_read" not in repo.calls


async def test_link_less_identity_can_never_mark_a_row(
    repo: FakeNotificationRepository,
    service: NotificationInboxService,
) -> None:
    """Fail closed: no employee link means no own notification, even in-tenant."""
    row = seed_notification(
        repo, tenant_id=TENANT_A, recipient_employee_id=EMPLOYEE, created_at=NOW
    )

    with pytest.raises(NotificationNotFoundError):
        await service.mark_read(identity(employee_id=None), row.id, NOW)

    assert repo.notifications[(TENANT_A, row.id)].read_at is None
    assert "mark_read" not in repo.calls


async def test_mark_all_read_marks_only_the_currently_unread_rows(
    repo: FakeNotificationRepository,
    service: NotificationInboxService,
) -> None:
    """AC: k unread → k writes; read timestamps kept; a later row stays unread."""
    already_read_at = NOW - timedelta(days=1)
    already_read = seed_notification(
        repo,
        tenant_id=TENANT_A,
        recipient_employee_id=EMPLOYEE,
        created_at=NOW - timedelta(minutes=10),
        read_at=already_read_at,
    )
    first_unread = seed_notification(
        repo, tenant_id=TENANT_A, recipient_employee_id=EMPLOYEE, created_at=NOW
    )
    second_unread = seed_notification(
        repo,
        tenant_id=TENANT_A,
        recipient_employee_id=EMPLOYEE,
        created_at=NOW + timedelta(minutes=1),
    )
    other_person = seed_notification(
        repo,
        tenant_id=TENANT_A,
        recipient_employee_id=OTHER,
        created_at=NOW + timedelta(minutes=2),
    )

    changed = await service.mark_all_read(identity(), NOW)

    assert changed == 2
    assert repo.notifications[(TENANT_A, already_read.id)].read_at == already_read_at
    assert repo.notifications[(TENANT_A, first_unread.id)].read_at == NOW
    assert repo.notifications[(TENANT_A, second_unread.id)].read_at == NOW
    assert repo.notifications[(TENANT_A, other_person.id)].read_at is None
    assert await service.unread_count(identity()) == 0

    # A row created after the statement is not covered by it.
    late = seed_notification(
        repo,
        tenant_id=TENANT_A,
        recipient_employee_id=EMPLOYEE,
        created_at=NOW + timedelta(minutes=5),
    )
    assert late.read_at is None
    assert await service.unread_count(identity()) == 1


async def test_mark_all_read_is_a_no_op_without_an_employee_link(
    repo: FakeNotificationRepository,
    service: NotificationInboxService,
) -> None:
    """AC: a link-less identity's read-all returns 0 and writes nothing."""
    row = seed_notification(
        repo, tenant_id=TENANT_A, recipient_employee_id=EMPLOYEE, created_at=NOW
    )

    assert await service.mark_all_read(identity(employee_id=None), NOW) == 0

    assert repo.notifications[(TENANT_A, row.id)].read_at is None
    assert "mark_all_read" not in repo.calls
