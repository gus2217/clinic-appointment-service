from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session


@dataclass(frozen=True)
class Principal:
    actor_id: str
    role: str

async def get_principal(
    x_actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    x_actor_role: str | None = Header(default=None, alias="X-Actor-Role"),
) -> Principal:
    if settings.auth_required:
        if not x_actor_id or not x_actor_role:
            raise HTTPException(status_code=401, detail="Identity required")
        return Principal(actor_id=x_actor_id, role=x_actor_role)
    return Principal(actor_id=x_actor_id or "anonymous", role=x_actor_role or "admin")

async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session
