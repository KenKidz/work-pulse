"""Redis pub/sub presence subscriber.

Each realtime connection subscribes to its own tenant channel on upgrade and
consumes facts published by any API process. The subscription is torn down
with the generator (client disconnect, server shutdown), best-effort — Redis
errors during cleanup must not mask the reason the stream ended.
"""

from collections.abc import AsyncGenerator
from contextlib import suppress
from uuid import UUID

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.infrastructure.redis.presence import tenant_channel
from app.shared.logging import get_logger
from app.shared.presence import PresenceFact

_log = get_logger("realtime.stream")


class RedisPresenceStream:
    """Subscribes to a tenant's channel and yields decoded `PresenceFact`s."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def subscribe(self, tenant_id: UUID) -> AsyncGenerator[PresenceFact, None]:
        channel = tenant_channel(tenant_id)
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            while True:
                # Bounded read: `listen()` blocks with `math.inf`, bypassing
                # socket timeouts, so a silent Redis would park the
                # subscription forever — neither yielding nor raising.
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message is None:
                    continue
                payload = message.get("data")
                if not isinstance(payload, (bytes, str)):
                    continue
                try:
                    fact = PresenceFact.model_validate_json(payload)
                except ValidationError:
                    # A mixed-version worker may publish a fact this reader
                    # does not know yet (a new reason/status). One unreadable
                    # message must never tear down the subscription; the next
                    # snapshot resync is the recovery path for the missed row.
                    _log.warning("presence_fact_skipped", error="invalid_payload")
                    continue
                if fact.tenant_id != tenant_id:
                    continue  # bind the payload to the channel it arrived on
                yield fact
        finally:
            with suppress(RedisError):
                await pubsub.unsubscribe(channel)
            with suppress(RedisError):
                await pubsub.aclose()
