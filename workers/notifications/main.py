"""Notification consumer entry point — `python -m workers.notifications.main`.

Runs inside the `apps/api` uv environment (`make dev-notifications`), reading
the same settings/`.env` as the API. Each message opens a fresh database session
that carries the read-only recipient directory and the delivery service; the
Kafka offset commits only after every delivery reaches a terminal state (ADR
0015 decision 3). SIGINT/SIGTERM ask the consumer to stop gracefully: the
in-flight message finishes, everything else stays unconsumed for the next run.
Broker, database and provider hiccups are the loop's to handle (ids-only log +
backoff); only configuration errors exit loudly for a supervisor to act on —
restart policy is Phase 8's.
"""

import asyncio
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiokafka import AIOKafkaConsumer

from app.config import get_settings
from app.domains.notifications.service import NotificationService
from app.infrastructure.notifications.resend_email import ResendEmailSender
from app.infrastructure.notifications.slack_web import SlackWebSender
from app.infrastructure.postgres.engine import dispose_engine, get_session_factory
from app.infrastructure.postgres.recipient_directory import PostgresRecipientDirectory
from app.infrastructure.postgres.repositories.notifications import (
    PostgresNotificationRepository,
)
from app.shared.logging import configure_logging, get_logger
from workers.notifications.consumer import NotificationsConsumer, NotificationUnit

_log = get_logger("notifications.main")


async def _run() -> None:
    settings = get_settings()
    configure_logging(settings.environment)

    session_factory = get_session_factory()
    # Provider identities are built once: both SDKs are stateless wrappers and
    # an unset credential is a per-delivery `skipped(not_configured)`, never a
    # startup failure (ADR 0015 decision 5).
    email_sender = ResendEmailSender(
        api_key=settings.resend_api_key,
        from_address=settings.resend_from_address,
    )
    slack_sender = SlackWebSender(
        bot_token=settings.slack_bot_token,
        channel_id=settings.slack_channel_id,
    )

    @asynccontextmanager
    async def unit() -> AsyncIterator[NotificationUnit]:
        """One message's session: directory reads + delivery service, scoped."""
        async with session_factory() as session:
            yield NotificationUnit(
                directory=PostgresRecipientDirectory(session),
                service=NotificationService(
                    repo=PostgresNotificationRepository(session),
                    email_sender=email_sender,
                    slack_sender=slack_sender,
                    commit=session.commit,
                    max_attempts=settings.notifications_send_max_attempts,
                    backoff_base_seconds=(
                        settings.notifications_send_backoff_base_seconds
                    ),
                ),
            )

    source = AIOKafkaConsumer(
        settings.kafka_events_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.notifications_consumer_group,
        # Notifications describe "what changed now": a first start lands at the
        # topic tip, never a history replay. Offsets advance manually, only
        # after a message's deliveries are terminal (ADR 0015 decision 3).
        auto_offset_reset="latest",
        enable_auto_commit=False,
    )
    consumer = NotificationsConsumer(
        source,
        unit,
        poll_seconds=settings.notifications_poll_seconds,
        backoff_base=settings.notifications_backoff_base_seconds,
        backoff_max=settings.notifications_backoff_max_seconds,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, consumer.stop)

    await source.start()
    run_task = asyncio.create_task(consumer.run())
    started_task = asyncio.create_task(consumer.started.wait())
    try:
        # Readiness is the first completed fetch round — group joined, the
        # `latest` position pinned — never a sleep (ADR 0013 decision 4). A
        # stop arriving first simply skips the line.
        await asyncio.wait(
            {run_task, started_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if consumer.started.is_set():
            _log.info(
                "notifications_consumer_started",
                bootstrap_servers=settings.kafka_bootstrap_servers,
                topic=settings.kafka_events_topic,
                group_id=settings.notifications_consumer_group,
            )
        await run_task
    finally:
        started_task.cancel()
        await source.stop()
        await dispose_engine()
        _log.info("notifications_consumer_stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
