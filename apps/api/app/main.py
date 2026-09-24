"""WorkPulse API — application entrypoint.

Run locally:  uv run uvicorn app.main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.config import get_settings
from app.shared.errors import register_error_handlers

settings = get_settings()


def create_app() -> FastAPI:
    """Build the FastAPI application.

    Kept as a factory so tests and future workers can create isolated instances.
    """
    application = FastAPI(
        title=settings.app_name,
        version=settings.version,
        debug=settings.environment == "development",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(application)

    @application.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        """Liveness probe. Must not depend on external services."""
        return {"status": "ok", "version": settings.version}

    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()