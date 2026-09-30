"""Version 1 of the HTTP API.

Each domain gets one thin router module in this package, mounted here.
Routers translate HTTP to domain services — no business logic lives here.
"""

from fastapi import APIRouter

from app.api.v1 import attendance, auth, employees, organizations, users

api_router = APIRouter()
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(users.router, tags=["users"])
api_router.include_router(organizations.router, tags=["organizations"])
api_router.include_router(employees.router, tags=["employees"])
api_router.include_router(attendance.router, tags=["attendance"])
api_router.include_router(attendance.shifts_router, tags=["shifts"])
