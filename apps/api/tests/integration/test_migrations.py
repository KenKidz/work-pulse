"""AC10 — schema is created by Alembic, not by hand.

Runs `upgrade head` / `downgrade base` against a throwaway database
(`workpulse_migration_check`) on the postgres-test server.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from tests.integration.conftest import TEST_DATABASE_URL, alembic_config

MIGRATION_DB = "workpulse_migration_check"
ADMIN_URL = TEST_DATABASE_URL  # connect as workpulse to its own db, AUTOCOMMIT
TARGET_URL = f"{TEST_DATABASE_URL.rsplit('/', 1)[0]}/{MIGRATION_DB}"
FEATURE_TABLES = {
    "audit_entries",
    "departments",
    "locations",
    "positions",
    "employees",
    "employments",
}


async def _admin_execute(sql: str) -> None:
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(sql))
    finally:
        await engine.dispose()


async def _table_names(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return set(
                await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_table_names()
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture
async def migration_database() -> AsyncIterator[str]:
    await _admin_execute(f'DROP DATABASE IF EXISTS "{MIGRATION_DB}"')
    await _admin_execute(f'CREATE DATABASE "{MIGRATION_DB}" OWNER workpulse')
    try:
        yield TARGET_URL
    finally:
        await _admin_execute(f'DROP DATABASE IF EXISTS "{MIGRATION_DB}"')


async def test_upgrade_head_creates_every_table(migration_database: str) -> None:
    cfg = alembic_config(migration_database)

    # Alembic runs its own asyncio loop internally — keep it off ours.
    await asyncio.to_thread(command.upgrade, cfg, "head")

    tables = await _table_names(migration_database)
    assert FEATURE_TABLES <= tables
    assert "alembic_version" in tables


async def test_downgrade_base_removes_every_table(migration_database: str) -> None:
    cfg = alembic_config(migration_database)

    await asyncio.to_thread(command.upgrade, cfg, "head")
    await asyncio.to_thread(command.downgrade, cfg, "base")

    tables = await _table_names(migration_database)
    assert not (FEATURE_TABLES & tables)


async def test_upgrade_is_idempotent_from_current_head(migration_database: str) -> None:
    cfg = alembic_config(migration_database)

    await asyncio.to_thread(command.upgrade, cfg, "head")
    await asyncio.to_thread(command.upgrade, cfg, "head")  # second run is a no-op

    tables = await _table_names(migration_database)
    assert FEATURE_TABLES <= tables
