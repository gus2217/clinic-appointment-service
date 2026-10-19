import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, field_validator
from app.models.appointment import Appointment

class AppointmentCreate(BaseModel):
    doctor_id: uuid.UUID
    patient_id: uuid.UUID
    starts_at: datetime

    @field_validator("starts_at")
    @classmethod
    def aware(cls, v: datetime) -> datetime:
        return v

class CancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)

class RescheduleRequest(BaseModel):
    starts_at: datetime

class AppointmentOut(BaseModel):
    id: uuid.UUID
    doctor_id: uuid.UUID
    patient_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    timezone: str
    status: str
    cancel_reason: str | None = None

    @classmethod
    def from_model(cls, appointment: Appointment, clinic_timezone: str) -> "AppointmentOut":
        tz = ZoneInfo(clinic_timezone)
        return cls(
            id=appointment.id,
            doctor_id=appointment.doctor_id,
            patient_id=appointment.patient_id,
            starts_at=appointment.starts_at.astimezone(tz),
            ends_at=appointment.ends_at.astimezone(tz),
            timezone=clinic_timezone,
            status=appointment.status,
            cancel_reason=appointment.cancel_reason,
        )

class SlotOut(BaseModel):
    starts_at: datetime
    ends_at: datetime

class AvailabilityOut(BaseModel):
    doctor_id: uuid.UUID
    date: date
    timezone: str
    slots: list[SlotOut]

class AppointmentListOut(BaseModel):
    items: list[AppointmentOut]
    next_cursor: datetime | None = None
