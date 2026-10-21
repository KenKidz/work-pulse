"""Alembic environment — async engine, metadata from the app.

Imports every model module so `Base.metadata` (and autogenerate) sees all
tables: domains models + the audit and outbox models.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine

# Import model modules for their side effects (registering tables on Base).
import app.domains.attendance.models  # noqa: F401
import app.domains.employees.models  # noqa: F401
import app.domains.identity.models  # noqa: F401
import app.domains.leave.models  # noqa: F401
import app.domains.organizations.models  # noqa: F401
import app.domains.payroll.models  # noqa: F401
import app.infrastructure.postgres.models_audit  # noqa: F401
import app.infrastructure.postgres.models_notifications  # noqa: F401
import app.infrastructure.postgres.models_outbox  # noqa: F401
from alembic import context
from app.config import get_settings
from app.infrastructure.postgres.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    """Explicit config URL wins (tests); otherwise app settings."""
    url = config.get_main_option("sqlalchemy.url")
    return url if url else get_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with an async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
