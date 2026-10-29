"""Reports projection worker entry point — `python -m workers.reports.main`.

Runs inside the `apps/api` uv environment (`make dev-reports`), reading the same
settings/`.env` as the API. The worker is the projection's only writer
(ADR 0019 decision 1): it applies the idempotent ClickHouse DDL at startup
(strictly — it cannot project without tables) and consumes `workpulse.events`
from `earliest` with the stable `workpulse-reports` group, so a first start
builds the history the topic still holds and restarts resume committed offsets.

SIGINT/SIGTERM ask the consumer to stop gracefully: the in-flight message
finishes (fold, write, commit); everything else stays unconsumed for the next
run. Broker hiccups are the loop's to handle (ids-only log + backoff); a
ClickHouse outage is retried head-of-line with the offset held, as ADR 0019
decision 6 pins. Only configuration/schema errors exit loudly — restart policy
is Phase 8's.
"""

import asyncio
import signal

from aiokafka import AIOKafkaConsumer

from app.config import get_settings
from app.infrastructure.clickhouse.client import create_clickhouse_client
from app.infrastructure.clickhouse.projection import ClickhouseProjectionWriter
from app.infrastructure.clickhouse.schema import apply_clickhouse_schema
from app.shared.logging import configure_logging, get_logger
from app.shared.telemetry import (
    configure_telemetry,
    flush_worker_telemetry,
    start_worker_metrics_server,
)
from workers.reports.consumer import ReportsConsumer

_log = get_logger("reports.main")


async def _run() -> None:
    settings = get_settings()
    configure_logging(settings.environment)
    configure_telemetry(settings)

    client = create_clickhouse_client()
    try:
        # The worker cannot project without its tables: a failure here stops
        # the worker loudly (ADR 0019 decision 11). The API's startup check is
        # the best-effort counterpart.
        apply_clickhouse_schema(client)

        source = AIOKafkaConsumer(
            settings.kafka_events_topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.reports_consumer_group,
            # Reports are built from the retained event history: a first start
            # replays from `earliest`, restarts resume committed offsets. The
            # relay pins `retention.ms` to cover the maximum report span
            # (ADR 0019 decision 6).
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        consumer = ReportsConsumer(
            source,
            ClickhouseProjectionWriter(client),
            poll_seconds=settings.reports_poll_seconds,
            backoff_base=settings.reports_backoff_base_seconds,
            backoff_max=settings.reports_backoff_max_seconds,
        )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, consumer.stop)

        await source.start()
        metrics_server = start_worker_metrics_server(settings.reports_metrics_port)
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
                    "reports_started",
                    bootstrap_servers=settings.kafka_bootstrap_servers,
                    topic=settings.kafka_events_topic,
                    group_id=settings.reports_consumer_group,
                    clickhouse_url=settings.clickhouse_url,
                )
            await run_task
        finally:
            started_task.cancel()
            if metrics_server is not None:
                metrics_server.stop()
            await source.stop()
            flush_worker_telemetry()
            _log.info("reports_stopped")
    finally:
        client.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
