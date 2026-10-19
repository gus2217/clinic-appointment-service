import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, update

from app.core.config import settings
from app.models.appointment import Appointment
from app.models.event import AppointmentEvent
from app.repositories.appointments import AppointmentRepository, SlotTakenError
from app.repositories.events import EventRepository
from app.services.booking import (
    AppointmentAlreadyCancelled, AppointmentNotActive, BookingService, NotFound, SlotTaken,
)

HOLD_HOURS = 24

ACTIVE = ("pending", "booked")

class AppointmentService:
    def __init__(self, session):
        self.session = session
        self.repo = AppointmentRepository(session)
        self.events = EventRepository(session)
        self.booking = BookingService(session)

    async def sweep_expired(self) -> None:
        await self.session.execute(
            update(Appointment)
            .where(Appointment.status == "pending", Appointment.hold_expires_at < func.now())
            .values(status="expired", cancelled_by="system", updated_at=func.now())
        )

    def _event(self, appointment: Appointment, event_type: str, actor: str,
               actor_role: str, from_status: str | None, reason: str | None = None) -> None:
        self.events.add(AppointmentEvent(
            appointment_id=appointment.id, event_type=event_type,
            actor_id=actor, actor_role=actor_role,
            from_status=from_status, to_status=appointment.status, reason=reason,
        ))

    async def get(self, appointment_id: uuid.UUID) -> Appointment:
        appointment = await self.repo.get(appointment_id)
        if appointment is None:
            raise NotFound("Appointment not found")
        return appointment

    async def _transition(self, appointment_id: uuid.UUID, target: str, event_type: str,
                          actor: str, actor_role: str, reason: str | None = None) -> Appointment:
        appointment = await self.get(appointment_id)
        if appointment.status not in ACTIVE:
            raise AppointmentNotActive("Appointment is not active")
        from_status = appointment.status
        appointment.status = target
        if target == "cancelled":
            appointment.cancel_reason = reason
            appointment.cancelled_by = actor
        if target != "pending":
            appointment.hold_expires_at = None
        appointment.updated_at = datetime.now(ZoneInfo("UTC"))
        self._event(appointment, event_type, actor, actor_role, from_status, reason)
        await self.repo.save(appointment)
        await self.session.commit()
        return appointment

    async def approve(self, appointment_id: uuid.UUID, actor: str, actor_role: str) -> Appointment:
        appointment = await self.get(appointment_id)
        if appointment.status != "pending":
            raise AppointmentNotActive("Only pending appointments can be approved")
        return await self._transition(appointment_id, "booked", "approved", actor, actor_role)

    async def reject(self, appointment_id: uuid.UUID, reason: str, actor: str, actor_role: str) -> Appointment:
        appointment = await self.get(appointment_id)
        if appointment.status != "pending":
            raise AppointmentNotActive("Only pending appointments can be rejected")
        appointment = await self._transition(appointment_id, "rejected", "rejected", actor, actor_role, reason)
        return appointment

    async def cancel(self, appointment_id: uuid.UUID, reason: str, actor: str, actor_role: str = "patient") -> Appointment:
        appointment = await self.get(appointment_id)
        if appointment.status == "cancelled":
            raise AppointmentAlreadyCancelled("Appointment is already cancelled")
        return await self._transition(appointment_id, "cancelled", "cancelled", actor, actor_role, reason)

    async def complete(self, appointment_id: uuid.UUID, actor: str, actor_role: str) -> Appointment:
        appointment = await self.get(appointment_id)
        if appointment.status != "booked":
            raise AppointmentNotActive("Only booked appointments can be completed")
        return await self._transition(appointment_id, "completed", "completed", actor, actor_role)

    async def no_show(self, appointment_id: uuid.UUID, actor: str, actor_role: str) -> Appointment:
        appointment = await self.get(appointment_id)
        if appointment.status != "booked":
            raise AppointmentNotActive("Only booked appointments can be marked no-show")
        return await self._transition(appointment_id, "no_show", "no_show", actor, actor_role)

    async def reschedule(self, appointment_id: uuid.UUID, starts_at: datetime) -> Appointment:
        appointment = await self.get(appointment_id)
        if appointment.status != "booked":
            raise AppointmentNotActive("Only booked appointments can be rescheduled")
        doctor = await self.booking.doctors.get(appointment.doctor_id)
        if doctor is None:
            raise NotFound("Doctor not found")
        now = datetime.now(ZoneInfo("UTC"))
        start, end = await self.booking._validate(doctor, starts_at, now)
        from_status = appointment.status
        appointment.starts_at = start
        appointment.ends_at = end
        appointment.updated_at = datetime.now(ZoneInfo("UTC"))
        self._event(appointment, "rescheduled", "system", "system", from_status)
        try:
            await self.repo.save(appointment)
        except SlotTakenError as exc:
            raise SlotTaken("This slot is already booked") from exc
        await self.session.commit()
        return appointment

    async def upcoming_for_patient(self, patient_id: uuid.UUID, limit: int,
                                   cursor: datetime | None):
        return await self.repo.upcoming_for_patient(
            patient_id, datetime.now(ZoneInfo("UTC")), limit, cursor
        )

    async def events_for(self, appointment_id: uuid.UUID):
        await self.get(appointment_id)
        return await self.events.for_appointment(appointment_id)
