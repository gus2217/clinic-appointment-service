"""Seed appointments for realistic query plans.

Doctors and patients come from the fixture directory; this service only owns
appointments, so this script creates nothing else.
"""
import asyncio
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy.exc import IntegrityError

from app.db.session import SessionFactory
from app.directories import fixtures
from app.models import Appointment

CLINIC = ZoneInfo("Africa/Nairobi")


async def main(appointments: int = 2000) -> None:
    doctors = list(fixtures.FIXTURE_DOCTORS)
    patients = list(fixtures.FIXTURE_PATIENTS)
    created = 0
    attempts = 0
    async with SessionFactory() as db:
        while created < appointments and attempts < appointments * 5:
            attempts += 1
            doctor = random.choice(doctors)
            patient = random.choice(patients)
            day = (datetime.now(CLINIC) + timedelta(days=random.randint(1, 60))).date()
            starts = datetime.combine(
                day,
                datetime.min.time().replace(
                    hour=random.randint(9, 16), minute=random.choice((0, 30))
                ),
                tzinfo=CLINIC,
            )
            try:
                async with db.begin_nested():
                    db.add(Appointment(
                        doctor_id=doctor, patient_id=patient,
                        starts_at=starts, ends_at=starts + timedelta(minutes=30),
                        duration_minutes=30,
                    ))
                    await db.flush()
            except IntegrityError:
                continue
            created += 1
        await db.commit()
    print(f"created {created} appointments")


if __name__ == "__main__":
    asyncio.run(main())
