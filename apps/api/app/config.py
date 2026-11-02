"""Application settings, loaded from environment variables (and `.env`)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Override any field via an environment variable."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "WorkPulse API"
    version: str = "0.1.0"
    environment: str = "development"

    # Override from the environment with a JSON list, e.g.
    # CORS_ORIGINS='["https://app.workpulse.dev"]'
    cors_origins: list[str] = ["http://localhost:3000"]

    database_url: str = (
        "postgresql+asyncpg://workpulse:workpulse@localhost:5432/workpulse"
    )
    # Throwaway database used by integration tests (`make check-api` brings up
    # the `postgres-test` compose service on :5433).
    database_test_url: str = (
        "postgresql+asyncpg://workpulse:workpulse@localhost:5433/workpulse_test"
    )

    # --- Phase 2: realtime fan-out (`make redis-up`) ---
    # Pub/sub bus for the Realtime Command Center. Integration tests point
    # `REDIS_URL` at the throwaway `redis-test` service on :6380.
    redis_url: str = "redis://localhost:6379/0"

    # --- Phase 3: event transport (`make kafka-up`, ADR 0012) ---
    # Outbox relay → Kafka: one topic, message key `data.employee_id`, value
    # the stored envelope. (Tests use the throwaway `kafka-test` on :9093.)
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_events_topic: str = "workpulse.events"
    kafka_events_topic_partitions: int = 1
    relay_batch_size: int = 100
    relay_poll_seconds: float = 1.0
    relay_backoff_base_seconds: float = 1.0
    relay_backoff_max_seconds: float = 30.0

    # The realtime consumer (workers/realtime, ADR 0013): `workpulse.events` →
    # presence facts on `realtime:tenant:{tenant_id}`. A stable group resumes
    # its committed offsets on restart; a first start lands at the topic tip
    # (`latest` — no history replay). `realtime_poll_seconds` is the `getmany`
    # max wait (returns early on data), so the relay-poll + fetch-wait latency
    # tail stays well inside the ≤2s wall metric under gate defaults.
    realtime_consumer_group: str = "workpulse-realtime"
    realtime_poll_seconds: float = 1.0
    realtime_backoff_base_seconds: float = 1.0
    realtime_backoff_max_seconds: float = 30.0

    # --- Phase 3: notifications (`workers/notifications`, ADR 0015) ---
    # Consumer knobs mirror the realtime consumer's shape (poll max wait and
    # bounded fetch/store backoff). Delivery knobs feed `NotificationService`:
    # at most `send_max_attempts` provider attempts per channel, doubling the
    # `send_backoff_base_seconds` delay in between.
    notifications_consumer_group: str = "workpulse-notifications"
    notifications_poll_seconds: float = 1.0
    notifications_backoff_base_seconds: float = 1.0
    notifications_backoff_max_seconds: float = 30.0
    notifications_send_max_attempts: int = 3
    notifications_send_backoff_base_seconds: float = 0.5

    # Provider identities (v1: one env-configured Resend sender and one Slack
    # channel — the single dev-tenant shape, ADR 0015 decision 5). An empty
    # value is not a startup failure: that channel's deliveries are
    # `skipped(not_configured)` while the other channel proceeds.
    resend_api_key: str | None = None
    resend_from_address: str | None = None
    slack_bot_token: str | None = None
    slack_channel_id: str | None = None

    # --- Phase 4: payroll calculation worker (`workers/payroll`, T8) ---
    # The job loop polls `payroll_runs` for `QUEUED` runs (claiming happens in
    # the service's state-guarded transition), calculates them and backs off
    # exponentially on database hiccups. Amounts never appear in its logs.
    payroll_worker_batch_size: int = 10
    payroll_worker_poll_seconds: float = 1.0
    payroll_worker_backoff_base_seconds: float = 1.0
    payroll_worker_backoff_max_seconds: float = 30.0

    # --- Phase 4: analytical projection (`make clickhouse-up`, ADR 0019) ---
    # Reports read from ClickHouse; the CDC sink writes it. The test URL points
    # at the throwaway `clickhouse-test` service on :8124 — `tests/conftest.py`
    # pins `CLICKHOUSE_URL` to it before any `app.*` import.
    clickhouse_url: str = "http://workpulse:workpulse@127.0.0.1:8123/workpulse"
    clickhouse_test_url: str = (
        "http://workpulse:workpulse@127.0.0.1:8124/workpulse_test"
    )

    # --- Phase 5: CDC projection sink (`make cdc-up`, ADR 0024) ---
    # Debezium writes `workpulse.cdc.<table>`; the sink lands the change rows
    # in the ClickHouse projection. It consumes `earliest` with a stable group
    # and applies its own readiness fence (D5). After the T9 cutover an empty
    # value targets the canonical `CLICKHOUSE_URL` database; the shadow
    # database used for parity is retired.
    cdc_sink_consumer_group: str = "workpulse-cdc-sink"
    cdc_projection_database: str = ""
    cdc_connect_url: str = "http://localhost:8083"
    #: Connector + replication slot the T8 health poller reads (must match
    #: `ops/cdc/connector.json` / `ops/cdc/register.sh`; the test instance
    #: overrides the slot to `workpulse_cdc_test`).
    cdc_connector_name: str = "workpulse-cdc"
    cdc_slot_name: str = "workpulse_cdc"
    cdc_health_poll_seconds: float = 15.0
    cdc_poll_seconds: float = 1.0
    cdc_backoff_base_seconds: float = 1.0
    cdc_backoff_max_seconds: float = 30.0

    # --- Phase 4+: employee documents (`make minio-up`, ADR 0022) ---
    # Private S3-compatible bucket; the API returns presigned URLs only and
    # never a key or a public path. The test twin points at the throwaway
    # `minio-test` service on :9002 — `tests/conftest.py` pins `STORAGE_*` to
    # the `STORAGE_TEST_*` values before any `app.*` import. The adapter clamps
    # TTLs to the feature caps (upload ≤ 10 min, download ≤ 5 min).
    storage_endpoint: str = "http://127.0.0.1:9000"
    storage_access_key: str = "workpulse"
    storage_secret_key: str = "workpulse"
    storage_bucket: str = "documents"
    storage_upload_ttl_seconds: int = 600
    storage_download_ttl_seconds: int = 300
    # Bounded client timeouts (Phase 7, ADR 0025): the MinIO breaker can only
    # fail fast if a single hung call is itself bounded and botocore retries are
    # pinned to one attempt (the adapter's Config). Uploads bypass the API
    # (browser → presigned POST); API calls are HEAD/copy/delete of at most the
    # 20 MB document cap, so the read timeout stays well below the breaker
    # cooldown to avoid pinning a worker thread for the full 30s.
    storage_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    storage_read_timeout_seconds: float = Field(default=5.0, gt=0)

    storage_test_endpoint: str = "http://127.0.0.1:9002"
    storage_test_access_key: str = "workpulse"
    storage_test_secret_key: str = "workpulse"
    storage_test_bucket: str = "documents-test"

    # --- Phase 6: observability (OpenTelemetry, ADR 0023) ---
    # Spans are always collected in-process; OTLP HTTP export is opt-in so the
    # default dev/test runs never attempt a network call. The API serves
    # Prometheus text exposition on `GET /metrics`; each worker optionally
    # starts its own loopback-only listener (`0` disables it — tests and
    # parallel runs never bind a port).
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "workpulse"
    relay_metrics_port: int = 9101
    realtime_metrics_port: int = 9102
    notifications_metrics_port: int = 9103
    payroll_metrics_port: int = 9104
    cdc_sink_metrics_port: int = 9106

    # --- Phase 7: reliability shared plumbing (ADR 0025) ---
    # Circuit breakers wrap only non-critical outbound calls (Kafka publish,
    # MinIO storage, Resend/Slack). State is per process; a dependency that is
    # down in this monolith stays down, so PostgreSQL/Redis/ClickHouse are not
    # wrapped. `CIRCUIT_BREAKER_ENABLED=false` passes calls straight through.
    circuit_breaker_enabled: bool = True
    circuit_breaker_failure_threshold: int = Field(default=5, ge=1)
    circuit_breaker_cooldown_seconds: float = Field(
        default=30.0, ge=0, allow_inf_nan=False
    )

    # Redis sliding-window rate limiting (Phase 7, ADR 0025). Two tiers:
    # `auth` covers `POST /auth/login` and `PUT /auth/password` keyed by client
    # IP (brute-force protection); `mutation` covers every authenticated
    # mutating route keyed by session user id. Reads are unthrottled. Budgets
    # are sized so the e2e suite and the k6 smoke keep ≥ 2× headroom (spec rule
    # 5); tripping a gate is a fixture/budget fix, never a disabled limiter.
    # `RATE_LIMIT_ENABLED=false` bypasses the limiter entirely (local debugging
    # only); Redis errors fail open regardless.
    rate_limit_enabled: bool = True
    # Reverse proxies allowed to supply `X-Forwarded-For`. Empty (the default)
    # keys the auth tier on the socket peer and ignores the client-controlled
    # header; list the immediate proxy hops (JSON list) when deployed behind one.
    rate_limit_trusted_proxies: list[str] = []
    rate_limit_auth_max_requests: int = Field(default=30, ge=1)
    rate_limit_auth_window_seconds: float = Field(
        default=60.0, gt=0, allow_inf_nan=False
    )
    rate_limit_mutation_max_requests: int = Field(default=120, ge=1)
    rate_limit_mutation_window_seconds: float = Field(
        default=60.0, gt=0, allow_inf_nan=False
    )

    # --- Identity: sessions (pinned in `.ai/features/identity-access.md`) ---
    # Absolute session lifetime; the `wp_session` cookie itself is a session
    # cookie (no Max-Age) — the server-side TTL is the authority.
    session_ttl_days: int = 7
    # `Secure` flag on the session cookie: off for local HTTP dev, on outside.
    session_cookie_secure: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
