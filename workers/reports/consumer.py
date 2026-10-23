"""The reports consumer: `workpulse.events` → ClickHouse projection (ADR 0019).

One instance (v1). The loop fetches batches from the bus, maps each message to
a fold command, applies the pure fold against the prior row read from the
projection store and commits the Kafka offset **only after the ClickHouse write
succeeds** (ADR 0019 decision 6). A deliberate skip — malformed envelope,
malformed payload, unknown/irrelevant event — commits too; it is a terminal
decision and a consumer never poisons its stream.

A ClickHouse failure leaves the offset uncommitted and retries the same message
with bounded in-process backoff: at-least-once toward the store, made harmless
by convergent per-entity folds (ADR 0019 decision 4). Head-of-line blocking on
a persistent store outage is the documented, accepted v1 failure mode — the
projection worker is a single instance.

The source and writer ports are structural protocols, so the unit tests drive
this exact loop with in-memory stubs — no broker, no ClickHouse
(`.ai/testing.md`). Every log line is ids-only: a skipped payload is identified
by `event_id` and an error category, never by its contents
(`.ai/security.md`).
"""

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.domains.reports.folds import (
    FoldError,
    fold_attendance,
    fold_employee_created,
    fold_employee_terminated,
    fold_employee_transferred,
    fold_leave,
    fold_overtime,
)
from app.domains.reports.repository import ProjectionWriter
from app.shared.logging import get_logger
from workers.reports.mapping import (
    FoldCommand,
    FoldKind,
    is_consumed_event_type,
    is_known_event_type,
    map_envelope,
)

_log = get_logger("reports.consumer")

# A bounded exponent: an unbounded `2 ** n` overflows to a float error after
# ~1025 consecutive failures and would kill the loop exactly mid-outage.
_MAX_BACKOFF_EXPONENT = 32


class KafkaMessage(Protocol):
    """One fetched record — `aiokafka`'s `ConsumerRecord` satisfies it."""

    value: bytes | None


class EventSource(Protocol):
    """Bus-consumer port — `AIOKafkaConsumer` satisfies it."""

    async def getmany(
        self, *, timeout_ms: int
    ) -> Mapping[Any, Sequence[KafkaMessage]]: ...

    async def commit(self) -> None: ...


class ReportsConsumer:
    """The fetch → map → fold/write → commit loop."""

    def __init__(
        self,
        source: EventSource,
        writer: ProjectionWriter,
        *,
        poll_seconds: float,
        backoff_base: float,
        backoff_max: float,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be > 0")
        if backoff_base <= 0 or backoff_max < backoff_base:
            raise ValueError("backoff_max must be >= backoff_base > 0")
        self._source = source
        self._writer = writer
        self._poll_seconds = poll_seconds
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._stopping = asyncio.Event()
        # Set once the first fetch round completes: the consumer has joined its
        # group and pinned its `earliest` position — readiness for the gate's
        # log line and for tests, never a sleep (ADR 0013 decision 4).
        self.started = asyncio.Event()

    def stop(self) -> None:
        """Ask the loop to stop gracefully (idempotent, signal-handler safe).

        Checked between messages: an in-flight message finishes (its write and
        commit) unless it is stuck retrying a store outage, in which case the
        offset stays uncommitted for the next run.
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
        command = self._map(value)
        if command is None:
            await self._commit()
            return
        await self._project(command)

    def _map(self, value: bytes | None) -> FoldCommand | None:
        """Parse + map one message; skips are logged ids-only, never raise."""
        envelope = _parse(value)
        if envelope is None:
            return None
        try:
            command = map_envelope(envelope)
        except FoldError as exc:
            # The fold's own error type: a consumed envelope missing a required
            # identifier. The message names the field only, never its value.
            _log.warning(
                "consumer_message_skipped",
                event_id=_event_id(envelope),
                error="invalid_envelope",
                reason=str(exc),
            )
            return None
        if command is not None:
            return command

        event_type = envelope.get("event_type")
        if is_consumed_event_type(event_type):
            # A consumed family at an unsupported version: a deliberate skip,
            # not an unknown event (folding it under v1 rules would be wrong).
            _log.warning(
                "consumer_message_skipped",
                event_id=_event_id(envelope),
                error="unsupported_version",
            )
        elif not is_known_event_type(event_type):
            # The event type itself may be hostile text — never echo it.
            _log.warning(
                "consumer_message_skipped",
                event_id=_event_id(envelope),
                error="unknown_event_type",
            )
        return None

    async def _project(self, command: FoldCommand) -> None:
        """Fold, write and commit; store errors retry before any commit."""
        failures = 0
        while not self._stopping.is_set():
            try:
                self._apply(command)
            except FoldError as exc:
                # A known event whose payload cannot be mapped: terminal skip.
                # No partial row was written — folds validate before writing.
                _log.warning(
                    "consumer_message_skipped",
                    event_id=command.event_id,
                    event_type=command.event_type,
                    error="invalid_envelope",
                    reason=str(exc),
                )
                await self._commit()
                return
            except Exception as exc:
                # ClickHouse outage: back off and retry the same message. Never
                # commit past it; the type only — driver text can embed row data.
                failures += 1
                _log.warning(
                    "consumer_projection_failed",
                    event_id=command.event_id,
                    event_type=command.event_type,
                    error=type(exc).__name__,
                )
                await self._pause(self._backoff_seconds(failures))
                continue
            await self._commit()
            return
        # Stopped before the write became durable: the offset stays put.

    def _apply(self, command: FoldCommand) -> None:
        """Apply one command's fold to the projection store (synchronous I/O).

        Every command loads the employee's folded dimension state first, then
        the entity's prior row for facts. `apply_dimension_fold` also executes
        a transfer's bounded re-stamp after the interval upserts (ADR 0019
        decision 5); a fact fold writes only when it produced a row (a
        withdrawal with no prior history produces none).
        """
        writer = self._writer
        state = writer.load_dimensions(command.tenant_id, command.employee_id)

        if command.kind is FoldKind.DIMENSION:
            if command.event_type == "employee.created":
                fold = fold_employee_created(command.envelope, prior=state.employee)
            elif command.event_type == "employee.transferred":
                fold = fold_employee_transferred(
                    command.envelope,
                    prior=state.employee,
                    assignments=state.assignments,
                )
            else:
                fold = fold_employee_terminated(
                    command.envelope,
                    prior=state.employee,
                    assignments=state.assignments,
                )
            writer.apply_dimension_fold(fold)
            return

        entity_id = command.entity_id
        if entity_id is None:  # pragma: no cover - mapping guarantees it
            raise FoldError("entity id is missing")

        if command.kind is FoldKind.OVERTIME:
            fact = fold_overtime(
                command.event_type,
                command.envelope,
                prior=writer.get_overtime(command.tenant_id, entity_id),
                dimensions=state,
            )
            if fact is not None:
                writer.upsert_overtime(fact)
            return

        if command.kind is FoldKind.ATTENDANCE:
            attendance = fold_attendance(
                command.event_type,
                command.envelope,
                prior=writer.get_attendance(command.tenant_id, entity_id),
                dimensions=state,
            )
            if attendance is not None:
                writer.upsert_attendance(attendance)
            return

        leave = fold_leave(
            command.event_type,
            command.envelope,
            prior=writer.get_leave(command.tenant_id, entity_id),
            dimensions=state,
        )
        if leave is not None:
            writer.upsert_leave(leave)

    async def _commit(self) -> None:
        """Advance the offset past the handled/skipped message (failures retry)."""
        try:
            await self._source.commit()
        except Exception as exc:
            # The offset simply stays put; the rows already written make the
            # redelivery a convergent no-op — at-least-once from Kafka.
            _log.warning("consumer_commit_failed", error=type(exc).__name__)

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


__all__ = ["EventSource", "KafkaMessage", "ReportsConsumer"]
