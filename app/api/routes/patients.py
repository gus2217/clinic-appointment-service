import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import authz
from app.api.deps import Principal, get_db, get_principal
from app.api.schemas import AppointmentListOut, AppointmentOut
from app.core.config import settings
from app.directories.patients import PatientDirectory
from app.services.appointments import AppointmentService
from app.services.booking import NotFound

router = APIRouter(prefix="/patients", tags=["patients"])

@router.get("/{patient_id}/appointments", response_model=AppointmentListOut)
async def upcoming(
    patient_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> AppointmentListOut:
    authz.can_list_patient(principal, str(patient_id))
    if await PatientDirectory(db).get(patient_id) is None:
        raise NotFound("Patient not found")
    rows = await AppointmentService(db).upcoming_for_patient(patient_id, limit, cursor)
    items = [AppointmentOut.from_model(r, settings.clinic_timezone) for r in rows]
    next_cursor = items[-1].starts_at if len(items) == limit else None
    return AppointmentListOut(items=items, next_cursor=next_cursor)
