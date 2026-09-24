"""Shared FastAPI dependencies.

Providers for request-scoped resources live here. Routers receive them through
their signatures; domains never construct infrastructure clients themselves.

    from app.dependencies import SettingsDep

    @router.get("/example")
    async def example(settings: SettingsDep) -> ...: ...
"""

from typing import Annotated

from fastapi import Depends

from app.config import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]

# Arrives with the first persistence feature:
# SessionDep = Annotated[AsyncSession, Depends(get_session)]