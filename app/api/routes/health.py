from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db

router = APIRouter(tags=["health"])

@router.get("/")
async def root():
    return {"service": "clinic-booking", "docs": "/docs"}

@router.get("/health")
async def health():
    return {"status": "ok"}

@router.get("/ready")
async def ready(db: AsyncSession = Depends(get_db)):
    await db.execute(text("select 1"))
    return {"status": "ready"}
