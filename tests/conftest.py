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
# Tests exercise the handler logic, not the auth gate: force it off explicitly.
os.environ["CLINIC_AUTH_MODE"] = "none"
BASE = os.environ.get("CLINIC_TEST_DATABASE_URL") or os.environ["CLINIC_DATABASE_URL"]
TEST_DB = BASE.replace("/clinicdb", "/clinicdb_test") if BASE.endswith("/clinicdb") else BASE
os.environ["CLINIC_DATABASE_URL"] = TEST_DB

from app.core.config import settings  # noqa: E402
from app.db.session import SessionFactory  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Clinic, Doctor, DoctorWorkingHours, Patient  # noqa: E402

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

@pytest.fixture()
async def seeded():
    async with SessionFactory() as db:
        clinic = Clinic(name="Test Clinic")
        db.add(clinic)
        await db.flush()
        doctor = Doctor(clinic_id=clinic.id, name="Dr. Test", slot_minutes=30)
        db.add(doctor)
        await db.flush()
        for weekday in range(7):
            db.add(DoctorWorkingHours(doctor_id=doctor.id, weekday=weekday,
                                      start_time=time(9, 0), end_time=time(17, 0)))
        patient = Patient(name="Patient One")
        db.add(patient)
        await db.commit()
        return {"doctor": doctor, "patient": patient}

def future_slot(days_ahead: int = 2, hour: int = 10, minute: int = 0):
    d = (date.today() + timedelta(days=days_ahead))
    from datetime import datetime
    return datetime.combine(d, time(hour, minute), tzinfo=CLINIC_TZ)
