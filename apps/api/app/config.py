"""Application settings, loaded from environment variables (and `.env`)."""

from functools import lru_cache

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
    # Reports read from ClickHouse; only the reports consumer writes it. The
    # test URL points at the throwaway `clickhouse-test` service on :8124 —
    # `tests/conftest.py` pins `CLICKHOUSE_URL` to it before any `app.*` import.
    clickhouse_url: str = "http://workpulse:workpulse@127.0.0.1:8123/workpulse"
    clickhouse_test_url: str = (
        "http://workpulse:workpulse@127.0.0.1:8124/workpulse_test"
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
