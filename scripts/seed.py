import asyncio
import os
import sys
from datetime import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionFactory
from app.models import Clinic, Doctor, DoctorWorkingHours, Patient

async def main() -> None:
    async with SessionFactory() as db:
        clinic = Clinic(name="Demo Clinic")
        db.add(clinic)
        await db.flush()
        for name in ["Dr. Achieng", "Dr. Bett", "Dr. Chege", "Dr. Odhiambo", "Dr. Wanjiku"]:
            doctor = Doctor(clinic_id=clinic.id, name=name)
            db.add(doctor)
            await db.flush()
            for weekday in range(5):
                db.add(DoctorWorkingHours(doctor_id=doctor.id, weekday=weekday,
                                          start_time=time(9, 0), end_time=time(17, 0)))
        for name in ["Patient One", "Patient Two", "Patient Three"]:
            db.add(Patient(name=name))
        await db.commit()
        print("seeded clinic", clinic.id)

if __name__ == "__main__":
    asyncio.run(main())
