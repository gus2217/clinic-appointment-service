"""Demo directory data.

Ids match the rows that were seeded into the demo database, so existing README
examples and bookmarks keep working when this becomes the default adapter.
"""

import uuid
from datetime import time

from app.directories.contracts import DoctorView, PatientView, TimeOff, WorkingHours

WEEKDAY_HOURS = tuple(
    WorkingHours(weekday=day, start_time=time(9, 0), end_time=time(17, 0))
    for day in range(5)
)

DOCTOR_SEEDS = (
    ("6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14", "Dr. Achieng"),
    ("891c1683-7204-44c7-a68d-9c1ca6a31814", "Dr. Bett"),
    ("efea9b80-d2f4-4a3b-906f-f4919fc59784", "Dr. Chege"),
    ("1b761fe4-fbcd-476d-8c10-2eeb7d905680", "Dr. Odhiambo"),
    ("cd955052-2fe8-4db1-b382-e5638beb9ab9", "Dr. Wanjiku"),
)

PATIENT_SEEDS = (
    ("1b6c25f0-45c6-426a-a3dd-33ab4e226e9d", "Patient One"),
    ("3e80c513-3391-40c4-aae9-802bb0ec7e3f", "Patient Two"),
    ("20922d98-a9b4-41fc-9c4b-07d01593d990", "Patient Three"),
)


def _doctor(doctor_id: str, name: str) -> DoctorView:
    return DoctorView(
        id=uuid.UUID(doctor_id), name=name, is_active=True,
        slot_minutes=30, hours=WEEKDAY_HOURS,
    )


FIXTURE_DOCTORS: dict[uuid.UUID, DoctorView] = {
    uuid.UUID(doctor_id): _doctor(doctor_id, name) for doctor_id, name in DOCTOR_SEEDS
}

FIXTURE_PATIENTS: dict[uuid.UUID, PatientView] = {
    uuid.UUID(patient_id): PatientView(id=uuid.UUID(patient_id), name=name)
    for patient_id, name in PATIENT_SEEDS
}

# (doctor_id, interval). Empty by default; tests and demos can populate it.
FIXTURE_TIME_OFF: tuple[tuple[uuid.UUID, TimeOff], ...] = ()
