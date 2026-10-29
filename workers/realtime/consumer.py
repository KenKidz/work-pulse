"""The realtime consumer: `workpulse.events` → presence facts (ADR 0013).

One instance (v1). The loop fetches batches from the bus, projects each
message through the pure `map_envelope` mapper, publishes the fact to the
tenant channel and commits the offset — every message handled start-to-finish
before the next (strict order; one partition in v1).

Delivery is at-least-once from Kafka / transient, at-most-once toward the
wall: the offset advances after the publish *attempt* — a swallowed Redis
failure still advances (the transient contract, identical to the write path's
best-effort publish) — so a crash between publish and restart can redeliver
an envelope, which maps to an identical full-row upsert (harmless by
contract). Missed deltas (worker down) recover via the client's snapshot
refresh; PostgreSQL stays the only truth.

Broker hiccups never kill the worker (no supervisor exists in v1): fetch
failures back off exponentially, commit failures simply leave the offset
unadvanced (the identical upsert repeats), and every log line is ids-only —
malformed payloads are skipped with the event id and an error category, never
contents (`.ai/security.md`).

The source and publisher ports are structural protocols, so the unit tests
drive this exact loop with in-memory stubs — no broker, no Redis
(`.ai/testing.md`).
"""

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Protocol

from app.shared.logging import get_logger
from app.shared.presence import PresenceFact, PresencePublisher
from app.shared.telemetry import event_type_label, worker_unit
from workers.realtime.mapping import is_realtime_envelope, map_envelope

_log = get_logger("realtime.consumer")

# The backoff exponent is bounded (same rationale as the relay's): an
# unbounded `2 ** n` overflows to a float error after ~1025 consecutive
# failures (~9 h at the 30 s cap) and would kill the loop mid-outage, exactly
# when it must keep retrying.
_MAX_BACKOFF_EXPONENT = 32


def _utc_today() -> date:
    """The consume date used to window leave events (server UTC)."""
    return datetime.now(UTC).date()


class KafkaMessage(Protocol):
    """One fetched record — `aiokafka`'s `ConsumerRecord` satisfies it."""

    value: bytes | None


class EventSource(Protocol):
    """Bus-consumer port — `AIOKafkaConsumer` satisfies it."""

    async def getmany(
        self, *, timeout_ms: int
    ) -> Mapping[Any, Sequence[KafkaMessage]]: ...

    async def commit(self) -> None: ...


class RealtimeConsumer:
    """The fetch → map → publish → commit loop (transient toward the wall)."""

    def __init__(
        self,
        source: EventSource,
        publisher: PresencePublisher,
        *,
        poll_seconds: float,
        backoff_base: float,
        backoff_max: float,
        today: Callable[[], date] = _utc_today,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be > 0")
        if backoff_base <= 0 or backoff_max < backoff_base:
            raise ValueError("backoff_max must be >= backoff_base > 0")
        self._source = source
        self._publisher = publisher
        self._poll_seconds = poll_seconds
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        # Injected consume-date clock: `leave.approved` is windowed to the day
        # the consumer is projecting, never the event's own date (ADR 0014).
        self._today = today
        self._stopping = asyncio.Event()
        # Set once the first fetch round completes: the consumer has joined
        # its group and pinned its `latest` position — readiness for the
        # gate's log line and for tests, never a sleep (ADR 0013/R6).
        self.started = asyncio.Event()

    def stop(self) -> None:
        """Ask the loop to stop gracefully (idempotent, signal-handler safe).

        Checked before each message: the in-flight one always finishes
        (publish attempted, offset committed); the rest stay unconsumed for
        the next run.
        """
        self._stopping.set()

    async def run(self) -> None:
        """Consume until `stop()`; broker hiccups are backed off, never fatal."""
        consecutive_failures = 0
        while not self._stopping.is_set():
            try:
                batches = await self._source.getmany(
                    timeout_ms=max(1, round(self._poll_seconds * 1000))
                )
            except Exception as exc:
                # A broker hiccup must not kill the worker: back off and
                # retry. The type only — never payloads or driver text.
                consecutive_failures += 1
                _log.warning("consumer_fetch_failed", error=type(exc).__name__)
                await self._pause(self._backoff_seconds(consecutive_failures))
                continue
            consecutive_failures = 0
            self.started.set()
            for messages in batches.values():
                for message in messages:
                    if self._stopping.is_set():
                        return  # stop between messages: this one stays for the next run
                    await self._handle(message.value)

    async def _handle(self, value: bytes | None) -> None:
        """One message start-to-finish: parse → map → publish attempt → commit."""
        envelope = _parse(value)
        event_type = (
            event_type_label(envelope.get("event_type"))
            if envelope is not None
            else "unknown"
        )
        async with worker_unit("realtime", event_type) as unit:
            if envelope is None:
                unit.mark_skipped()
                if not await self._commit():
                    unit.mark_failed("commit_failed")
                return

            fact = self._project(envelope)
            if fact is None:
                unit.mark_skipped()
            else:
                try:
                    await self._publisher.publish(fact)
                except Exception as exc:
                    # The port says implementations never raise; a violating
                    # adapter is still swallowed ids-only and the offset
                    # advances — the transient contract (the wall misses
                    # latency only). The unit is recorded as failed.
                    unit.mark_failed(type(exc).__name__)
                    _log.warning(
                        "consumer_publish_failed",
                        tenant_id=str(fact.tenant_id),
                        employee_id=str(fact.employee_id),
                        error=type(exc).__name__,
                    )
            if not await self._commit():
                unit.mark_failed("commit_failed")

    def _project(self, envelope: Mapping[str, Any]) -> PresenceFact | None:
        """Map one parsed message; skips are logged ids-only, never raise."""
        today = self._today()
        fact = map_envelope(envelope, today=today)
        if fact is None and is_realtime_envelope(envelope, today=today):
            # Ours but not fact-shaped (missing/invalid field): a skip, never
            # a partial fact, never a crash. The event id and an error
            # category only — the payload itself is never echoed.
            _log.warning(
                "consumer_message_skipped",
                event_id=_event_id(envelope),
                error="invalid_envelope",
            )
        return fact

    async def _commit(self) -> bool:
        """Advance the offset past the handled message (failures retry).

        Returns `False` when the commit failed; the offset simply stays put,
        the message is redelivered and its identical upsert repeats —
        at-least-once from Kafka, harmless. The loop keeps running.
        """
        try:
            await self._source.commit()
            return True
        except Exception as exc:
            _log.warning("consumer_commit_failed", error=type(exc).__name__)
            return False

    def _backoff_seconds(self, consecutive_failures: int) -> float:
        """Exponential between fetches: base, 2×, 4×, … capped at `backoff_max`."""
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
        _log.warning("consumer_message_skipped", error=type(exc).__name__)
        return None
    if not isinstance(envelope, dict):
        # Valid JSON that is not an envelope (a scalar or a list).
        _log.warning("consumer_message_skipped", error=type(envelope).__name__)
        return None
    return envelope


def _event_id(envelope: Mapping[str, Any]) -> str | None:
    """The envelope's `event_id` when it is a plain string (ids-only logs)."""
    event_id = envelope.get("event_id")
    return event_id if isinstance(event_id, str) else None
