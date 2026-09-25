"""Session-per-request dependency.

Commits on success, rolls back on any exception, always closes. Services and
repositories receive the session; routers never open one themselves.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.postgres.engine import get_session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
