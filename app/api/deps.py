from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Principal as Principal
from app.api.auth import get_principal as get_principal
from app.db.session import get_session

__all__ = ["Principal", "get_db", "get_principal"]


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session
