import uuid
from datetime import datetime
from typing import Sequence
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.appointment import Appointment
from app.models.doctor import DoctorTimeOff

class SlotTakenError(Exception):
    pass

class AppointmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _translate(exc: IntegrityError) -> Exception:
        diag = getattr(exc.orig, "diag", None)
        name = (
            getattr(diag, "constraint_name", None)
            or getattr(exc.orig, "constraint_name", None)
            or ""
        )
        if "ex_doctor_no_overlap" in name or "ex_patient_no_overlap" in name:
            return SlotTakenError("slot taken")
        return exc

    async def add(self, appointment: Appointment) -> Appointment:
        self.session.add(appointment)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise self._translate(exc) from exc
        return appointment

    async def get(self, appointment_id: uuid.UUID) -> Appointment | None:
        return await self.session.get(Appointment, appointment_id)

    async def save(self, appointment: Appointment) -> Appointment:
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise self._translate(exc) from exc
        return appointment

    async def booked_between(
        self, doctor_id: uuid.UUID, start: datetime, end: datetime
    ) -> Sequence[Appointment]:
        rows = await self.session.scalars(
            select(Appointment).where(
                Appointment.doctor_id == doctor_id,
                Appointment.status == "booked",
                Appointment.starts_at < end,
                Appointment.ends_at > start,
            )
        )
        return rows.all()

    async def time_off_between(
        self, doctor_id: uuid.UUID, start: datetime, end: datetime
    ) -> Sequence[DoctorTimeOff]:
        rows = await self.session.scalars(
            select(DoctorTimeOff).where(
                DoctorTimeOff.doctor_id == doctor_id,
                DoctorTimeOff.starts_at < end,
                DoctorTimeOff.ends_at > start,
            )
        )
        return rows.all()

    async def upcoming_for_patient(
        self, patient_id: uuid.UUID, after: datetime, limit: int, cursor: datetime | None
    ) -> Sequence[Appointment]:
        stmt = select(Appointment).where(
            Appointment.patient_id == patient_id,
            Appointment.status == "booked",
            Appointment.starts_at >= after,
        )
        if cursor is not None:
            stmt = stmt.where(Appointment.starts_at > cursor)
        stmt = stmt.order_by(Appointment.starts_at.asc(), Appointment.id.asc()).limit(limit)
        rows = await self.session.scalars(stmt)
        return rows.all()
