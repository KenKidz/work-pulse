"""HTTP integration tests for the notification inbox (T4, ADR 0016).

Every route from the pinned contract is exercised over the real repository and
Postgres: own-only listing (two users, one tenant), unread counts, idempotent
mark-read, read-all's "at call time" semantics, `404 notification_not_found` for
foreign and cross-tenant ids, `401` without a session, `422` for non-integer
pagination, and a link-less identity's empty inbox. The rule-7 proof asserts
that read-state mutations write no audit row and no outbox event.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from httpx import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.domains.notifications.models import NotificationKind
from app.domains.notifications.repository import NewNotification
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_notifications import NotificationRow
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.repositories.notifications import (
    PostgresNotificationRepository,
)
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    TENANT_B,
    ClientFactory,
    identity,
)

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
OTHER_EMPLOYEE = uuid7()
OTHER_USER = uuid7()
ITEM_KEYS = {"id", "tenant_id", "kind", "subject", "body", "created_at", "read_at"}


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def _seed(
    session: AsyncSession,
    *,
    event_id: str,
    tenant_id: UUID = TENANT_A,
    recipient_employee_id: UUID = ACTOR,
    subject: str = "Leave request from Ada Lovelace",
    body: str = "Ada Lovelace requested annual leave (3 days).",
    created_at: datetime = NOW,
    read_at: datetime | None = None,
) -> UUID:
    """Insert one projection row directly — the worker is not in this hop."""
    repo = PostgresNotificationRepository(session)
    notification, created = await repo.get_or_create(
        NewNotification(
            id=uuid7(),
            tenant_id=tenant_id,
            event_id=event_id,
            event_type="leave.requested",
            kind=NotificationKind.LEAVE_REQUESTED,
            recipient_employee_id=recipient_employee_id,
            recipient_user_id=OTHER_USER,
            subject=subject,
            body=body,
            created_at=created_at,
        )
    )
    assert created is True
    if read_at is not None:
        assert await repo.mark_read(tenant_id, notification.id, read_at=read_at)
    await session.flush()
    return notification.id


async def _stored_read_at(session: AsyncSession, notification_id: UUID) -> Any:
    """Read `read_at` through a column query (no ORM identity-map staleness)."""
    stmt = select(NotificationRow.read_at).where(NotificationRow.id == notification_id)
    return (await session.execute(stmt)).scalar_one()


async def _stored_projection(session: AsyncSession, notification_id: UUID) -> tuple:
    """The projection fields the HTTP path must never rewrite (rule 3)."""
    stmt = select(
        NotificationRow.tenant_id,
        NotificationRow.event_id,
        NotificationRow.event_type,
        NotificationRow.kind,
        NotificationRow.recipient_employee_id,
        NotificationRow.recipient_user_id,
        NotificationRow.subject,
        NotificationRow.body,
        NotificationRow.created_at,
    ).where(NotificationRow.id == notification_id)
    return tuple((await session.execute(stmt)).one())


async def _counts(session: AsyncSession) -> tuple[int, int]:
    audits = (
        await session.execute(select(func.count()).select_from(AuditEntry))
    ).scalar_one()
    events = (
        await session.execute(select(func.count()).select_from(EventOutboxRow))
    ).scalar_one()
    return audits, events


async def test_list_is_own_only_newest_first_and_paginated(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC: each user gets only their own rows, newest first, with stored content."""
    oldest = await _seed(session, event_id="evt_inbox_0001")
    middle = await _seed(
        session, event_id="evt_inbox_0002", created_at=NOW + timedelta(minutes=1)
    )
    newest = await _seed(
        session,
        event_id="evt_inbox_0003",
        subject="Attendance late: Ada Lovelace",
        created_at=NOW + timedelta(minutes=2),
    )
    # Another person in the same tenant and the same person in another tenant
    # must never appear in this inbox.
    other_person = await _seed(
        session,
        event_id="evt_inbox_0004",
        recipient_employee_id=OTHER_EMPLOYEE,
        created_at=NOW + timedelta(minutes=3),
    )
    tenant_b_row = await _seed(
        session,
        event_id="evt_inbox_0005",
        tenant_id=TENANT_B,
        created_at=NOW + timedelta(minutes=4),
    )

    async with client_factory() as client:
        response = await client.get("/api/v1/notifications?page=1&page_size=2")

        assert response.status_code == 200
        body = response.json()
        assert body["page"] == {
            "page": 1,
            "page_size": 2,
            "total_items": 3,
            "total_pages": 2,
        }
        assert [item["id"] for item in body["items"]] == [
            str(newest),
            str(middle),
        ]
        assert set(body["items"][0]) == ITEM_KEYS
        assert body["items"][0]["kind"] == NotificationKind.LEAVE_REQUESTED.value
        assert body["items"][0]["subject"] == "Attendance late: Ada Lovelace"
        assert body["items"][0]["body"] == (
            "Ada Lovelace requested annual leave (3 days)."
        )
        assert body["items"][0]["read_at"] is None

        second_page = await client.get("/api/v1/notifications?page=2&page_size=2")
        assert [item["id"] for item in second_page.json()["items"]] == [str(oldest)]

    async with client_factory(
        identity(role=Role.EMPLOYEE, employee_id=OTHER_EMPLOYEE)
    ) as other_client:
        only_other = (
            await other_client.get("/api/v1/notifications?page=1&page_size=20")
        ).json()
        assert only_other["page"]["total_items"] == 1
        assert [item["id"] for item in only_other["items"]] == [str(other_person)]

    async with client_factory(identity(tenant=TENANT_B, employee_id=ACTOR)) as tenant_b:
        foreign = (await tenant_b.get("/api/v1/notifications")).json()
        # Only tenant B's own row — none of tenant A's three.
        assert [item["id"] for item in foreign["items"]] == [str(tenant_b_row)]
        assert foreign["page"]["total_items"] == 1


async def test_mark_read_is_idempotent_and_drops_the_count_exactly_once(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC: first mark sets `read_at`; the second keeps the original timestamp."""
    notification = await _seed(session, event_id="evt_inbox_0001")
    await _seed(session, event_id="evt_inbox_0002")

    async with client_factory() as client:
        count = await client.get("/api/v1/notifications/unread-count")
        assert count.status_code == 200
        assert count.json() == {"unread_count": 2}

        first = await client.post(f"/api/v1/notifications/{notification}/read")
        assert first.status_code == 204
        assert first.content == b""

        after_first = await client.get("/api/v1/notifications")
        first_read_at = {
            item["id"]: item["read_at"] for item in after_first.json()["items"]
        }[str(notification)]
        assert first_read_at is not None
        assert (await client.get("/api/v1/notifications/unread-count")).json() == {
            "unread_count": 1
        }

        second = await client.post(f"/api/v1/notifications/{notification}/read")
        assert second.status_code == 204
        after_second = await client.get("/api/v1/notifications")
        assert {item["id"]: item["read_at"] for item in after_second.json()["items"]}[
            str(notification)
        ] == first_read_at
        assert (await client.get("/api/v1/notifications/unread-count")).json() == {
            "unread_count": 1
        }

        unknown = await client.post(f"/api/v1/notifications/{uuid7()}/read")
        _assert_error(unknown, 404, "notification_not_found")
        assert (await client.get("/api/v1/notifications/unread-count")).json() == {
            "unread_count": 1
        }


async def test_mark_all_read_marks_only_the_rows_unread_at_call_time(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC: k unread → 0; earlier timestamps stay; a later row stays unread."""
    already_read_at = NOW - timedelta(days=1)
    already_read = await _seed(
        session,
        event_id="evt_inbox_0001",
        created_at=NOW - timedelta(minutes=3),
        read_at=already_read_at,
    )
    first_unread = await _seed(
        session, event_id="evt_inbox_0002", created_at=NOW - timedelta(minutes=2)
    )
    second_unread = await _seed(
        session, event_id="evt_inbox_0003", created_at=NOW - timedelta(minutes=1)
    )

    async with client_factory() as client:
        before = {
            item["id"]: item["read_at"]
            for item in (await client.get("/api/v1/notifications")).json()["items"]
        }
        assert before[str(already_read)] is not None

        response = await client.post("/api/v1/notifications/read-all")
        assert response.status_code == 204

        after = (await client.get("/api/v1/notifications")).json()["items"]
        by_id = {item["id"]: item["read_at"] for item in after}
        assert by_id[str(already_read)] == before[str(already_read)]
        assert by_id[str(first_unread)] is not None
        assert by_id[str(second_unread)] is not None
        assert (await client.get("/api/v1/notifications/unread-count")).json() == {
            "unread_count": 0
        }

        # A row created after the statement was not covered by it.
        late = await _seed(
            session, event_id="evt_inbox_0004", created_at=NOW + timedelta(minutes=1)
        )
        assert (await client.get("/api/v1/notifications/unread-count")).json() == {
            "unread_count": 1
        }
        late_item = next(
            item
            for item in (await client.get("/api/v1/notifications")).json()["items"]
            if item["id"] == str(late)
        )
        assert late_item["read_at"] is None

        # Second read-all is the idempotent no-op that clears the new row.
        again = await client.post("/api/v1/notifications/read-all")
        assert again.status_code == 204
        assert (await client.get("/api/v1/notifications/unread-count")).json() == {
            "unread_count": 0
        }


async def test_foreign_and_cross_tenant_ids_are_one_404_and_change_nothing(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC: another user's and another tenant's ids are `404`; rows stay unread."""
    own = await _seed(session, event_id="evt_inbox_0001")
    other_person = await _seed(
        session,
        event_id="evt_inbox_0002",
        recipient_employee_id=OTHER_EMPLOYEE,
        created_at=NOW + timedelta(minutes=1),
    )

    async with client_factory() as client:
        foreign_user = await client.post(f"/api/v1/notifications/{other_person}/read")
        _assert_error(foreign_user, 404, "notification_not_found")
        assert await _stored_read_at(session, other_person) is None

        # Read-all is recipient-scoped: it must not touch the other person.
        assert (await client.post("/api/v1/notifications/read-all")).status_code == 204
        assert await _stored_read_at(session, own) is not None
        assert await _stored_read_at(session, other_person) is None

    async with client_factory(identity(tenant=TENANT_B, employee_id=ACTOR)) as tenant_b:
        cross_tenant = await tenant_b.post(f"/api/v1/notifications/{own}/read")
        _assert_error(cross_tenant, 404, "notification_not_found")
        assert (await tenant_b.get("/api/v1/notifications")).json()["items"] == []
        assert (
            await tenant_b.post("/api/v1/notifications/read-all")
        ).status_code == 204

    # Tenant B's read-all was a no-op and A's own row kept its read timestamp.
    assert await _stored_read_at(session, own) is not None
    assert await _stored_read_at(session, other_person) is None


async def test_every_route_requires_a_session(client_factory: ClientFactory) -> None:
    """AC: no session → `401 unauthenticated` on all four routes."""
    async with client_factory(None) as client:
        responses = [
            await client.get("/api/v1/notifications"),
            await client.get("/api/v1/notifications/unread-count"),
            await client.post(f"/api/v1/notifications/{uuid7()}/read"),
            await client.post("/api/v1/notifications/read-all"),
        ]
    for response in responses:
        _assert_error(response, 401, "unauthenticated")


async def test_pagination_contract_422_clamps_integers_and_rejects_text(
    client_factory: ClientFactory,
) -> None:
    """AC: non-integer pagination is `422 validation_error`; integers clamp."""
    async with client_factory() as client:
        for query in ("page=x", "page_size=x"):
            response = await client.get(f"/api/v1/notifications?{query}")
            _assert_error(response, 422, "validation_error")

        clamped_low = (
            await client.get("/api/v1/notifications?page=0&page_size=0")
        ).json()
        assert clamped_low["page"]["page"] == 1
        assert clamped_low["page"]["page_size"] == 1

        clamped_high = (
            await client.get("/api/v1/notifications?page=1&page_size=501")
        ).json()
        assert clamped_high["page"]["page_size"] == 100


async def test_link_less_identity_has_an_empty_inbox(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC: an account without `employee_id` gets empty shapes and no writes."""
    notification = await _seed(session, event_id="evt_inbox_0001")

    async with client_factory(identity(employee_id=None)) as client:
        listing = await client.get("/api/v1/notifications")
        assert listing.status_code == 200
        assert listing.json() == {
            "items": [],
            "page": {
                "page": 1,
                "page_size": 20,
                "total_items": 0,
                "total_pages": 0,
            },
        }
        assert (await client.get("/api/v1/notifications/unread-count")).json() == {
            "unread_count": 0
        }
        assert (await client.post("/api/v1/notifications/read-all")).status_code == 204

        # Fail closed: an existing row is not addressable without a link.
        marked = await client.post(f"/api/v1/notifications/{notification}/read")
        _assert_error(marked, 404, "notification_not_found")
        assert await _stored_read_at(session, notification) is None


async def test_read_state_mutations_write_no_audit_entry_and_no_event(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """Rule 3/7: only `read_at` changes; no audit row and no outbox event."""
    notification = await _seed(session, event_id="evt_inbox_0001")
    await _seed(session, event_id="evt_inbox_0002")
    projection_before = await _stored_projection(session, notification)
    assert await _counts(session) == (0, 0)

    async with client_factory() as client:
        assert (
            await client.post(f"/api/v1/notifications/{notification}/read")
        ).status_code == 204
        assert (await client.post("/api/v1/notifications/read-all")).status_code == 204

    assert await _stored_read_at(session, notification) is not None
    assert await _stored_projection(session, notification) == projection_before
    assert await _counts(session) == (0, 0)
