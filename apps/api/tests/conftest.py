"""Repository-wide test configuration — loaded before any test module.

Sets `DATABASE_URL` from `DATABASE_TEST_URL`, `REDIS_URL` from
`REDIS_TEST_URL` and `KAFKA_BOOTSTRAP_SERVERS` from
`KAFKA_TEST_BOOTSTRAP_SERVERS` BEFORE any `app.*` import so the lazily-created
clients always target the throwaway services (`postgres-test`, :5433;
`redis-test`, :6380; `kafka-test`, :9093), regardless of which test directory
is collected first. Tests must never touch the dev database, Redis or Kafka.
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
