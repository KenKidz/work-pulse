"""Synchronous ClickHouse client, created lazily from settings.

Mirrors `infrastructure/postgres/engine.py` and `infrastructure/redis/client.py`:
lazy creation keeps `app.main` importable without a reachable ClickHouse (unit
tests, OpenAPI export), and one client serves the process. `clickhouse-connect`
speaks the HTTP interface to the Phase-4 analytical store (ADR 0019).

The client is synchronous by design. Async callers (the API's startup schema
check) run it via `anyio.to_thread` so a slow analytics store never blocks the
event loop (ADR 0006 / ADR 0019 decision 12).
"""

from clickhouse_connect import get_client
from clickhouse_connect.driver.client import Client

from app.config import get_settings

_client: Client | None = None


def create_clickhouse_client(
    *,
    connect_timeout: int = 2,
    send_receive_timeout: int = 30,
    query_retries: int = 2,
) -> Client:
    """Create a client from settings. The caller owns its lifetime.

    The report path uses the defaults (a 30s read timeout is ample for an
    aggregate query). The API's best-effort startup schema check passes much
    tighter values so a half-up ClickHouse cannot hold boot open.
    """
    return get_client(
        dsn=get_settings().clickhouse_url,
        connect_timeout=connect_timeout,
        send_receive_timeout=send_receive_timeout,
        query_retries=query_retries,
        client_name="workpulse",
    )


def get_clickhouse_client() -> Client:
    """Return the process-wide client, creating it on first use.

    A short connect timeout is deliberate: the analytics store is optional for
    the write path, and an unreachable ClickHouse must fail fast (report reads
    answer `503 reports_unavailable`) rather than stall startup.
    """
    global _client
    if _client is None:
        _client = create_clickhouse_client()
    return _client


def dispose_clickhouse() -> None:
    """Close the lazily-created client (clean shutdown and tests)."""
    global _client
    if _client is not None:
        _client.close()
    _client = None
