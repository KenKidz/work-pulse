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

    # --- Identity: sessions (pinned in `.ai/features/identity-access.md`) ---
    # Absolute session lifetime; the `wp_session` cookie itself is a session
    # cookie (no Max-Age) — the server-side TTL is the authority.
    session_ttl_days: int = 7
    # `Secure` flag on the session cookie: off for local HTTP dev, on outside.
    session_cookie_secure: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
