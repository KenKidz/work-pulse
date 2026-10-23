"""Repository-wide test configuration — loaded before any test module.

Sets `DATABASE_URL` from `DATABASE_TEST_URL`, `REDIS_URL` from
`REDIS_TEST_URL`, `KAFKA_BOOTSTRAP_SERVERS` from
`KAFKA_TEST_BOOTSTRAP_SERVERS` and `CLICKHOUSE_URL` from `CLICKHOUSE_TEST_URL`
BEFORE any `app.*` import so the lazily-created clients always target the
throwaway services (`postgres-test`, :5433; `redis-test`, :6380;
`kafka-test`, :9093; `clickhouse-test`, :8124), regardless of which test
directory is collected first. Tests must never touch the dev database, Redis,
Kafka or ClickHouse.
"""

import os

_DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://workpulse:workpulse@localhost:5433/workpulse_test"
)
TEST_DATABASE_URL = os.environ.get("DATABASE_TEST_URL", _DEFAULT_TEST_DATABASE_URL)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

_DEFAULT_TEST_REDIS_URL = "redis://localhost:6380/0"
TEST_REDIS_URL = os.environ.get("REDIS_TEST_URL", _DEFAULT_TEST_REDIS_URL)
os.environ["REDIS_URL"] = TEST_REDIS_URL

# The env override wins over the `.env` files (which point at the dev broker).
_DEFAULT_TEST_KAFKA_BOOTSTRAP_SERVERS = "localhost:9093"
TEST_KAFKA_BOOTSTRAP_SERVERS = os.environ.get(
    "KAFKA_TEST_BOOTSTRAP_SERVERS", _DEFAULT_TEST_KAFKA_BOOTSTRAP_SERVERS
)
os.environ["KAFKA_BOOTSTRAP_SERVERS"] = TEST_KAFKA_BOOTSTRAP_SERVERS

# Same rule for the analytics store: the env override wins over `.env`, which
# points at the dev ClickHouse on :8123.
_DEFAULT_TEST_CLICKHOUSE_URL = (
    "http://workpulse:workpulse@127.0.0.1:8124/workpulse_test"
)
TEST_CLICKHOUSE_URL = os.environ.get(
    "CLICKHOUSE_TEST_URL", _DEFAULT_TEST_CLICKHOUSE_URL
)
os.environ["CLICKHOUSE_URL"] = TEST_CLICKHOUSE_URL
