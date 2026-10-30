"""Repository-wide test configuration — loaded before any test module.

Sets `DATABASE_URL` from `DATABASE_TEST_URL`, `REDIS_URL` from
`REDIS_TEST_URL`, `KAFKA_BOOTSTRAP_SERVERS` from
`KAFKA_TEST_BOOTSTRAP_SERVERS`, `CLICKHOUSE_URL` from `CLICKHOUSE_TEST_URL`,
the `STORAGE_*` keys from their `STORAGE_TEST_*` twins and disables the worker
Prometheus listeners (`*_METRICS_PORT=0`) BEFORE any `app.*`
import so the lazily-created clients always target the throwaway services
(`postgres-test`, :5433; `redis-test`, :6380; `kafka-test`, :9093;
`clickhouse-test`, :8124; `minio-test`, :9002), regardless of which test
directory is collected first. Tests must never touch the dev database, Redis,
Kafka, ClickHouse or the dev document bucket.
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

# The CDC sink targets the D2 shadow database. Tests pin it to the throwaway
# twin (created by the sink/writer on startup) so a test never writes the
# canonical projection the reports fold owns.
os.environ["CDC_PROJECTION_DATABASE"] = os.environ.get(
    "CDC_TEST_PROJECTION_DATABASE", "workpulse_test_cdc"
)

# And the document bucket: `STORAGE_ENDPOINT` et al. point at the dev MinIO on
# :9000 otherwise. Credentials and bucket are the `minio-test` provisioning
# (ADR 0022); TTLs may keep their defaults.
_DEFAULT_TEST_STORAGE_ENDPOINT = "http://127.0.0.1:9002"
os.environ["STORAGE_ENDPOINT"] = os.environ.get(
    "STORAGE_TEST_ENDPOINT", _DEFAULT_TEST_STORAGE_ENDPOINT
)
os.environ["STORAGE_ACCESS_KEY"] = os.environ.get(
    "STORAGE_TEST_ACCESS_KEY", "workpulse"
)
os.environ["STORAGE_SECRET_KEY"] = os.environ.get(
    "STORAGE_TEST_SECRET_KEY", "workpulse"
)
os.environ["STORAGE_BUCKET"] = os.environ.get("STORAGE_TEST_BUCKET", "documents-test")

# Worker Prometheus listeners bind loopback ports in dev; under pytest they
# must stay disabled so parallel runs never collide (ADR 0023).
for _worker in ("relay", "realtime", "notifications", "payroll", "reports", "cdc_sink"):
    os.environ[f"{_worker.upper()}_METRICS_PORT"] = "0"
