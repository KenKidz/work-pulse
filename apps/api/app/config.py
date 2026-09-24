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

    # Unused until the first persistence feature lands (Phase 1, feature #1).
    database_url: str = (
        "postgresql+asyncpg://workpulse:workpulse@localhost:5432/workpulse"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()