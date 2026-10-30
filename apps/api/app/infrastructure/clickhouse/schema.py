"""Idempotent ClickHouse DDL for the Analytics & Reports projection (ADR 0019/0024).

The SQL lives in `database/clickhouse/schema.sql` so ops can read and run the
exact same statements outside the app. The CDC sink applies it strictly at
startup (it cannot project without tables); the API applies it best-effort so
ClickHouse being down never blocks boot — report reads then answer `503
reports_unavailable` (ADR 0019 decision 11, ADR 0024).
"""

from pathlib import Path

from clickhouse_connect.driver.client import Client

from app.infrastructure.clickhouse.client import (
    create_clickhouse_client,
    get_clickhouse_client,
)
from app.shared.logging import get_logger

_log = get_logger("clickhouse.schema")

# Startup is best-effort: keep each request short and disable retries so a
# half-up ClickHouse cannot hold the API's lifespan open for minutes. The
# report read path keeps the client defaults (see `client.py`).
_STARTUP_CONNECT_TIMEOUT_SECONDS = 1
_STARTUP_SEND_RECEIVE_TIMEOUT_SECONDS = 2
_STARTUP_QUERY_RETRIES = 0

# `apps/api/app/infrastructure/clickhouse/schema.py` → repo root.
_REPO_ROOT = Path(__file__).resolve().parents[5]
SCHEMA_PATH = _REPO_ROOT / "database" / "clickhouse" / "schema.sql"

#: The five current-state tables the rebuild procedure truncates (ADR 0019).
#: `projection_state` is deliberately not here: the rebuild fence must survive
#: the truncate (`ready = 0`) until the replay catches up.
PROJECTION_TABLES = (
    "dim_employee",
    "dim_assignment",
    "fact_overtime",
    "fact_attendance",
    "fact_leave",
)
PROJECTION_STATE_TABLE = "projection_state"
#: Everything the DDL creates — the schema smoke test's expected set.
SCHEMA_TABLES = (*PROJECTION_TABLES, PROJECTION_STATE_TABLE)


def read_schema_statements(path: Path = SCHEMA_PATH) -> list[str]:
    """Split the DDL file into single statements.

    ClickHouse's HTTP interface executes one statement per request, so the
    file's `;`-separated statements are applied individually. Full-line `--`
    comments are dropped; the DDL deliberately contains no semicolons inside
    string literals.
    """
    source = path.read_text(encoding="utf-8")
    without_comments = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("--")
    )
    return [part.strip() for part in without_comments.split(";") if part.strip()]


def apply_clickhouse_schema(
    client: Client | None = None, *, path: Path = SCHEMA_PATH
) -> None:
    """Apply the projection DDL. Every statement is `IF NOT EXISTS`, so
    repeated application converges instead of failing."""
    conn = client if client is not None else get_clickhouse_client()
    for statement in read_schema_statements(path):
        conn.command(statement)


def ensure_clickhouse_schema_best_effort() -> None:
    """Apply the DDL at API startup without ever failing boot.

    The analytics store is optional for the write path: log the error type only
    and let the first report read surface `reports_unavailable` if it is still
    down. A short-lived, short-timeout client bounds the attempt; the shared
    report client is intentionally not used here.
    """
    client: Client | None = None
    try:
        client = create_clickhouse_client(
            connect_timeout=_STARTUP_CONNECT_TIMEOUT_SECONDS,
            send_receive_timeout=_STARTUP_SEND_RECEIVE_TIMEOUT_SECONDS,
            query_retries=_STARTUP_QUERY_RETRIES,
        )
        apply_clickhouse_schema(client)
    except Exception as exc:  # noqa: BLE001 - best-effort by contract
        _log.warning("clickhouse_schema_unavailable", error=type(exc).__name__)
    finally:
        if client is not None:
            client.close()
