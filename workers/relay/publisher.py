"""Kafka adapter for the relay's `EnvelopePublisher` port (ADR 0012).

`start()` warms up the connection — it ensures the single `workpulse.events`
topic exists (admin create, ignore-exists — 1 partition / replication factor 1
in v1; broker auto-create is the backstop) and starts the producer — but it is
**never fatal**: a broker that is down at startup must not stop the relay
(the write path never depends on the bus). Topic creation also pins
`retention.ms` to `REPORT_RETENTION_MS`, the 24-month maximum report span plus
a margin, because the reports projection rebuilds by replaying `earliest`
within that window (ADR 0019 decision 6). `publish()` connects lazily
(therefore also retries after a broker restart) and sends one stored envelope
**verbatim**: key = UTF-8 `data.employee_id` when present, otherwise
`data.run_id` for run-level events (ADR 0012 and its 2026-10-21 amendment —
one employee's transitions land on one partition, in publish order), value =
`json.dumps(envelope)` as UTF-8 (semantic, not byte-wise, verbatim — `jsonb`
may normalize object key order; `event_id`/`occurred_at` are never
re-stamped), no headers in v1. It returns only after the broker acks
(`send_and_wait`) — at-least-once rests on that. Recoverable broker failures
propagate to the relay, which bookkeeps them per row (`attempts`,
`last_error`) and retries.
"""

import json
from typing import Any

from aiokafka import AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import KafkaError, TopicAlreadyExistsError

from app.shared.logging import get_logger
from app.shared.outbox import kafka_message_key

_log = get_logger("relay.publisher")

#: Topic retention the reports projection depends on (ADR 0019 decision 6).
#: Reports accept a range of at most 24 calendar months; 26 months of 31 days
#: leaves a margin for month-boundary spans and relay/consumer lag. A rebuild
#: replays `earliest` within this window (the `event_outbox` rows are the
#: documented re-publish source once the topic has aged past it).
REPORT_RETENTION_MS = 26 * 31 * 24 * 60 * 60 * 1000


class KafkaEnvelopePublisher:
    """`start()` warms up; `publish()` connects lazily and waits for the ack."""

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str,
        partitions: int = 1,
        retention_ms: int = REPORT_RETENTION_MS,
        request_timeout_ms: int = 40_000,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._partitions = partitions
        self._retention_ms = retention_ms
        self._request_timeout_ms = request_timeout_ms
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        """Ensure the topic + start the producer; broker-down is tolerated.

        A broker that is unreachable at startup keeps the relay running: rows
        keep committing, every publish attempt retries the connection, and the
        per-row failure is bookkept by the relay (`attempts`, `last_error`).
        """
        try:
            await self._connect()
        except KafkaError as exc:
            # Infrastructure error text only — never payload contents.
            _log.warning(
                "broker_unavailable_at_startup",
                bootstrap_servers=self._bootstrap_servers,
                error=type(exc).__name__,
            )

    async def publish(self, envelope: dict[str, Any]) -> None:
        """Publish one stored envelope; returns after the broker acks."""
        producer = await self._connect()
        await producer.send_and_wait(
            self._topic,
            key=kafka_message_key(envelope).encode("utf-8"),
            value=json.dumps(envelope).encode("utf-8"),
        )

    async def stop(self) -> None:
        producer, self._producer = self._producer, None
        if producer is not None:
            await producer.stop()

    async def _connect(self) -> AIOKafkaProducer:
        """Connect on first use; a live producer is reused as-is."""
        if self._producer is None:
            await self._ensure_topic()
            producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap_servers,
                request_timeout_ms=self._request_timeout_ms,
            )
            await producer.start()
            self._producer = producer
        return self._producer

    async def _ensure_topic(self) -> None:
        # The relay owns provisioning (ADR 0012 decision 9); create is
        # idempotent by contract, an existing topic is the steady state.
        admin = AIOKafkaAdminClient(
            bootstrap_servers=self._bootstrap_servers,
            request_timeout_ms=self._request_timeout_ms,
        )
        await admin.start()
        try:
            try:
                await admin.create_topics(
                    [
                        NewTopic(
                            name=self._topic,
                            num_partitions=self._partitions,
                            replication_factor=1,
                            # Reports replay the retained stream (ADR 0019);
                            # pin retention at creation. An existing topic is
                            # the steady state and keeps its own config — ops
                            # verifies the live value.
                            topic_configs={"retention.ms": str(self._retention_ms)},
                        )
                    ]
                )
            except TopicAlreadyExistsError:
                pass
        finally:
            await admin.close()
