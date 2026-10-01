"""Redis infrastructure: async client + presence pub/sub adapters.

Wired at the edges (FastAPI dependencies, the realtime hub). Domains never
import this package directly — they depend on the `app.shared.presence` ports.
"""

from app.infrastructure.redis.client import dispose_redis, get_redis
from app.infrastructure.redis.presence import RedisPresencePublisher, tenant_channel
from app.infrastructure.redis.streams import RedisPresenceStream

__all__ = [
    "RedisPresencePublisher",
    "RedisPresenceStream",
    "dispose_redis",
    "get_redis",
    "tenant_channel",
]
