"""PostgreSQL infrastructure: declarative base, engine, session management.

Wired at the edges (FastAPI dependencies, Alembic). Domains never import this
package directly — they depend on repository protocols.
"""

from app.infrastructure.postgres.base import Base
from app.infrastructure.postgres.engine import (
    dispose_engine,
    get_engine,
    get_session_factory,
)
from app.infrastructure.postgres.session import get_session

__all__ = [
    "Base",
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_session_factory",
]
