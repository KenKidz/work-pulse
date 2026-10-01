"""Redis pub/sub presence subscriber.

Each realtime connection subscribes to its own tenant channel on upgrade and
consumes facts published by any API process. The subscription is torn down
with the generator (client disconnect, server shutdown), best-effort — Redis
errors during cleanup must not mask the reason the stream ended.
"""

from collections.abc import AsyncGenerator
from contextlib import suppress
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.infrastructure.redis.presence import tenant_channel
from app.shared.presence import PresenceFact


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
                fact = PresenceFact.model_validate_json(payload)
                if fact.tenant_id != tenant_id:
                    continue  # bind the payload to the channel it arrived on
                yield fact
        finally:
            with suppress(RedisError):
                await pubsub.unsubscribe(channel)
            with suppress(RedisError):
                await pubsub.aclose()
