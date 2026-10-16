"""Realtime consumer entry point — `python -m workers.realtime.main` (ADR 0013).

Runs inside the `apps/api` uv environment (`make dev-realtime`), reading the
same settings/`.env` as the API. SIGINT/SIGTERM ask the consumer to stop
gracefully: the in-flight message finishes (publish attempted, offset
committed), everything else stays unconsumed for the next run. Broker hiccups
are the loop's to handle (ids-only log + backoff; failures degrade the wall's
latency only — the write path never touches the bus); only configuration
errors exit loudly for a supervisor to act on — restart policy is Phase 8's.
"""

import asyncio
import signal

from aiokafka import AIOKafkaConsumer

from app.config import get_settings
from app.infrastructure.redis import RedisPresencePublisher, dispose_redis, get_redis
from app.shared.logging import configure_logging, get_logger
from workers.realtime.consumer import RealtimeConsumer

_log = get_logger("realtime.main")


async def _run() -> None:
    settings = get_settings()
    configure_logging(settings.environment)

    source = AIOKafkaConsumer(
        settings.kafka_events_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.realtime_consumer_group,
        # The wall shows "what changed now", never history: a first start
        # lands at the topic tip; offsets advance manually, one handled
        # message at a time (ADR 0013).
        auto_offset_reset="latest",
        enable_auto_commit=False,
    )
    consumer = RealtimeConsumer(
        source,
        RedisPresencePublisher(get_redis()),
        poll_seconds=settings.realtime_poll_seconds,
        backoff_base=settings.realtime_backoff_base_seconds,
        backoff_max=settings.realtime_backoff_max_seconds,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, consumer.stop)

    await source.start()
    run_task = asyncio.create_task(consumer.run())
    started_task = asyncio.create_task(consumer.started.wait())
    try:
        # Readiness is the first completed fetch round — group joined, the
        # `latest` position pinned — never a sleep (T4/R6). The gate's
        # log-line wait keys on the line below; a stop arriving first skips it.
        await asyncio.wait(
            {run_task, started_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if consumer.started.is_set():
            _log.info(
                "realtime_consumer_started",
                bootstrap_servers=settings.kafka_bootstrap_servers,
                topic=settings.kafka_events_topic,
                group_id=settings.realtime_consumer_group,
            )
        await run_task
    finally:
        started_task.cancel()
        await source.stop()
        await dispose_redis()
        _log.info("realtime_consumer_stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
