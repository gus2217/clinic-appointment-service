import asyncio
from datetime import time

from app.db.session import SessionFactory
from app.models import Clinic, Doctor, DoctorWorkingHours, Patient


async def main() -> None:
    async with SessionFactory() as db:
        clinic = Clinic(name="Demo Clinic")
        db.add(clinic)
        await db.flush()
        doctors = []
        for name in ["Dr. Achieng", "Dr. Bett", "Dr. Chege", "Dr. Odhiambo", "Dr. Wanjiku"]:
            doctor = Doctor(clinic_id=clinic.id, name=name)
            db.add(doctor)
            await db.flush()
            doctors.append(doctor)
            for weekday in range(5):
                db.add(DoctorWorkingHours(doctor_id=doctor.id, weekday=weekday,
                                          start_time=time(9, 0), end_time=time(17, 0)))
        patients = []
        for name in ["Patient One", "Patient Two", "Patient Three"]:
            patient = Patient(name=name)
            db.add(patient)
            await db.flush()
            patients.append(patient)
        await db.commit()
        print(f"clinic {clinic.id}")
        for doctor in doctors:
            print(f"doctor {doctor.id} {doctor.name}")
        for patient in patients:
            print(f"patient {patient.id} {patient.name}")


if __name__ == "__main__":
    asyncio.run(main())
