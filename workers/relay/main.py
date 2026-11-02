"""Relay entry point — `python -m workers.relay.main` (ADR 0012 decision 10).

Runs inside the `apps/api` uv environment (`make dev-relay`), reading the same
settings/`.env` as the API. SIGINT/SIGTERM ask the relay to stop gracefully:
the in-flight row finishes, everything else stays unpublished for the next
run. Broker and database hiccups — including either being down at startup —
are the loop's to handle (lazy connect + backoff; failures are bookkept per
row); only configuration errors (bad settings, unreadable `.env`) exit loudly
for a supervisor to act on — restart policy is Phase 8's.
"""

import asyncio
import signal
from datetime import UTC, datetime

from app.config import get_settings
from app.infrastructure.postgres.engine import dispose_engine, get_session_factory
from app.infrastructure.postgres.outbox import PostgresOutboxRepository
from app.shared.circuit_breaker import CircuitBreaker
from app.shared.logging import configure_logging, get_logger
from app.shared.telemetry import (
    configure_telemetry,
    flush_worker_telemetry,
    start_worker_metrics_server,
)
from workers.relay.publisher import KafkaEnvelopePublisher
from workers.relay.relay import OutboxRelay

_log = get_logger("relay.main")


def _utc_now() -> datetime:
    """The relay's injected clock (time is a parameter, `.ai/conventions.md`)."""
    return datetime.now(UTC)


async def _run() -> None:
    settings = get_settings()
    configure_logging(settings.environment)
    configure_telemetry(settings)

    publisher = KafkaEnvelopePublisher(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        topic=settings.kafka_events_topic,
        partitions=settings.kafka_events_topic_partitions,
        breaker=CircuitBreaker.from_settings("kafka", settings),
    )
    relay = OutboxRelay(
        get_session_factory(),
        PostgresOutboxRepository,
        publisher,
        batch_size=settings.relay_batch_size,
        poll_seconds=settings.relay_poll_seconds,
        backoff_base=settings.relay_backoff_base_seconds,
        backoff_max=settings.relay_backoff_max_seconds,
        clock=_utc_now,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, relay.stop)

    await publisher.start()
    metrics_server = start_worker_metrics_server(settings.relay_metrics_port)
    _log.info(
        "relay_started",
        bootstrap_servers=settings.kafka_bootstrap_servers,
        topic=settings.kafka_events_topic,
        batch_size=settings.relay_batch_size,
    )
    try:
        await relay.run()
    finally:
        if metrics_server is not None:
            metrics_server.stop()
        await publisher.stop()
        await dispose_engine()
        flush_worker_telemetry()
        _log.info("relay_stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
