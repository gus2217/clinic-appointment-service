import os
import subprocess
import sys
from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

def _load_env() -> None:
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)

_load_env()
BASE = os.environ.get("CLINIC_TEST_DATABASE_URL") or os.environ["CLINIC_DATABASE_URL"]
TEST_DB = BASE.replace("/clinicdb", "/clinicdb_test") if BASE.endswith("/clinicdb") else BASE
os.environ["CLINIC_DATABASE_URL"] = TEST_DB

from dataclasses import replace  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.directories import fixtures  # noqa: E402
from app.directories.contracts import WorkingHours  # noqa: E402
from app.main import app  # noqa: E402

CLINIC_TZ = ZoneInfo("Africa/Nairobi")

def _reset_schema() -> None:
    async def run() -> None:
        engine = create_async_engine(settings.async_database_url)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    import asyncio
    asyncio.run(run())

@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    _reset_schema()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, cwd=ROOT)

@pytest.fixture()
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

@pytest.fixture(autouse=True)
def week_long_hours(monkeypatch) -> None:
    """Keep date arithmetic stable: every fixture doctor works every day in tests."""
    hours = tuple(WorkingHours(day, time(9, 0), time(17, 0)) for day in range(7))
    monkeypatch.setattr(
        fixtures,
        "FIXTURE_DOCTORS",
        {key: replace(view, hours=hours) for key, view in fixtures.FIXTURE_DOCTORS.items()},
    )


@pytest.fixture()
async def seeded():
    doctor = next(iter(fixtures.FIXTURE_DOCTORS.values()))
    patient = next(iter(fixtures.FIXTURE_PATIENTS.values()))
    return {"doctor": doctor, "patient": patient}

def future_slot(days_ahead: int = 2, hour: int = 10, minute: int = 0):
    d = (date.today() + timedelta(days=days_ahead))
    from datetime import datetime
    return datetime.combine(d, time(hour, minute), tzinfo=CLINIC_TZ)
