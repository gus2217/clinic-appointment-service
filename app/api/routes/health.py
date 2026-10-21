import os

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db

router = APIRouter(tags=["health"])

@router.get("/")
async def root() -> dict[str, str]:
    return {"service": "clinic-booking", "docs": "/docs"}

@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

@router.get("/ready")
async def ready(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    await db.execute(text("select 1"))
    return {"status": "ready"}

@router.get("/version")
async def version() -> dict[str, str]:
    return {"commit": os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GIT_SHA") or "unknown"}
