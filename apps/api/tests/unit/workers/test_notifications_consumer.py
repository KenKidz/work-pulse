"""Unit tests — the notification consumer loop against stubs (T5, ADR 0015).

The real `NotificationsConsumer` runs with an in-memory source, directory and
store, recording sender fakes and a recording sleeper: no broker, database or
provider is touched and no test waits on the wall clock (`.ai/testing.md`).
Pinned here: every deliberate skip commits and the loop continues; the offset
advances only after terminal deliveries; store failures retry without a commit
and never kill the loop; readiness (`started`) fires after the first completed
fetch round; and log lines are ids-only.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import TracebackType
from typing import Any
from uuid import UUID

import pytest
import structlog
from uuid_utils.compat import uuid7

from app.domains.attendance.events import attendance_checked_in, attendance_late
from app.domains.leave.events import leave_approved, leave_requested
from app.domains.notifications.models import (
    DeliveryChannel,
    DeliveryOutcome,
    DeliveryStatus,
    DeliverySummary,
    RenderedMessage,
)
from app.domains.notifications.schemas import Recipient, SubjectFacts
from app.domains.notifications.service import NotificationService
from tests.unit.notifications.fakes import (
    FakeNotificationRepository,
    RecordingEmailSender,
    RecordingSlackSender,
    RecordingSleeper,
)
from workers.notifications.consumer import (
    EventSource,
    NotificationsConsumer,
    NotificationUnit,
    UnitFactory,
)

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
ACTOR = UUID("00000000-0000-4000-8000-000000000031")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000001")
EMPLOYEE_B = UUID("00000000-0000-4000-8000-000000000002")
MANAGER = UUID("00000000-0000-4000-8000-000000000003")
MANAGER_USER = UUID("00000000-0000-4000-8000-000000000041")
EMPLOYEE_USER = UUID("00000000-0000-4000-8000-000000000042")
LEAVE_REQUEST = UUID("00000000-0000-4000-8000-0000000000e1")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000a1")
SHIFT = UUID("00000000-0000-4000-8000-0000000000b1")
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
WORKDAY = date(2026, 10, 21)
# A leak test value: it must never appear in a log line (ids-only contract).
SENTINEL = "Zorblax Qqweird pii-unique@example.test"

MANAGER_RECIPIENT = Recipient(
    employee_id=MANAGER,
    user_id=MANAGER_USER,
    display_name="Mary Manager",
    account_address="mary.manager@example.test",
)
EMPLOYEE_RECIPIENT = Recipient(
    employee_id=EMPLOYEE,
    user_id=EMPLOYEE_USER,
    display_name="Ada Lovelace",
    account_address="ada@example.test",
)


def _requested(employee_id: UUID = EMPLOYEE) -> dict[str, Any]:
    return leave_requested(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST,
        employee_id=employee_id,
        leave_type_code="annual",
        start_date=WORKDAY,
        end_date=date(2026, 10, 23),
        days=3,
    )


def _approved(employee_id: UUID = EMPLOYEE) -> dict[str, Any]:
    return leave_approved(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST,
        employee_id=employee_id,
        leave_type_code="annual",
        start_date=WORKDAY,
        end_date=date(2026, 10, 23),
        days=3,
        decided_by=ACTOR,
    )


def _late(employee_id: UUID = EMPLOYEE) -> dict[str, Any]:
    return attendance_late(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        attendance_id=ATTENDANCE,
        employee_id=employee_id,
        workday=WORKDAY,
        shift_id=SHIFT,
        minutes_late=30,
    )


def _checked_in(employee_id: UUID = EMPLOYEE) -> dict[str, Any]:
    return attendance_checked_in(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        attendance_id=ATTENDANCE,
        employee_id=employee_id,
        workday=WORKDAY,
        work_mode="onsite",
        late=False,
        minutes_late=None,
    )


def _bytes(envelope: Mapping[str, Any]) -> bytes:
    return json.dumps(envelope).encode("utf-8")


class StubMessage:
    """The fetched-record shape the loop reads (`message.value`)."""

    def __init__(self, value: bytes | None) -> None:
        self.value = value


class StubSource:
    """Scripted source: `getmany` hands out one queued batch per call."""

    def __init__(
        self,
        batches: Sequence[Sequence[bytes]] = (),
        *,
        failures: int = 0,
        commit_failures: int = 0,
    ) -> None:
        self._batches = [list(batch) for batch in batches]
        self._failures = failures
        self._commit_failures = commit_failures
        self.journal: list[str] = []
        self.on_drained: Callable[[], None] | None = None
        self.fetches = 0
        self.commits = 0

    async def getmany(self, *, timeout_ms: int) -> Mapping[Any, Sequence[StubMessage]]:
        self.fetches += 1
        if self._failures > 0:
            self._failures -= 1
            raise RuntimeError("broker unreachable")
        if not self._batches:
            if self.on_drained is not None:
                self.on_drained()
            return {}
        return {0: [StubMessage(value) for value in self._batches.pop(0)]}

    async def commit(self) -> None:
        self.journal.append("kafka_commit")
        if self._commit_failures > 0:
            self._commit_failures -= 1
            raise RuntimeError("coordinator left the group")
        self.commits += 1


class StubDirectory:
    """Read-only directory double: per-id answers, no I/O."""

    def __init__(
        self,
        *,
        facts: Mapping[UUID, SubjectFacts | None] | None = None,
        recipients: Mapping[UUID, Recipient | None] | None = None,
        actor_names: Mapping[UUID, str | None] | None = None,
    ) -> None:
        self._facts = facts or {}
        self._recipients = recipients or {}
        self._actor_names = actor_names or {}

    async def subject_facts(
        self, tenant_id: UUID, employee_id: UUID
    ) -> SubjectFacts | None:
        assert tenant_id == TENANT
        return self._facts.get(employee_id)

    async def recipient(self, tenant_id: UUID, employee_id: UUID) -> Recipient | None:
        assert tenant_id == TENANT
        return self._recipients.get(employee_id)

    async def actor_display_name(self, tenant_id: UUID, actor_id: UUID) -> str | None:
        assert tenant_id == TENANT
        return self._actor_names.get(actor_id)


@dataclass(frozen=True, slots=True)
class StubUnit:
    """The async-context wrapper `UnitFactory` returns."""

    unit: NotificationUnit

    async def __aenter__(self) -> NotificationUnit:
        return self.unit

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


def _factory(directory: StubDirectory, service: Any) -> UnitFactory:
    def build() -> AbstractAsyncContextManager[NotificationUnit]:
        return StubUnit(NotificationUnit(directory=directory, service=service))

    return build


class JournalCommitter:
    """Records the service's transaction boundaries in the source journal."""

    def __init__(self, journal: list[str]) -> None:
        self._journal = journal
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1
        self._journal.append("store_commit")


class FlakyRepository(FakeNotificationRepository):
    """Fails the first `get_or_create` calls (a transient store outage)."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self._remaining = failures

    async def get_or_create(self, notification: Any) -> tuple[Any, bool]:
        if self._remaining > 0:
            self._remaining -= 1
            raise RuntimeError("database unavailable")
        return await super().get_or_create(notification)


def _service(
    *,
    repo: FakeNotificationRepository | None = None,
    email: RecordingEmailSender | None = None,
    slack: RecordingSlackSender | None = None,
    sleeper: RecordingSleeper | None = None,
    journal: list[str] | None = None,
) -> NotificationService:
    return NotificationService(
        repo=repo if repo is not None else FakeNotificationRepository(),
        email_sender=email if email is not None else RecordingEmailSender(),
        slack_sender=slack if slack is not None else RecordingSlackSender(),
        commit=JournalCommitter(journal if journal is not None else []),
        sleep=sleeper if sleeper is not None else RecordingSleeper(),
        backoff_base_seconds=0.0,
    )


def _consumer(
    source: EventSource,
    factory: UnitFactory,
    *,
    poll_seconds: float = 0.001,
    backoff_base: float = 0.5,
    backoff_max: float = 8.0,
) -> NotificationsConsumer:
    return NotificationsConsumer(
        source,
        factory,
        poll_seconds=poll_seconds,
        backoff_base=backoff_base,
        backoff_max=backoff_max,
        now=lambda: NOW,
    )


def _manager_directory(*, manager_id: UUID | None = MANAGER) -> StubDirectory:
    return StubDirectory(
        facts={EMPLOYEE: SubjectFacts("Ada Lovelace", manager_id)},
        recipients={MANAGER: MANAGER_RECIPIENT, EMPLOYEE: EMPLOYEE_RECIPIENT},
        actor_names={ACTOR: "Grace Hopper"},
    )


def _record_pauses(
    monkeypatch: pytest.MonkeyPatch, consumer: NotificationsConsumer, *, stop_after: int
) -> list[float]:
    """Replace the loop's sleep with a recorder; the Nth pause stops the loop."""
    pauses: list[float] = []

    async def fake_pause(seconds: float) -> None:
        pauses.append(seconds)
        if len(pauses) >= stop_after:
            consumer.stop()

    monkeypatch.setattr(consumer, "_pause", fake_pause)
    return pauses


async def test_leave_requested_delivers_to_the_manager_on_both_channels() -> None:
    source = StubSource([[_bytes(_requested())]])
    directory = _manager_directory()
    repo = FakeNotificationRepository()
    email = RecordingEmailSender()
    slack = RecordingSlackSender()
    journal: list[str] = []
    source.journal = journal
    service = _service(repo=repo, email=email, slack=slack, journal=journal)
    consumer = _consumer(source, _factory(directory, service))
    source.on_drained = consumer.stop

    await consumer.run()

    rows = list(repo.notifications.values())
    assert len(rows) == 1
    row = rows[0]
    assert row.tenant_id == TENANT
    assert row.kind.value == "leave_requested"
    assert row.recipient_employee_id == MANAGER
    assert row.recipient_user_id == MANAGER_USER
    assert email.sent[0][0] == MANAGER_RECIPIENT
    assert slack.sent[0][0] == MANAGER_RECIPIENT
    assert len(email.sent) == 1
    assert len(slack.sent) == 1
    # Terminal store writes happen before the Kafka offset commit.
    assert source.journal[-1] == "kafka_commit"
    assert "store_commit" in source.journal[:-1]
    assert source.commits == 1
    assert consumer.started.is_set()


async def test_approved_delivers_to_the_requester_and_names_the_decider() -> None:
    source = StubSource([[_bytes(_approved())]])
    directory = StubDirectory(
        facts={EMPLOYEE: SubjectFacts("Ada Lovelace", MANAGER)},
        recipients={EMPLOYEE: EMPLOYEE_RECIPIENT},
        actor_names={ACTOR: "Grace Hopper"},
    )
    repo = FakeNotificationRepository()
    email = RecordingEmailSender()
    service = _service(repo=repo, email=email)
    consumer = _consumer(source, _factory(directory, service))
    source.on_drained = consumer.stop

    await consumer.run()

    row = next(iter(repo.notifications.values()))
    assert row.recipient_employee_id == EMPLOYEE
    assert "approved by Grace Hopper" in row.body


async def test_skip_messages_commit_and_the_loop_continues() -> None:
    structlog.configure(cache_logger_on_first_use=False)
    unknown = {
        "event_id": "evt_unknown",
        "event_type": f"mystery.{SENTINEL}",
        "version": 1,
        "tenant_id": str(TENANT),
        "occurred_at": NOW.isoformat(),
        "actor_id": str(ACTOR),
        "data": {},
    }
    source = StubSource(
        [
            [
                b"this is not json",
                _bytes(unknown),
                _bytes(_checked_in()),
                _bytes(_requested()),
            ]
        ]
    )
    directory = _manager_directory()
    repo = FakeNotificationRepository()
    email = RecordingEmailSender()
    service = _service(repo=repo, email=email)
    consumer = _consumer(source, _factory(directory, service))
    source.on_drained = consumer.stop

    with structlog.testing.capture_logs() as logs:
        await consumer.run()

    assert len(repo.notifications) == 1
    assert len(email.sent) == 1
    assert source.commits == 4  # every message was handled — skips advance too
    skipped = [entry for entry in logs if entry["event"] == "consumer_message_skipped"]
    assert {entry["error"] for entry in skipped} == {
        "malformed_envelope",
        "unknown_event_type",
    }
    dumped = json.dumps(logs, default=str)
    assert SENTINEL not in dumped  # hostile event type never echoed
    assert "evt_unknown" in dumped  # ids-only identification


async def test_unresolvable_recipients_write_no_rows_and_commit() -> None:
    structlog.configure(cache_logger_on_first_use=False)
    no_manager_source = StubSource([[_bytes(_requested())]])
    no_manager_directory = StubDirectory(
        facts={EMPLOYEE: SubjectFacts("Ada Lovelace", None)},
        recipients={MANAGER: MANAGER_RECIPIENT},
    )
    no_manager_repo = FakeNotificationRepository()
    no_manager_consumer = _consumer(
        no_manager_source,
        _factory(
            no_manager_directory,
            _service(repo=no_manager_repo),
        ),
    )
    no_manager_source.on_drained = no_manager_consumer.stop

    with structlog.testing.capture_logs() as no_manager_logs:
        await no_manager_consumer.run()

    assert no_manager_repo.notifications == {}
    assert no_manager_source.commits == 1
    assert any(entry.get("error") == "no_manager" for entry in no_manager_logs)

    no_account_source = StubSource([[_bytes(_requested())]])
    no_account_directory = StubDirectory(
        facts={EMPLOYEE: SubjectFacts("Ada Lovelace", MANAGER)},
        recipients={},  # the manager has no linked account
    )
    no_account_repo = FakeNotificationRepository()
    no_account_consumer = _consumer(
        no_account_source,
        _factory(no_account_directory, _service(repo=no_account_repo)),
    )
    no_account_source.on_drained = no_account_consumer.stop

    with structlog.testing.capture_logs() as no_account_logs:
        await no_account_consumer.run()

    assert no_account_repo.notifications == {}
    assert no_account_source.commits == 1
    assert any(entry.get("error") == "no_linked_account" for entry in no_account_logs)


class StubService:
    """Returns scripted summaries; records every call in the shared journal."""

    def __init__(
        self, summaries: Sequence[DeliverySummary], journal: list[str]
    ) -> None:
        self._summaries = list(summaries)
        self._journal = journal
        self.calls = 0

    async def handle(
        self,
        intent: Any,
        recipient: Recipient,
        message: RenderedMessage,
        now: datetime,
    ) -> DeliverySummary:
        self.calls += 1
        self._journal.append("service_call")
        if len(self._summaries) > 1:
            return self._summaries.pop(0)
        return self._summaries[0]


def _summary(status: DeliveryStatus) -> DeliverySummary:
    return DeliverySummary(
        notification_id=uuid7(),
        created=True,
        deliveries=(
            DeliveryOutcome(
                channel=DeliveryChannel.EMAIL,
                status=status,
                attempts=1,
            ),
        ),
    )


async def test_a_non_terminal_summary_never_authorizes_a_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = StubSource([[_bytes(_requested())]])
    directory = _manager_directory()
    journal: list[str] = []
    source.journal = journal
    service = StubService(
        [_summary(DeliveryStatus.PENDING), _summary(DeliveryStatus.SENT)],
        journal,
    )
    consumer = _consumer(source, _factory(directory, service))  # type: ignore[arg-type]
    source.on_drained = consumer.stop
    pauses = _record_pauses(monkeypatch, consumer, stop_after=99)

    await consumer.run()

    assert pauses == [0.5]  # one retry after the non-terminal summary
    assert service.calls == 2
    assert journal == ["service_call", "service_call", "kafka_commit"]
    assert source.commits == 1


async def test_store_failure_retries_without_committing_and_the_loop_lives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structlog.configure(cache_logger_on_first_use=False)
    source = StubSource([[_bytes(_requested()), _bytes(_requested(EMPLOYEE_B))]])
    directory = StubDirectory(
        facts={
            EMPLOYEE: SubjectFacts("Ada Lovelace", MANAGER),
            EMPLOYEE_B: SubjectFacts("Alan Turing", MANAGER),
        },
        recipients={MANAGER: MANAGER_RECIPIENT},
    )
    repo = FlakyRepository(failures=2)
    service = _service(repo=repo)
    consumer = _consumer(source, _factory(directory, service))
    source.on_drained = consumer.stop
    pauses = _record_pauses(monkeypatch, consumer, stop_after=99)

    with structlog.testing.capture_logs() as logs:
        await consumer.run()

    assert [0.5, 1.0] == pauses  # bounded, doubling backoff with no sleeps
    assert len(repo.notifications) == 2  # both messages eventually stored
    assert source.commits == 2
    assert sum(1 for entry in logs if entry["event"] == "consumer_handle_failed") == 2


async def test_commit_failure_never_kills_the_loop() -> None:
    source = StubSource(
        [[_bytes(_requested()), _bytes(_requested(EMPLOYEE_B))]],
        commit_failures=1,
    )
    directory = StubDirectory(
        facts={
            EMPLOYEE: SubjectFacts("Ada Lovelace", MANAGER),
            EMPLOYEE_B: SubjectFacts("Alan Turing", MANAGER),
        },
        recipients={MANAGER: MANAGER_RECIPIENT},
    )
    repo = FakeNotificationRepository()
    consumer = _consumer(source, _factory(directory, _service(repo=repo)))
    source.on_drained = consumer.stop

    await consumer.run()

    assert len(repo.notifications) == 2
    assert source.journal.count("kafka_commit") == 2
    assert source.commits == 1  # the first commit failed, the loop kept going


async def test_fetch_failures_back_off_and_never_kill_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = StubSource([[_bytes(_requested())]], failures=2)
    directory = _manager_directory()
    repo = FakeNotificationRepository()
    consumer = _consumer(source, _factory(directory, _service(repo=repo)))
    source.on_drained = consumer.stop
    pauses = _record_pauses(monkeypatch, consumer, stop_after=99)

    await consumer.run()

    assert pauses == [0.5, 1.0]
    assert len(repo.notifications) == 1


async def test_started_fires_only_after_the_first_completed_fetch_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = StubSource([], failures=2)
    consumer = _consumer(source, _factory(_manager_directory(), _service()))
    source.on_drained = consumer.stop
    pauses = _record_pauses(monkeypatch, consumer, stop_after=99)

    assert not consumer.started.is_set()

    await consumer.run()

    assert pauses == [0.5, 1.0]  # failed rounds never signalled readiness
    assert consumer.started.is_set()


async def test_stop_before_the_first_fetch_touches_nothing() -> None:
    source = StubSource([[_bytes(_requested())]])
    consumer = _consumer(source, _factory(_manager_directory(), _service()))

    consumer.stop()
    await consumer.run()

    assert source.fetches == 0
    assert source.commits == 0
    assert not consumer.started.is_set()


def test_backoff_caps_at_the_maximum() -> None:
    consumer = _consumer(
        StubSource(),
        _factory(_manager_directory(), _service()),
        backoff_base=1.0,
        backoff_max=5.0,
    )

    assert [consumer._backoff_seconds(n) for n in range(1, 5)] == [1.0, 2.0, 4.0, 5.0]
    assert consumer._backoff_seconds(5000) == 5.0  # bounded exponent, no overflow


def test_invalid_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        _consumer(
            StubSource(),
            _factory(_manager_directory(), _service()),
            poll_seconds=0,
        )
    with pytest.raises(ValueError):
        _consumer(
            StubSource(),
            _factory(_manager_directory(), _service()),
            backoff_base=2.0,
            backoff_max=1.0,
        )


def test_the_five_trigger_types_are_the_only_mapped_kinds() -> None:
    """Guardrail: adding a mapping entry must be a deliberate spec/ADR change."""
    from workers.notifications.mapping import TRIGGER_EVENT_TYPES

    assert TRIGGER_EVENT_TYPES == {
        "leave.requested",
        "leave.approved",
        "leave.rejected",
        "leave.withdrawn",
        "attendance.late",
    }
