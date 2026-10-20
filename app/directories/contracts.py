import uuid
from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class WorkingHours:
    weekday: int
    start_time: time
    end_time: time


@dataclass(frozen=True)
class DoctorView:
    """What this service needs to know about a doctor, regardless of who owns it."""

    id: uuid.UUID
    name: str
    is_active: bool
    slot_minutes: int
    hours: tuple[WorkingHours, ...]


@dataclass(frozen=True)
class PatientView:
    id: uuid.UUID
    name: str
