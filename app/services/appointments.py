import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.models.appointment import Appointment
from app.repositories.appointments import AppointmentRepository, SlotTakenError
from app.services.booking import (
    AppointmentAlreadyCancelled, AppointmentNotActive, BookingService, NotFound, SlotTaken,
)

class AppointmentService:
    def __init__(self, session):
        self.session = session
        self.repo = AppointmentRepository(session)
        self.booking = BookingService(session)

    async def get(self, appointment_id: uuid.UUID) -> Appointment:
        appointment = await self.repo.get(appointment_id)
        if appointment is None:
            raise NotFound("Appointment not found")
        return appointment

    async def cancel(self, appointment_id: uuid.UUID, reason: str, actor: str) -> Appointment:
        appointment = await self.get(appointment_id)
        if appointment.status == "cancelled":
            raise AppointmentAlreadyCancelled("Appointment is already cancelled")
        appointment.status = "cancelled"
        appointment.cancel_reason = reason
        appointment.cancelled_by = actor
        await self.repo.save(appointment)
        await self.session.commit()
        return appointment

    async def reschedule(self, appointment_id: uuid.UUID, starts_at: datetime) -> Appointment:
        appointment = await self.get(appointment_id)
        if appointment.status != "booked":
            raise AppointmentNotActive("Only active appointments can be rescheduled")
        doctor = await self.booking.doctors.get(appointment.doctor_id)
        if doctor is None:
            raise NotFound("Doctor not found")
        now = datetime.now(ZoneInfo("UTC"))
        start, end = await self.booking._validate(doctor, starts_at, now)
        appointment.starts_at = start
        appointment.ends_at = end
        try:
            await self.repo.save(appointment)
        except SlotTakenError as exc:
            raise SlotTaken("This slot is already booked") from exc
        await self.session.commit()
        return appointment

    async def upcoming_for_patient(
        self, patient_id: uuid.UUID, limit: int, cursor: datetime | None
    ):
        return await self.repo.upcoming_for_patient(
            patient_id, datetime.now(ZoneInfo("UTC")), limit, cursor
        )
