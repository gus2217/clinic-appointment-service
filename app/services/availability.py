from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.directories.contracts import DoctorView
from app.directories.doctors import build_doctor_directory
from app.repositories.appointments import AppointmentRepository


class AvailabilityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tz = ZoneInfo(settings.clinic_timezone)
        self.repo = AppointmentRepository(session)
        self.doctors = build_doctor_directory(session)

    async def slots(self, doctor: DoctorView, on_date: date) -> list[tuple[datetime, datetime]]:
        hours = [h for h in doctor.hours if h.weekday == on_date.weekday()]
        if not hours:
            return []
        day_start = datetime.combine(on_date, time.min, tzinfo=self.tz)
        day_end = day_start + timedelta(days=1)
        booked = await self.repo.booked_between(doctor.id, day_start, day_end)
        time_off = await self.doctors.time_off(doctor.id, day_start, day_end)
        now = datetime.now(ZoneInfo("UTC"))
        lead = now + timedelta(minutes=settings.lead_time_minutes)
        result: list[tuple[datetime, datetime]] = []
        for h in hours:
            cursor = datetime.combine(on_date, h.start_time, tzinfo=self.tz)
            close = datetime.combine(on_date, h.end_time, tzinfo=self.tz)
            while cursor + timedelta(minutes=settings.slot_minutes) <= close:
                end = cursor + timedelta(minutes=settings.slot_minutes)
                start_utc = cursor.astimezone(ZoneInfo("UTC"))
                end_utc = end.astimezone(ZoneInfo("UTC"))
                busy = any(start_utc < a.ends_at and a.starts_at < end_utc for a in booked)
                off = any(start_utc < t.ends_at and t.starts_at < end_utc for t in time_off)
                if start_utc > now and start_utc >= lead and not busy and not off:
                    result.append((start_utc, end_utc))
                cursor = end
        return result
