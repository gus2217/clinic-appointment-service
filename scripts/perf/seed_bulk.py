"""Seed N doctors and M appointments for realistic plans."""
import asyncio
import random
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.session import SessionFactory
from app.models import Appointment, Clinic, Doctor, DoctorWorkingHours, Patient

CLINIC = ZoneInfo("Africa/Nairobi")

async def main(doctors: int = 20, appointments: int = 2000) -> None:
    async with SessionFactory() as db:
        clinic = Clinic(name="Perf Clinic")
        db.add(clinic)
        await db.flush()
        doctor_rows = []
        for index in range(doctors):
            doctor = Doctor(clinic_id=clinic.id, name=f"Perf Doctor {index}")
            db.add(doctor)
            await db.flush()
            for weekday in range(5):
                db.add(DoctorWorkingHours(doctor_id=doctor.id, weekday=weekday,
                                          start_time=time(9, 0), end_time=time(17, 0)))
            doctor_rows.append(doctor)
        patient = Patient(name="Perf Patient")
        db.add(patient)
        await db.flush()
        day = datetime.now(CLINIC).date() + timedelta(days=2)
        for index in range(appointments):
            doctor = random.choice(doctor_rows)
            slot = datetime.combine(day + timedelta(days=index // 200),
                                    time(9, 0), tzinfo=CLINIC) + timedelta(minutes=30 * (index % 16))
            db.add(Appointment(doctor_id=doctor.id, patient_id=patient.id,
                               starts_at=slot.astimezone(ZoneInfo("UTC")),
                               ends_at=(slot + timedelta(minutes=30)).astimezone(ZoneInfo("UTC")),
                               status="booked"))
        await db.commit()
        print(f"seeded {doctors} doctors, {appointments} appointments")

if __name__ == "__main__":
    asyncio.run(main())
