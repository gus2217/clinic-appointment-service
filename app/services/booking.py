import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.directories.contracts import DoctorView
from app.directories.doctors import build_doctor_directory
from app.directories.patients import build_patient_directory
from app.models.appointment import Appointment
from app.repositories.appointments import AppointmentRepository, SlotTakenError

HOLD_HOURS = 24

class BookingError(Exception):
    """Expected domain failure: carries the API code and HTTP status."""

    code = "BOOKING_ERROR"
    status = 400

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

class NotFound(BookingError):
    status = 404
    code = "NOT_FOUND"

class SlotInPast(BookingError):
    status = 422
    code = "SLOT_IN_PAST"

class LeadTimeViolation(BookingError):
    status = 422
    code = "LEAD_TIME_VIOLATION"

class SlotMisaligned(BookingError):
    status = 422
    code = "SLOT_MISALIGNED"

class OutsideWorkingHours(BookingError):
    status = 422
    code = "OUTSIDE_WORKING_HOURS"

class SlotTaken(BookingError):
    status = 409
    code = "SLOT_TAKEN"

class AppointmentAlreadyCancelled(BookingError):
    status = 409
    code = "APPOINTMENT_ALREADY_CANCELLED"

class AppointmentNotActive(BookingError):
    status = 409
    code = "APPOINTMENT_NOT_ACTIVE"

class IdempotencyConflict(BookingError):
    status = 409
    code = "IDEMPOTENCY_CONFLICT"


class IdempotencyInProgress(BookingError):
    status = 409
    code = "IDEMPOTENCY_IN_PROGRESS"


class BookingService:
    def __init__(self, session: AsyncSession, clinic_timezone: str | None = None) -> None:
        self.session = session
        self.tz = ZoneInfo(clinic_timezone or settings.clinic_timezone)
        self.doctors = build_doctor_directory(session)
        self.patients = build_patient_directory(session)
        self.appointments = AppointmentRepository(session)

    def _normalise(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=self.tz)
        return value

    async def validate(
        self, doctor: DoctorView, starts_at: datetime, now: datetime
    ) -> tuple[datetime, datetime]:
        local = self._normalise(starts_at).astimezone(self.tz)
        start_utc = local.astimezone(UTC)
        if start_utc <= now:
            raise SlotInPast("Slot is in the past")
        if start_utc < now + timedelta(minutes=settings.lead_time_minutes):
            raise LeadTimeViolation("Lead time not met")
        if local.minute % settings.slot_minutes or local.second or local.microsecond:
            raise SlotMisaligned("Slot is not aligned to the grid")
        hours = [h for h in doctor.hours if h.weekday == local.weekday()]
        end_local = local + timedelta(minutes=settings.slot_minutes)
        if not any(h.start_time <= local.time() and end_local.time() <= h.end_time for h in hours):
            raise OutsideWorkingHours("Outside working hours")
        day_start = datetime.combine(local.date(), time.min, tzinfo=self.tz)
        time_off = await self.doctors.time_off(
            doctor.id, day_start, day_start + timedelta(days=1), fresh=True
        )
        for off in time_off:
            if start_utc < off.ends_at and off.starts_at < start_utc + timedelta(
                minutes=settings.slot_minutes
            ):
                raise OutsideWorkingHours("Doctor is unavailable")
        return start_utc, start_utc + timedelta(minutes=settings.slot_minutes)

    async def book(
        self, doctor_id: uuid.UUID, patient_id: uuid.UUID, starts_at: datetime,
        requires_approval: bool = False,
    ) -> Appointment:
        await self.appointments.sweep_expired()
        doctor = await self.doctors.get(doctor_id)
        if doctor is None or not doctor.is_active:
            raise NotFound("Doctor not found")
        patient = await self.patients.get(patient_id)
        if patient is None:
            raise NotFound("Patient not found")

        now = datetime.now(UTC)
        start, end = await self.validate(doctor, starts_at, now)
        status = "pending" if requires_approval else "booked"
        appointment = Appointment(
            doctor_id=doctor.id, patient_id=patient.id, starts_at=start, ends_at=end,
            duration_minutes=settings.slot_minutes, status=status,
            hold_expires_at=(now + timedelta(hours=HOLD_HOURS)) if requires_approval else None,
        )
        try:
            await self.appointments.add(appointment)
        except SlotTakenError as exc:
            raise SlotTaken("This slot is already booked") from exc
        await self.session.flush()
        return appointment
