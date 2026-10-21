"""T6 real-broker proofs — the notification worker end to end (AC 1–9).

Every part except the providers is real: Kafka `kafka-test` (:9093) carries the
v1 envelopes, the actual `NotificationsConsumer` runs with its `latest` group,
and the real directory + repository write against the test PostgreSQL. Channel
adapters are recording fakes (providers are never hit, `.ai/testing.md`);
`RecordingSleeper` keeps retry proofs free of wall-clock waits.

Readiness is deterministic (ADR 0013 decision 4): the consumer's `started`
event is awaited before anything is produced, so a fresh unique group has
pinned the topic tip and no record from an earlier test can leak in. Rows are
read through separate committed sessions and awaited with a bounded poll — no
fixed "give the worker a second" sleeps.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import structlog
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domains.attendance.events import attendance_checked_in, attendance_late
from app.domains.employees.events import employee_updated
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.leave.events import (
    leave_approved,
    leave_rejected,
    leave_requested,
    leave_withdrawn,
)
from app.domains.notifications.exceptions import NotConfiguredError
from app.domains.notifications.models import (
    SKIP_NOT_CONFIGURED,
    DeliveryChannel,
    DeliveryStatus,
    NotificationKind,
    RenderedMessage,
)
from app.domains.notifications.ports import EmailSender, SlackSender
from app.domains.notifications.schemas import Recipient
from app.domains.notifications.service import NotificationService
from app.infrastructure.notifications.slack_web import SlackWebSender
from app.infrastructure.postgres.engine import get_session_factory
from app.infrastructure.postgres.models_notifications import (
    NotificationDeliveryRow,
    NotificationRow,
)
from app.infrastructure.postgres.recipient_directory import PostgresRecipientDirectory
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.postgres.repositories.identity import PostgresUserRepository
from app.infrastructure.postgres.repositories.notifications import (
    PostgresNotificationRepository,
)
from app.infrastructure.postgres.repositories.organizations import (
    PostgresDepartmentRepository,
    PostgresLocationRepository,
    PostgresPositionRepository,
)
from app.shared.identity import Role
from tests.conftest import TEST_KAFKA_BOOTSTRAP_SERVERS
from tests.integration.conftest import TENANT_A, TENANT_B
from tests.unit.notifications.fakes import (
    RecordingEmailSender,
    RecordingSlackSender,
    RecordingSleeper,
)
from workers.notifications.consumer import NotificationsConsumer, NotificationUnit

KAFKA_BOOTSTRAP = TEST_KAFKA_BOOTSTRAP_SERVERS
TOPIC = get_settings().kafka_events_topic
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
WORKDAY = date(2026, 10, 21)
HIRED_AT = date(2026, 1, 5)
SLACK_TOKEN = "xoxb-test-secret-token"
SLACK_CHANNEL = "C0123456789"
# Hostile free text planted in a payload; it must never reach a row or a log.
SENTINEL = "Family medical details — do-not-render"
TERMINAL_STATUSES = {
    DeliveryStatus.SENT.value,
    DeliveryStatus.SKIPPED.value,
    DeliveryStatus.FAILED.value,
}


# --- fixtures -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class World:
    """One tenant's manager/employee/account wiring for a delivery scenario."""

    tenant_id: UUID
    manager_id: UUID
    manager_name: str
    manager_user_id: UUID
    manager_account: str
    employee_id: UUID
    employee_name: str
    employee_user_id: UUID
    employee_account: str


async def _seed_refs(
    session: AsyncSession, tenant_id: UUID, suffix: str
) -> tuple[UUID, UUID, UUID]:
    department_id, position_id, location_id = uuid4(), uuid4(), uuid4()
    await PostgresDepartmentRepository(session).add(
        tenant_id, department_id, f"Dept {suffix}", None
    )
    await PostgresPositionRepository(session).add(
        tenant_id, position_id, f"Position {suffix}", None
    )
    await PostgresLocationRepository(session).add(
        tenant_id, location_id, f"Location {suffix}", None, "UTC"
    )
    return department_id, position_id, location_id


async def _add_employee(
    session: AsyncSession,
    tenant_id: UUID,
    refs: tuple[UUID, UUID, UUID],
    *,
    code: str,
    first_name: str,
    last_name: str,
    email: str,
    manager_id: UUID | None = None,
) -> UUID:
    department_id, position_id, location_id = refs
    employee = await PostgresEmployeeRepository(session).add(
        tenant_id,
        uuid4(),
        employee_code=code,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=None,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        manager_id=manager_id,
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        hired_at=HIRED_AT,
    )
    return employee.id


async def _add_user(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    employee_id: UUID | None,
    email: str,
) -> UUID:
    user = await PostgresUserRepository(session).add(
        tenant_id,
        uuid4(),
        email=email,
        role=Role.EMPLOYEE,
        employee_id=employee_id,
        password_hash="not-used-in-notification-tests",
    )
    return user.id


async def _seed_world(session: AsyncSession, tenant_id: UUID, suffix: str) -> World:
    """A manager with an account, and their employee with an account."""
    refs = await _seed_refs(session, tenant_id, suffix)
    manager_id = await _add_employee(
        session,
        tenant_id,
        refs,
        code=f"EMP-{suffix}-M",
        first_name="Mary",
        last_name="Manager",
        # The employment address deliberately differs from the account address:
        # delivery must use `users.email` (ADR 0015 decision 6).
        email=f"mary.manager.{suffix}@employment.test",
    )
    manager_user_id = await _add_user(
        session,
        tenant_id,
        employee_id=manager_id,
        email=f"mary.manager.{suffix}@accounts.test",
    )
    employee_id = await _add_employee(
        session,
        tenant_id,
        refs,
        code=f"EMP-{suffix}-E",
        first_name="Ada",
        last_name="Lovelace",
        email=f"ada.{suffix}@employment.test",
        manager_id=manager_id,
    )
    employee_user_id = await _add_user(
        session,
        tenant_id,
        employee_id=employee_id,
        email=f"ada.{suffix}@accounts.test",
    )
    await session.commit()
    return World(
        tenant_id=tenant_id,
        manager_id=manager_id,
        manager_name="Mary Manager",
        manager_user_id=manager_user_id,
        manager_account=f"mary.manager.{suffix}@accounts.test",
        employee_id=employee_id,
        employee_name="Ada Lovelace",
        employee_user_id=employee_user_id,
        employee_account=f"ada.{suffix}@accounts.test",
    )


async def _add_unmanaged_employee(
    session: AsyncSession, tenant_id: UUID, suffix: str
) -> UUID:
    """An employee with no `manager_id` (a committed skip)."""
    refs = await _seed_refs(session, tenant_id, suffix)
    employee_id = await _add_employee(
        session,
        tenant_id,
        refs,
        code=f"EMP-{suffix}-NM",
        first_name="Grace",
        last_name="Hopper",
        email=f"grace.{suffix}@employment.test",
    )
    await _add_user(
        session,
        tenant_id,
        employee_id=employee_id,
        email=f"grace.{suffix}@accounts.test",
    )
    await session.commit()
    return employee_id


async def _add_employee_with_unlinked_manager(
    session: AsyncSession, tenant_id: UUID, suffix: str
) -> UUID:
    """An employee whose manager has no linked user account (a committed skip)."""
    refs = await _seed_refs(session, tenant_id, suffix)
    manager_id = await _add_employee(
        session,
        tenant_id,
        refs,
        code=f"EMP-{suffix}-UM",
        first_name="No",
        last_name="Account",
        email=f"no.account.{suffix}@employment.test",
    )
    employee_id = await _add_employee(
        session,
        tenant_id,
        refs,
        code=f"EMP-{suffix}-UE",
        first_name="Alan",
        last_name="Turing",
        email=f"alan.{suffix}@employment.test",
        manager_id=manager_id,
    )
    await _add_user(
        session,
        tenant_id,
        employee_id=employee_id,
        email=f"alan.{suffix}@accounts.test",
    )
    await session.commit()
    return employee_id


# --- envelopes ----------------------------------------------------------------


def _requested(world: World, *, employee_id: UUID | None = None) -> dict[str, Any]:
    return leave_requested(
        tenant_id=world.tenant_id,
        actor_id=world.employee_user_id,
        occurred_at=NOW,
        leave_request_id=uuid4(),
        employee_id=employee_id if employee_id is not None else world.employee_id,
        leave_type_code="annual",
        start_date=WORKDAY,
        end_date=WORKDAY + timedelta(days=2),
        days=3,
    )


def _approved(world: World) -> dict[str, Any]:
    return leave_approved(
        tenant_id=world.tenant_id,
        actor_id=world.manager_user_id,
        occurred_at=NOW,
        leave_request_id=uuid4(),
        employee_id=world.employee_id,
        leave_type_code="annual",
        start_date=WORKDAY,
        end_date=WORKDAY + timedelta(days=2),
        days=3,
        decided_by=world.manager_user_id,
    )


def _rejected(world: World) -> dict[str, Any]:
    return leave_rejected(
        tenant_id=world.tenant_id,
        actor_id=world.manager_user_id,
        occurred_at=NOW,
        leave_request_id=uuid4(),
        employee_id=world.employee_id,
        leave_type_code="sick",
        start_date=WORKDAY,
        end_date=WORKDAY,
        decided_by=world.manager_user_id,
    )


def _withdrawn(world: World) -> dict[str, Any]:
    return leave_withdrawn(
        tenant_id=world.tenant_id,
        actor_id=world.employee_user_id,
        occurred_at=NOW,
        leave_request_id=uuid4(),
        employee_id=world.employee_id,
        start_date=WORKDAY,
        end_date=WORKDAY,
    )


def _late(world: World) -> dict[str, Any]:
    return attendance_late(
        tenant_id=world.tenant_id,
        actor_id=world.employee_user_id,
        occurred_at=NOW,
        attendance_id=uuid4(),
        employee_id=world.employee_id,
        workday=WORKDAY,
        shift_id=uuid4(),
        minutes_late=17,
    )


def _checked_in(world: World) -> dict[str, Any]:
    """A known non-trigger event (the realtime wall's input, not ours)."""
    return attendance_checked_in(
        tenant_id=world.tenant_id,
        actor_id=world.employee_user_id,
        occurred_at=NOW,
        attendance_id=uuid4(),
        employee_id=world.employee_id,
        workday=WORKDAY,
        work_mode="onsite",
        late=False,
        minutes_late=None,
    )


# --- the hop ------------------------------------------------------------------


class EchoingEmailSender:
    """A provider that echoes the rendered message back in its error.

    This is the hostile-provider shape `last_error` must survive: the service
    and adapter scrub the subject, body, display name and account address
    before the text is persisted (`.ai/security.md`).
    """

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.calls: list[tuple[Recipient, RenderedMessage, UUID]] = []
        self.error = error or RuntimeError("provider unavailable")

    async def send(
        self,
        *,
        recipient: Recipient,
        message: RenderedMessage,
        idempotency_key: UUID,
    ) -> None:
        self.calls.append((recipient, message, idempotency_key))
        raise RuntimeError(
            f"{type(self.error).__name__}: subject={message.subject} "
            f"body={message.body} to={recipient.account_address} "
            f"name={recipient.display_name}"
        )


class FakeSlackClient:
    """Records `chat.postMessage` calls; no network (`.ai/testing.md`)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def chat_postMessage(self, *, channel: str, text: str) -> object:
        self.calls.append((channel, text))
        return {"ok": True}


@dataclass(slots=True)
class Hop:
    """The running consumer and a direct topic producer."""

    producer: AIOKafkaProducer
    email: Any
    slack: Any

    async def publish(self, *values: Mapping[str, Any] | bytes) -> None:
        """Publish envelopes (or raw bytes) as the relay would."""
        for value in values:
            payload = (
                value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
            )
            await self.producer.send_and_wait(TOPIC, value=payload)


@asynccontextmanager
async def _hop(
    *,
    email_sender: EmailSender | None = None,
    slack_sender: SlackSender | None = None,
) -> AsyncIterator[Hop]:
    """Run the production consumer loop against the test broker and database.

    The consumer starts first and its `started` event is awaited — the fresh
    group has joined and pinned `latest` — before the caller publishes.
    """
    email: Any = email_sender if email_sender is not None else RecordingEmailSender()
    slack: Any = slack_sender if slack_sender is not None else RecordingSlackSender()
    session_factory = get_session_factory()

    @asynccontextmanager
    async def unit() -> AsyncIterator[NotificationUnit]:
        """One message's fresh session: directory reads + real delivery service."""
        async with session_factory() as session:
            yield NotificationUnit(
                directory=PostgresRecipientDirectory(session),
                service=NotificationService(
                    repo=PostgresNotificationRepository(session),
                    email_sender=email,
                    slack_sender=slack,
                    commit=session.commit,
                    max_attempts=3,
                    backoff_base_seconds=0.0,
                    sleep=RecordingSleeper(),
                ),
            )

    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
    await producer.start()
    source = AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=f"workpulse-notifications-test-{uuid4()}",
        auto_offset_reset="latest",
        enable_auto_commit=False,
    )
    await source.start()
    consumer = NotificationsConsumer(
        source,
        unit,
        poll_seconds=0.05,
        backoff_base=0.05,
        backoff_max=0.2,
        now=lambda: NOW,
    )
    consumer_task = asyncio.create_task(consumer.run())
    try:
        async with asyncio.timeout(30.0):
            await consumer.started.wait()
        yield Hop(producer=producer, email=email, slack=slack)
    finally:
        consumer.stop()
        await consumer_task
        await source.stop()
        await producer.stop()


# --- row helpers --------------------------------------------------------------


async def _read_notifications() -> list[NotificationRow]:
    async with get_session_factory()() as session:
        stmt = select(NotificationRow).order_by(
            NotificationRow.created_at.asc(), NotificationRow.id.asc()
        )
        return list((await session.execute(stmt)).scalars())


async def _read_deliveries(notification_id: UUID) -> list[NotificationDeliveryRow]:
    async with get_session_factory()() as session:
        stmt = (
            select(NotificationDeliveryRow)
            .where(NotificationDeliveryRow.notification_id == notification_id)
            .order_by(NotificationDeliveryRow.channel.asc())
        )
        return list((await session.execute(stmt)).scalars())


async def _eventually[T](
    probe: Callable[[], Awaitable[T | None]],
    *,
    description: str,
    timeout: float = 20.0,
) -> T:
    """Poll a committed-state probe under one bounded wait (no fixed sleeps)."""

    async def poll() -> T:
        while True:
            result = await probe()
            if result is not None:
                return result
            await asyncio.sleep(0.01)

    try:
        return await asyncio.wait_for(poll(), timeout)
    except TimeoutError:
        raise AssertionError(f"timed out waiting for {description}") from None


async def _wait_notifications(
    count: int, *, timeout: float = 20.0
) -> list[NotificationRow]:
    async def probe() -> list[NotificationRow] | None:
        rows = await _read_notifications()
        return rows if len(rows) >= count else None

    return await _eventually(
        probe,
        description=f"{count} notification row(s)",
        timeout=timeout,
    )


async def _wait_deliveries(
    notification_id: UUID, *, timeout: float = 20.0
) -> list[NotificationDeliveryRow]:
    """Wait for both channel rows and a terminal state (the commit gate)."""

    async def probe() -> list[NotificationDeliveryRow] | None:
        rows = await _read_deliveries(notification_id)
        if len(rows) < 2:
            return None
        return rows if all(row.status in TERMINAL_STATUSES for row in rows) else None

    return await _eventually(
        probe,
        description=f"terminal deliveries for notification {notification_id}",
        timeout=timeout,
    )


def _by_channel(
    rows: list[NotificationDeliveryRow],
) -> dict[DeliveryChannel, NotificationDeliveryRow]:
    return {DeliveryChannel(row.channel): row for row in rows}


# --- AC 1/2: the five trigger paths -------------------------------------------


async def test_leave_requested_notifies_the_manager_on_both_channels(
    session: AsyncSession,
) -> None:
    """AC1 — manager gets one notification, email to the account, Slack sent."""
    world = await _seed_world(session, TENANT_A, "A")

    async with _hop() as hop:
        await hop.publish(_requested(world))
        rows = await _wait_notifications(1)
        assert len(rows) == 1
        row = rows[0]
        deliveries = await _wait_deliveries(row.id)

    assert row.tenant_id == world.tenant_id
    assert row.event_type == "leave.requested"
    assert row.kind == NotificationKind.LEAVE_REQUESTED.value
    assert row.recipient_employee_id == world.manager_id
    assert row.recipient_user_id == world.manager_user_id
    assert world.employee_name in row.subject
    assert world.employee_name in row.body
    assert "annual leave" in row.body
    assert "2026-10-21 to 2026-10-23" in row.body
    assert "3 days" in row.body

    by_channel = _by_channel(deliveries)
    assert set(by_channel) == {DeliveryChannel.EMAIL, DeliveryChannel.SLACK}
    assert all(row.status == DeliveryStatus.SENT.value for row in deliveries)
    assert all(row.attempts == 1 for row in deliveries)
    assert all(row.sent_at == NOW for row in deliveries)

    assert isinstance(hop.email, RecordingEmailSender)
    assert isinstance(hop.slack, RecordingSlackSender)
    assert len(hop.email.sent) == 1
    assert len(hop.slack.sent) == 1
    assert hop.email.sent[0][0].account_address == world.manager_account
    assert hop.slack.sent[0][0].employee_id == world.manager_id


async def test_requester_leave_events_notify_the_requester(
    session: AsyncSession,
) -> None:
    """AC2 — approved/rejected/withdrawn address the requester, never the manager."""
    world = await _seed_world(session, TENANT_A, "A")

    async with _hop() as hop:
        await hop.publish(_approved(world), _rejected(world), _withdrawn(world))
        rows = await _wait_notifications(3)
        assert len(rows) == 3
        for row in rows:
            await _wait_deliveries(row.id)

    by_kind = {row.kind: row for row in rows}
    assert set(by_kind) == {
        NotificationKind.LEAVE_APPROVED.value,
        NotificationKind.LEAVE_REJECTED.value,
        NotificationKind.LEAVE_WITHDRAWN.value,
    }
    assert all(row.recipient_employee_id == world.employee_id for row in rows)
    assert all(row.recipient_user_id == world.employee_user_id for row in rows)
    assert "approved by Mary Manager" in by_kind["leave_approved"].body
    assert "rejected by Mary Manager" in by_kind["leave_rejected"].body
    assert by_kind["leave_withdrawn"].subject == "Leave request withdrawn"

    assert isinstance(hop.email, RecordingEmailSender)
    assert len(hop.email.sent) == 3
    assert {sent[0].account_address for sent in hop.email.sent} == {
        world.employee_account
    }


async def test_attendance_late_notifies_the_manager(session: AsyncSession) -> None:
    """AC2 — `attendance.late` addresses the late employee's manager."""
    world = await _seed_world(session, TENANT_A, "A")

    async with _hop() as hop:
        await hop.publish(_late(world))
        rows = await _wait_notifications(1)
        await _wait_deliveries(rows[0].id)

    row = rows[0]
    assert row.kind == NotificationKind.ATTENDANCE_LATE.value
    assert row.recipient_employee_id == world.manager_id
    assert row.recipient_user_id == world.manager_user_id
    assert world.employee_name in row.subject
    assert "2026-10-21" in row.body
    assert "17 minutes late" in row.body
    assert isinstance(hop.slack, RecordingSlackSender)
    assert hop.slack.sent[0][0].employee_id == world.manager_id


# --- AC 5/6: skips and bad messages -------------------------------------------


async def test_non_trigger_and_malformed_messages_commit_and_the_loop_lives(
    session: AsyncSession,
) -> None:
    """AC6 — malformed/unknown/known-non-trigger messages are skipped; a later
    trigger still lands (the offset advanced past every skip)."""
    world = await _seed_world(session, TENANT_A, "A")
    unknown = {
        "event_id": "evt_notifications_unknown",
        "event_type": f"mystery.{SENTINEL}",
        "version": 1,
        "tenant_id": str(TENANT_A),
        "occurred_at": NOW.isoformat(),
        "actor_id": str(world.employee_user_id),
        "data": {},
    }
    employee_changed = employee_updated(
        tenant_id=TENANT_A,
        actor_id=world.manager_user_id,
        occurred_at=NOW,
        employee_id=world.employee_id,
        changed_fields=["first_name"],
    )

    structlog.configure(cache_logger_on_first_use=False)
    with structlog.testing.capture_logs() as logs:
        async with _hop() as hop:
            await hop.publish(
                b"this is not json",
                _checked_in(world),
                employee_changed,
                unknown,
                _requested(world),
            )
            rows = await _wait_notifications(1)
            assert len(rows) == 1
            await _wait_deliveries(rows[0].id)

    assert [row.kind for row in rows] == [NotificationKind.LEAVE_REQUESTED.value]
    errors = {entry.get("error") for entry in logs}
    assert "malformed_envelope" in errors
    assert "unknown_event_type" in errors
    dumped = json.dumps(logs, default=str)
    assert "evt_notifications_unknown" in dumped  # ids-only identification
    assert SENTINEL not in dumped  # the hostile event type is never echoed


async def test_unresolvable_recipients_write_no_rows_and_the_offset_advances(
    session: AsyncSession,
) -> None:
    """AC5 — no manager / no linked account: no rows, ids-only log, commit."""
    world = await _seed_world(session, TENANT_A, "A")
    no_manager = await _add_unmanaged_employee(session, TENANT_A, "NM")
    unlinked_manager = await _add_employee_with_unlinked_manager(
        session, TENANT_A, "UL"
    )

    structlog.configure(cache_logger_on_first_use=False)
    with structlog.testing.capture_logs() as logs:
        async with _hop() as hop:
            await hop.publish(
                _requested(world, employee_id=no_manager),
                _requested(world, employee_id=unlinked_manager),
                _requested(world),
            )
            rows = await _wait_notifications(1)
            assert len(rows) == 1
            await _wait_deliveries(rows[0].id)

    assert rows[0].recipient_employee_id == world.manager_id
    errors = {entry.get("error") for entry in logs}
    assert "no_manager" in errors
    assert "no_linked_account" in errors
    dumped = json.dumps(logs, default=str)
    assert SENTINEL not in dumped


# --- AC 3: redelivery ----------------------------------------------------------


async def test_redelivery_writes_nothing_and_resends_no_sent_delivery(
    session: AsyncSession,
) -> None:
    """AC3 — the same event twice is one row; only the new event sends again."""
    world = await _seed_world(session, TENANT_A, "A")
    requested = _requested(world)
    approved = _approved(world)  # distinct event_id, distinct kind

    async with _hop() as hop:
        # Single partition: the duplicate is fully handled before `approved`,
        # so two rows is a deterministic "the duplicate was a no-op" barrier.
        await hop.publish(requested, requested, approved)
        rows = await _wait_notifications(2)
        assert len(rows) == 2
        for row in rows:
            await _wait_deliveries(row.id)
        assert len(await _read_notifications()) == 2

    assert {row.event_id for row in rows} == {
        requested["event_id"],
        approved["event_id"],
    }
    assert isinstance(hop.email, RecordingEmailSender)
    assert isinstance(hop.slack, RecordingSlackSender)
    assert len(hop.email.calls) == 2  # one per notification, never the replay
    assert len(hop.slack.calls) == 2
    assert len(hop.email.sent) == 2
    assert len(hop.slack.sent) == 2


# --- AC 4/7: failures and configuration ---------------------------------------


async def test_email_failure_is_bounded_and_slack_still_completes(
    session: AsyncSession,
) -> None:
    """AC4 — ≤3 attempts, `failed` with payload-free `last_error`, Slack sent,
    and the next message is still handled after the offset advances."""
    world = await _seed_world(session, TENANT_A, "A")
    email = EchoingEmailSender()

    async with _hop(email_sender=email, slack_sender=RecordingSlackSender()) as hop:
        await hop.publish(_requested(world), _approved(world))
        rows = await _wait_notifications(2)
        assert len(rows) == 2
        deliveries = {row.id: await _wait_deliveries(row.id) for row in rows}

    assert len(email.calls) == 6  # 3 attempts × 2 messages, never more
    for row in rows:
        by_channel = _by_channel(deliveries[row.id])
        failed = by_channel[DeliveryChannel.EMAIL]
        assert failed.status == DeliveryStatus.FAILED.value
        assert failed.attempts == 3
        assert failed.sent_at is None
        assert by_channel[DeliveryChannel.SLACK].status == DeliveryStatus.SENT.value
        assert failed.last_error is not None
        assert failed.last_error.startswith("RuntimeError: ")
        for secret in (
            row.subject,
            row.body,
            world.manager_account,
            world.employee_account,
            "Mary Manager",
            "Ada Lovelace",
        ):
            assert secret not in failed.last_error


@pytest.mark.parametrize("channel", ["email", "slack"])
async def test_unconfigured_channel_skips_independently(
    session: AsyncSession, channel: str
) -> None:
    """AC7 — the missing channel is `skipped(not_configured)`; the other sends."""
    world = await _seed_world(session, TENANT_A, "A")
    not_configured = NotConfiguredError()
    email: EmailSender
    slack: SlackSender
    if channel == "email":
        email = RecordingEmailSender(error=not_configured)
        slack = RecordingSlackSender()
    else:
        email = RecordingEmailSender()
        slack = RecordingSlackSender(error=not_configured)

    async with _hop(email_sender=email, slack_sender=slack) as hop:
        await hop.publish(_requested(world))
        rows = await _wait_notifications(1)
        deliveries = _by_channel(await _wait_deliveries(rows[0].id))

    skipped = deliveries[DeliveryChannel(channel)]
    assert skipped.status == DeliveryStatus.SKIPPED.value
    assert skipped.last_error == SKIP_NOT_CONFIGURED
    assert skipped.attempts == 0  # never retried
    assert skipped.sent_at is None

    other = DeliveryChannel.SLACK if channel == "email" else DeliveryChannel.EMAIL
    assert deliveries[other].status == DeliveryStatus.SENT.value
    assert isinstance(hop.email, RecordingEmailSender)
    assert isinstance(hop.slack, RecordingSlackSender)
    # The unconfigured channel was attempted exactly once, the other sent once.
    assert len(hop.email.calls) == 1
    assert len(hop.slack.calls) == 1


# --- AC 8: tenant isolation -----------------------------------------------------


async def test_rows_and_deliveries_carry_the_events_tenant(
    session: AsyncSession,
) -> None:
    """AC8 — an event for tenant B writes B's rows and addresses B's people."""
    world_a = await _seed_world(session, TENANT_A, "A")
    world_b = await _seed_world(session, TENANT_B, "B")

    async with _hop() as hop:
        await hop.publish(_requested(world_b))
        rows = await _wait_notifications(1)
        assert len(rows) == 1
        await _wait_deliveries(rows[0].id)

    row = rows[0]
    assert row.tenant_id == TENANT_B
    assert row.recipient_employee_id == world_b.manager_id
    assert row.recipient_user_id == world_b.manager_user_id
    assert row.tenant_id != world_a.tenant_id
    assert isinstance(hop.email, RecordingEmailSender)
    assert isinstance(hop.slack, RecordingSlackSender)
    assert hop.email.sent[0][0].employee_id == world_b.manager_id
    assert hop.slack.sent[0][0].employee_id == world_b.manager_id


# --- AC 8/9: the configured Slack target and the PII sweep ---------------------


async def test_hostile_payload_and_provider_echo_never_leak(
    session: AsyncSession,
) -> None:
    """The end-to-end PII sweep: payload free text, the rendered message and a
    hostile provider echo cannot reach a row body, a log line or `last_error`."""
    world = await _seed_world(session, TENANT_A, "A")
    hostile = _requested(world)
    hostile["data"]["reason"] = SENTINEL
    hostile["data"]["decision_note"] = SENTINEL
    email = EchoingEmailSender()

    structlog.configure(cache_logger_on_first_use=False)
    with structlog.testing.capture_logs() as logs:
        async with _hop(email_sender=email, slack_sender=RecordingSlackSender()) as hop:
            await hop.publish(hostile)
            rows = await _wait_notifications(1)
            deliveries = _by_channel(await _wait_deliveries(rows[0].id))

    row = rows[0]
    assert SENTINEL not in row.subject
    assert SENTINEL not in row.body
    assert "reason" not in row.body.lower()
    assert "decision_note" not in row.body.lower()

    failed = deliveries[DeliveryChannel.EMAIL]
    assert failed.status == DeliveryStatus.FAILED.value
    assert failed.attempts == 3
    assert failed.last_error is not None
    for secret in (
        row.subject,
        row.body,
        world.employee_name,
        world.manager_name,
        world.employee_account,
        world.manager_account,
        SENTINEL,
    ):
        assert secret not in failed.last_error

    dumped = json.dumps(logs, default=str)
    assert SENTINEL not in dumped
    assert row.body not in dumped
    assert world.employee_account not in dumped
    assert world.manager_account not in dumped


async def test_slack_posts_to_the_configured_channel_and_omits_free_text(
    session: AsyncSession,
) -> None:
    """AC8/9 — the real Slack adapter targets the configured channel; a hostile
    payload's `reason`/`decision_note` never enters the message or a log line."""
    world = await _seed_world(session, TENANT_A, "A")
    hostile = _requested(world)
    hostile["data"]["reason"] = SENTINEL
    hostile["data"]["decision_note"] = SENTINEL
    client = FakeSlackClient()
    slack = SlackWebSender(
        bot_token=SLACK_TOKEN,
        channel_id=SLACK_CHANNEL,
        client=client,
    )

    structlog.configure(cache_logger_on_first_use=False)
    with structlog.testing.capture_logs() as logs:
        async with _hop(slack_sender=slack) as hop:
            await hop.publish(hostile)
            rows = await _wait_notifications(1)
            assert len(rows) == 1
            await _wait_deliveries(rows[0].id)

    row = rows[0]
    assert SENTINEL not in row.subject
    assert SENTINEL not in row.body
    assert "reason" not in row.body.lower()
    assert "decision_note" not in row.body.lower()
    assert world.employee_name in row.body

    assert len(client.calls) == 1
    channel, text = client.calls[0]
    assert channel == SLACK_CHANNEL
    assert text == f"{row.subject}\n\n{row.body}"
    assert SENTINEL not in text
    assert world.employee_account not in text
    assert world.manager_account not in text

    dumped = json.dumps(logs, default=str)
    assert SENTINEL not in dumped
    assert world.employee_account not in dumped
    assert world.manager_account not in dumped
