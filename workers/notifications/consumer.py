"""The notification consumer: `workpulse.events` → stored deliveries (ADR 0015).

One instance (v1). The loop fetches batches from the bus, maps each message to
a `NotificationIntent` through the pure mapper, resolves the addressee and
display names through the read-only directory, renders the stored message of
record, drives the delivery service and commits the offset **only after every
delivery is terminal**. A deliberate skip (malformed envelope, unknown type,
unresolvable recipient) commits too — it is a terminal decision.

Unlike the wall (ADR 0013), notifications have no snapshot refresh to heal a
lost message, so store or reference-read errors are retried in-process with
bounded backoff and the offset is left uncommitted: at-least-once toward the
store, dedupe on `event_id` making a redelivery harmless. A poisoned store
error can stall the single v1 worker — the documented, accepted failure mode
(spec R7). Fetch and commit hiccups never kill the loop, and every log line is
ids-only: malformed payloads are skipped with the event id and an error
category, never contents (`.ai/security.md`).

The source and unit-of-work ports are structural protocols, so the unit tests
drive this exact loop with in-memory stubs — no broker, no database, no
provider (`.ai/testing.md`).
"""

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from app.domains.notifications.models import DeliverySummary, RenderedMessage
from app.domains.notifications.ports import RecipientDirectory
from app.domains.notifications.schemas import NotificationIntent, Recipient
from app.shared.logging import get_logger
from app.shared.telemetry import event_type_label, worker_unit
from workers.notifications.mapping import (
    is_known_event_type,
    is_trigger_envelope,
    map_envelope,
    resolve_recipient,
)
from workers.notifications.templates import render

_log = get_logger("notifications.consumer")

# A bounded exponent: an unbounded `2 ** n` overflows to a float error after
# ~1025 consecutive failures and would kill the loop exactly mid-outage.
_MAX_BACKOFF_EXPONENT = 32


def _utc_now() -> datetime:
    """The handling instant (the only wall-clock read in the message path)."""
    return datetime.now(UTC)


class KafkaMessage(Protocol):
    """One fetched record — `aiokafka`'s `ConsumerRecord` satisfies it."""

    value: bytes | None


class EventSource(Protocol):
    """Bus-consumer port — `AIOKafkaConsumer` satisfies it."""

    async def getmany(
        self, *, timeout_ms: int
    ) -> Mapping[Any, Sequence[KafkaMessage]]: ...

    async def commit(self) -> None: ...


class DeliveryService(Protocol):
    """The one application operation the consumer invokes (domain port shape)."""

    async def handle(
        self,
        intent: NotificationIntent,
        recipient: Recipient,
        message: RenderedMessage,
        now: datetime,
    ) -> DeliverySummary: ...


@dataclass(frozen=True, slots=True)
class NotificationUnit:
    """One message's reference reads and delivery service on a fresh session.

    A unit is opened per handling attempt so a store/reference failure cannot
    leak a broken transaction into the retry.
    """

    directory: RecipientDirectory
    service: DeliveryService


class UnitFactory(Protocol):
    """Opens a `NotificationUnit` context (`main` wires a DB session inside)."""

    def __call__(self) -> AbstractAsyncContextManager[NotificationUnit]: ...


@dataclass(frozen=True, slots=True)
class _Prepared:
    """A trigger resolved to its person-addressed, rendered delivery."""

    intent: NotificationIntent
    recipient: Recipient
    message: RenderedMessage


class NotificationsConsumer:
    """The fetch → map/resolve/render → deliver → commit loop."""

    def __init__(
        self,
        source: EventSource,
        unit_factory: UnitFactory,
        *,
        poll_seconds: float,
        backoff_base: float,
        backoff_max: float,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be > 0")
        if backoff_base <= 0 or backoff_max < backoff_base:
            raise ValueError("backoff_max must be >= backoff_base > 0")
        self._source = source
        self._unit_factory = unit_factory
        self._poll_seconds = poll_seconds
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._now = now
        self._stopping = asyncio.Event()
        # Set once the first fetch round completes: the consumer has joined its
        # group and pinned its `latest` position — readiness for the gate's log
        # line and for tests, never a sleep (ADR 0013 decision 4).
        self.started = asyncio.Event()

    def stop(self) -> None:
        """Ask the loop to stop gracefully (idempotent, signal-handler safe).

        Checked between messages: an in-flight message always finishes (its
        deliveries reach terminal and the offset commits) unless it is stuck in
        a store retry, in which case the offset stays uncommitted for the next
        run.
        """
        self._stopping.set()

    async def run(self) -> None:
        """Consume until `stop()`; broker hiccups back off, never kill the loop."""
        consecutive_failures = 0
        while not self._stopping.is_set():
            try:
                batches = await self._source.getmany(
                    timeout_ms=max(1, round(self._poll_seconds * 1000))
                )
            except Exception as exc:
                # A broker hiccup must not kill the worker: back off and retry.
                # The type only — never payloads or driver text.
                consecutive_failures += 1
                _log.warning("consumer_fetch_failed", error=type(exc).__name__)
                await self._pause(self._backoff_seconds(consecutive_failures))
                continue
            consecutive_failures = 0
            self.started.set()
            for messages in batches.values():
                for message in messages:
                    if self._stopping.is_set():
                        return  # this message stays unconsumed for the next run
                    await self._handle(message.value)

    async def _handle(self, value: bytes | None) -> None:
        """One message start-to-finish, or a committed deliberate skip."""
        envelope = _parse(value)
        event_type = (
            event_type_label(envelope.get("event_type"))
            if envelope is not None
            else "unknown"
        )
        async with worker_unit("notifications", event_type) as unit:
            if envelope is None:
                unit.mark_skipped()
                if not await self._commit():
                    unit.mark_failed("commit_failed")
                return

            intent = self._map(envelope)
            if intent is None:
                unit.mark_skipped()
                if not await self._commit():
                    unit.mark_failed("commit_failed")
                return

            outcome = await self._deliver(intent)
            if outcome in ("skipped", "stopped"):
                unit.mark_skipped()
            elif outcome == "failed":
                unit.mark_failed("delivery_failed")

    async def _deliver(self, intent: NotificationIntent) -> str:
        """Resolve, render and deliver; return the message outcome.

        `succeeded` — every delivery terminal and the offset committed;
        `skipped` — a deliberate terminal skip (no recipient, invalid
        content) and the offset committed; `failed` — a store/commit failure
        left the offset uncommitted; `stopped` — `stop()` arrived before the
        message became terminal. Store errors retry in-process before any
        commit, so one poisoning is not a loop failure.
        """
        failures = 0
        while not self._stopping.is_set():
            try:
                prepared: _Prepared | None = None
                async with self._unit_factory() as unit:
                    prepared = await self._prepare(intent, unit.directory)
                    if prepared is not None:
                        summary = await unit.service.handle(
                            prepared.intent,
                            prepared.recipient,
                            prepared.message,
                            self._now(),
                        )
                        if not summary.terminal:
                            # The service promises terminal summaries; a
                            # non-terminal one must never authorize a commit.
                            raise RuntimeError("delivery summary is not terminal")
                if not await self._commit():
                    return "failed"
                return "succeeded" if prepared is not None else "skipped"
            except Exception as exc:
                # Store/reference outage: back off and retry the same message.
                # Never commit past it (at-least-once toward the store); the
                # type only — driver text can embed statement or payload data.
                failures += 1
                _log.warning(
                    "consumer_handle_failed",
                    event_id=intent.event_id,
                    error=type(exc).__name__,
                )
                await self._pause(self._backoff_seconds(failures))
        # Stopped before the message became terminal: the offset stays put.
        return "failed" if failures else "stopped"

    async def _prepare(
        self, intent: NotificationIntent, directory: RecipientDirectory
    ) -> _Prepared | None:
        """Resolve recipient + names and render; `None` is a committed skip."""
        facts = await directory.subject_facts(
            intent.tenant_id, intent.content.subject_employee_id
        )
        if facts is None:
            _log.warning(
                "consumer_message_skipped",
                event_id=intent.event_id,
                error="unknown_subject",
            )
            return None

        recipient_employee_id = resolve_recipient(intent, facts)
        if recipient_employee_id is None:
            # The affected employee has no direct manager — no rows, ids only.
            _log.warning(
                "consumer_message_skipped",
                event_id=intent.event_id,
                error="no_manager",
                kind=intent.kind.value,
            )
            return None

        recipient = await directory.recipient(intent.tenant_id, recipient_employee_id)
        if recipient is None:
            # A person-addressed notification is never sent "to the channel
            # anyway" (ADR 0015 decision 4).
            _log.warning(
                "consumer_message_skipped",
                event_id=intent.event_id,
                error="no_linked_account",
                kind=intent.kind.value,
                employee_id=str(recipient_employee_id),
            )
            return None

        actor_name = None
        decided_by = intent.content.decided_by
        if decided_by is not None:
            actor_name = await directory.actor_display_name(
                intent.tenant_id, decided_by
            )

        try:
            message = render(
                intent,
                subject_name=facts.display_name,
                recipient_name=recipient.display_name,
                actor_name=actor_name,
            )
        except ValueError:
            # A mapping/render disagreement is malformed input, not a transient
            # failure: skip it terminal and keep the loop alive.
            _log.warning(
                "consumer_message_skipped",
                event_id=intent.event_id,
                error="invalid_content",
            )
            return None

        return _Prepared(
            intent=replace(intent, recipient_employee_id=recipient_employee_id),
            recipient=recipient,
            message=message,
        )

    def _map(self, envelope: Mapping[str, Any]) -> NotificationIntent | None:
        """Map + log deliberate skips ids-only; known non-triggers stay silent."""
        intent = map_envelope(envelope)
        if intent is not None:
            return intent
        if is_trigger_envelope(envelope):
            _log.warning(
                "consumer_message_skipped",
                event_id=_event_id(envelope),
                error="invalid_envelope",
            )
        elif not is_known_event_type(envelope.get("event_type")):
            # The event type itself may be hostile text — never echo it.
            _log.warning(
                "consumer_message_skipped",
                event_id=_event_id(envelope),
                error="unknown_event_type",
            )
        return None

    async def _commit(self) -> bool:
        """Advance the offset past the handled/skipped message (failures retry).

        Returns `False` when the commit failed; the offset simply stays put
        and the rows already stored make the redelivery a dedupe no-op —
        at-least-once from Kafka, harmless. The loop keeps running.
        """
        try:
            await self._source.commit()
            return True
        except Exception as exc:
            _log.warning("consumer_commit_failed", error=type(exc).__name__)
            return False

    def _backoff_seconds(self, consecutive_failures: int) -> float:
        """Exponential between attempts: base, 2×, 4×, … capped at `backoff_max`."""
        exponent = min(consecutive_failures - 1, _MAX_BACKOFF_EXPONENT)
        return min(self._backoff_base * 2**exponent, self._backoff_max)

    async def _pause(self, seconds: float) -> None:
        """Sleep, but wake immediately on `stop()`."""
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except TimeoutError:
            pass


def _parse(value: bytes | None) -> dict[str, Any] | None:
    """Decode one topic message; anything unparseable is logged and skipped."""
    try:
        envelope = json.loads(value or b"")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # The type only — a decode error's message can quote the raw value.
        _log.warning(
            "consumer_message_skipped",
            error="malformed_envelope",
            error_type=type(exc).__name__,
        )
        return None
    if not isinstance(envelope, dict):
        # Valid JSON that is not an envelope (a scalar or a list).
        _log.warning(
            "consumer_message_skipped",
            error="malformed_envelope",
            error_type=type(envelope).__name__,
        )
        return None
    return envelope


def _event_id(envelope: Mapping[str, Any]) -> str | None:
    """The envelope's `event_id` when it is a plain string (ids-only logs)."""
    event_id = envelope.get("event_id")
    return event_id if isinstance(event_id, str) else None
