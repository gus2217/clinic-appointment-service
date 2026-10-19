import uuid
from datetime import date
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.api.schemas import AvailabilityOut, SlotOut
from app.core.config import settings
from app.directories.doctors import DoctorDirectory
from app.services.availability import AvailabilityService
from app.services.booking import NotFound

router = APIRouter(prefix="/doctors", tags=["doctors"])

@router.get("/{doctor_id}/availability", response_model=AvailabilityOut)
async def availability(
    doctor_id: uuid.UUID,
    date: date,
    db: AsyncSession = Depends(get_db),
):
    doctor = await DoctorDirectory(db).get(doctor_id)
    if doctor is None or not doctor.is_active:
        raise NotFound("Doctor not found")
    slots = await AvailabilityService(db).slots(doctor, date)
    tz = ZoneInfo(settings.clinic_timezone)
    return AvailabilityOut(
        doctor_id=doctor_id,
        date=date,
        timezone=settings.clinic_timezone,
        slots=[SlotOut(starts_at=s.astimezone(tz), ends_at=e.astimezone(tz)) for s, e in slots],
    )
