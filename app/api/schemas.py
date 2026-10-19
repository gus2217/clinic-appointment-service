import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field
from app.models.appointment import Appointment
from app.models.event import AppointmentEvent

class AppointmentCreate(BaseModel):
    doctor_id: uuid.UUID
    patient_id: uuid.UUID
    starts_at: datetime
    requires_approval: bool = False

class CancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)

class RejectRequest(BaseModel):
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
    hold_expires_at: datetime | None = None

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
            hold_expires_at=(appointment.hold_expires_at.astimezone(tz)
                             if appointment.hold_expires_at else None),
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

class EventOut(BaseModel):
    id: uuid.UUID
    event_type: str
    actor_id: str | None
    actor_role: str | None
    from_status: str | None
    to_status: str | None
    reason: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, event: AppointmentEvent) -> "EventOut":
        return cls(
            id=event.id, event_type=event.event_type, actor_id=event.actor_id,
            actor_role=event.actor_role, from_status=event.from_status,
            to_status=event.to_status, reason=event.reason, created_at=event.created_at,
        )
