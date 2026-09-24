"""Version 1 of the HTTP API.

Each domain gets one thin router module in this package, mounted here, e.g.:

    from app.api.v1 import employees

    api_router.include_router(
        employees.router, prefix="/employees", tags=["employees"]
    )

Routers translate HTTP to domain services — no business logic lives here.
"""

from fastapi import APIRouter

api_router = APIRouter()