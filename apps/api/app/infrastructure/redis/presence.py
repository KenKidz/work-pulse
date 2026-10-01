"""Redis pub/sub presence publisher (Phase 2 fan-out).

One channel per tenant (`realtime:tenant:{tenant_id}`), so every API process
feeding the bus reaches every viewer — an in-process bus would not.

Publishing is best-effort by contract: a check-in must never fail because
Redis is down. Failures are logged with ids and the error type only — no PII
(`.ai/security.md`) — and swallowed. Delivery is transient/at-most-once;
missed deltas are recovered by the client resyncing a snapshot.
"""

from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.shared.logging import get_logger
from app.shared.presence import PresenceFact

_log = get_logger("realtime.presence")


def tenant_channel(tenant_id: UUID) -> str:
    """The per-tenant fan-out channel (pinned in the feature spec)."""
    return f"realtime:tenant:{tenant_id}"


class RedisPresencePublisher:
    """Publishes presence facts to the tenant channel. Never raises."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, fact: PresenceFact) -> None:
        try:
            await self._redis.publish(
                tenant_channel(fact.tenant_id), fact.model_dump_json()
            )
        except RedisError as exc:
            _log.warning(
                "presence_publish_failed",
                tenant_id=str(fact.tenant_id),
                employee_id=str(fact.employee_id),
                error=type(exc).__name__,
            )
