"""CDC projection sink entry point — `python -m workers.cdc_sink.main`.

Runs inside the `apps/api` uv environment (`make dev-cdc-sink`), reading the
same settings/`.env` as the API. The worker is the CDC landing layer's only
writer: it applies the idempotent ClickHouse DDL to its target database
(strictly — it cannot project without tables), warms the leave-type reference
dictionary from the capture stream, and consumes the six
`workpulse.cdc.<table>` topics plus Debezium's heartbeat topic from `earliest`
with the stable `workpulse-cdc-sink` group (ADR 0024 D1/D7).

SIGINT/SIGTERM ask the consumer to stop gracefully: the in-flight change
finishes (map, write, commit); everything else stays unconsumed for the next
run. Broker hiccups are the loop's to handle (ids-only log + backoff); a
ClickHouse outage is retried head-of-line with the offset held. Only
configuration/schema errors exit loudly — restart policy is Phase 8's.
"""

import asyncio
import contextlib
import signal
from collections.abc import Sequence
from typing import Protocol

from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.errors import UnknownTopicOrPartitionError

from app.config import Settings, get_settings
from app.infrastructure.clickhouse.cdc_projection import ClickhouseCdcWriter
from app.shared.boot import validate_boot_configuration
from app.shared.logging import configure_logging, get_logger
from app.shared.telemetry import (
    configure_telemetry,
    flush_worker_telemetry,
    start_worker_metrics_server,
)
from workers.cdc_sink.consumer import CdcSinkConsumer
from workers.cdc_sink.health import (
    CdcHealthPoller,
    fetch_connector_state,
    fetch_consumer_lag,
    fetch_slot_lag_bytes,
)
from workers.cdc_sink.mapping import (
    DATA_TOPICS,
    HEARTBEAT_TOPIC,
    LEAVE_TYPE_TOPIC,
    Change,
    LeaveTypeBook,
    decode_leave_type,
    parse_message,
)

_log = get_logger("cdc_sink.main")


class StartableSource(Protocol):
    """The `start()` slice of `AIOKafkaConsumer` the startup wait needs."""

    async def start(self) -> None: ...


async def _start_source_with_retry(
    source: StartableSource,
    *,
    backoff_base: float,
    backoff_max: float,
) -> None:
    """Start the consumer, waiting for the connector to create the topics.

    `AIOKafkaConsumer.start()` waits on metadata for every subscribed topic
    and raises `UnknownTopicOrPartitionError` after the request timeout when
    the broker disables auto-topic-creation. Sink-first startup is documented
    and supported: retry with bounded backoff instead of killing the worker;
    the connector creates the seven topics when it registers. A broker that is
    truly down raises a different error and fails the worker loudly.
    """
    failures = 0
    while True:
        try:
            await source.start()
            return
        except UnknownTopicOrPartitionError:
            failures += 1
            delay = min(
                backoff_base * 2 ** min(failures - 1, 6),
                backoff_max,
            )
            _log.warning(
                "cdc_sink_topics_not_ready",
                attempt=failures,
                retry_in_seconds=delay,
            )
            await asyncio.sleep(delay)


async def _is_fresh_group(source: AIOKafkaConsumer, topics: Sequence[str]) -> bool:
    """Whether the stable group has no committed offsets on any data topic.

    A fresh group is fenced before its first fetch (D5). A metadata/coordinator
    hiccup is treated as "not fresh": re-fencing a healthy projection on a
    transient error would serve `503`s until the next snapshot evidence, while
    `ops/cdc/register.sh` already fences a brand-new connector. The consumer
    still notices a `ready = 0` fence on its first evidence message.
    """
    try:
        for topic in topics:
            committed = await source.committed(TopicPartition(topic, 0))
            if committed is not None:
                return False
        return True
    except Exception as exc:  # noqa: BLE001 - conservative fallback by contract
        _log.warning("cdc_sink_offset_check_failed", error=type(exc).__name__)
        return False


async def _warm_leave_types(
    settings: Settings, book: LeaveTypeBook, topic: str
) -> None:
    """Replay the leave-types topic so a restarted sink starts with a warm book.

    The topic is tiny (seeded reference rows) and the replay is bounded by its
    current end offset. A missing topic is normal before the connector's first
    snapshot and simply leaves the book empty; the snapshot fills it.
    """
    consumer = AIOKafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=None,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    partition = TopicPartition(topic, 0)
    await consumer.start()
    try:
        consumer.assign([partition])
        await consumer.seek_to_beginning(partition)
        end = (await consumer.end_offsets([partition]))[partition]
        while True:
            position = await consumer.position(partition)
            if position >= end:
                return
            batches = await consumer.getmany(timeout_ms=200)
            for messages in batches.values():
                for message in messages:
                    event = parse_message(message.value)
                    if (
                        isinstance(event, Change)
                        and event.table == "leave_types"
                        and not event.dropped
                        and event.after is not None
                    ):
                        book.apply(decode_leave_type(event.after))
    except Exception as exc:  # noqa: BLE001 - warm-up is best-effort
        _log.warning("cdc_sink_leave_type_warmup_failed", error=type(exc).__name__)
    finally:
        await consumer.stop()


async def _run() -> None:
    settings = get_settings()
    configure_logging(settings.environment)
    configure_telemetry(settings)

    writer = ClickhouseCdcWriter.connect(settings.cdc_projection_database)
    try:
        # The sink cannot project without its tables: a failure here stops the
        # worker loudly (ADR 0019 decision 11's worker-side contract).
        writer.apply_schema()

        leave_types = LeaveTypeBook()
        source = AIOKafkaConsumer(
            *DATA_TOPICS,
            HEARTBEAT_TOPIC,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.cdc_sink_consumer_group,
            # A first start replays the retained capture stream; restarts
            # resume committed offsets. The connector's snapshot covers
            # pre-existing rows (D5).
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        await _start_source_with_retry(
            source,
            backoff_base=settings.cdc_backoff_base_seconds,
            backoff_max=settings.cdc_backoff_max_seconds,
        )
        fresh_group = await _is_fresh_group(source, DATA_TOPICS)
        await _warm_leave_types(settings, leave_types, LEAVE_TYPE_TOPIC)

        consumer = CdcSinkConsumer(
            source,
            writer,
            poll_seconds=settings.cdc_poll_seconds,
            backoff_base=settings.cdc_backoff_base_seconds,
            backoff_max=settings.cdc_backoff_max_seconds,
            fresh_group=fresh_group,
            leave_types=leave_types,
        )

        health = CdcHealthPoller(
            connector_name=settings.cdc_connector_name,
            slot_name=settings.cdc_slot_name,
            topics=DATA_TOPICS,
            fetch_connector_state=lambda: fetch_connector_state(
                settings.cdc_connect_url, settings.cdc_connector_name
            ),
            fetch_slot_lag=lambda: fetch_slot_lag_bytes(settings.cdc_slot_name),
            fetch_consumer_lag=lambda: fetch_consumer_lag(source, DATA_TOPICS),
            poll_seconds=settings.cdc_health_poll_seconds,
        )

        def _stop() -> None:
            consumer.stop()
            health.stop()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _stop)

        metrics_server = start_worker_metrics_server(settings.cdc_sink_metrics_port)
        health_task = asyncio.create_task(health.run())
        run_task = asyncio.create_task(consumer.run())
        started_task = asyncio.create_task(consumer.started.wait())
        try:
            # Readiness is the first completed fetch round — group joined and
            # the `earliest` position pinned — never a sleep (ADR 0013
            # decision 4). A stop arriving first simply skips the line.
            await asyncio.wait(
                {run_task, started_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if consumer.started.is_set():
                _log.info(
                    "cdc_sink_started",
                    bootstrap_servers=settings.kafka_bootstrap_servers,
                    topics=len(DATA_TOPICS),
                    group_id=settings.cdc_sink_consumer_group,
                    database=settings.cdc_projection_database or "canonical",
                    leave_types=len(leave_types),
                )
            await run_task
        finally:
            started_task.cancel()
            health.stop()
            health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await health_task
            if metrics_server is not None:
                metrics_server.stop()
            await source.stop()
            flush_worker_telemetry()
            _log.info("cdc_sink_stopped")
    finally:
        writer.close()


def main() -> None:
    validate_boot_configuration()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
