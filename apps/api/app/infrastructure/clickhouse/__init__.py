"""ClickHouse infrastructure: the Phase-4 analytical store (ADR 0019).

Wired at the edges (the CDC sink writer and the reports repository). Domains
never import this package directly — they depend on repository protocols.
"""

from app.infrastructure.clickhouse.client import (
    create_clickhouse_client,
    dispose_clickhouse,
    get_clickhouse_client,
)
from app.infrastructure.clickhouse.schema import (
    PROJECTION_STATE_TABLE,
    PROJECTION_TABLES,
    SCHEMA_PATH,
    SCHEMA_TABLES,
    apply_clickhouse_schema,
    ensure_clickhouse_schema_best_effort,
    read_schema_statements,
)

__all__ = [
    "PROJECTION_STATE_TABLE",
    "PROJECTION_TABLES",
    "SCHEMA_PATH",
    "SCHEMA_TABLES",
    "apply_clickhouse_schema",
    "create_clickhouse_client",
    "dispose_clickhouse",
    "ensure_clickhouse_schema_best_effort",
    "get_clickhouse_client",
    "read_schema_statements",
]
