"""Async Redis client, created lazily from settings.

Mirrors `infrastructure/postgres/engine.py`: lazy creation keeps `app.main`
importable without a reachable Redis (unit tests, OpenAPI export). One client
per process; `dispose_redis()` for clean shutdown and tests.
"""

from redis.asyncio import Redis

from app.config import get_settings

_client: Redis | None = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        # The bus is best-effort: a stalled Redis must fail fast, never
        # stall the write path (redis-py defaults would block ~5s).
        _client = Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=1,
        )
    return _client


async def dispose_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
