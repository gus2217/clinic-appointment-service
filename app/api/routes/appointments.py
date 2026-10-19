import uuid
from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import Principal, get_db, get_principal
from app.api.schemas import AppointmentCreate, AppointmentOut, CancelRequest, RescheduleRequest
from app.core.config import settings
from app.repositories.idempotency import IdempotencyRepository, hash_request
from app.services.appointments import AppointmentService
from app.services.booking import BookingError, BookingService

router = APIRouter(prefix="/appointments", tags=["appointments"])

class IdempotencyConflict(BookingError):
    code = "IDEMPOTENCY_CONFLICT"

@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
async def create(
    payload: AppointmentCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if idempotency_key:
        repo = IdempotencyRepository(db)
        request_hash = hash_request(payload.model_dump(mode="json"))
        existing = await repo.find(principal.actor_id, idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyConflict("Idempotency key reused with a different request")
            return AppointmentOut(**existing.response_body)
    appointment = await BookingService(db).book(
        payload.doctor_id, payload.patient_id, payload.starts_at
    )
    body = AppointmentOut.from_model(appointment, settings.clinic_timezone)
    if idempotency_key:
        await IdempotencyRepository(db).store(
            principal.actor_id, idempotency_key, request_hash, 201,
            body.model_dump(mode="json"),
        )
        await db.commit()
    return body

@router.get("/{appointment_id}", response_model=AppointmentOut)
async def get_appointment(appointment_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    appointment = await AppointmentService(db).get(appointment_id)
    return AppointmentOut.from_model(appointment, settings.clinic_timezone)

@router.patch("/{appointment_id}/cancel", response_model=AppointmentOut)
async def cancel(
    appointment_id: uuid.UUID,
    payload: CancelRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    appointment = await AppointmentService(db).cancel(
        appointment_id, payload.reason, principal.actor_id
    )
    return AppointmentOut.from_model(appointment, settings.clinic_timezone)

@router.patch("/{appointment_id}/reschedule", response_model=AppointmentOut)
async def reschedule(
    appointment_id: uuid.UUID,
    payload: RescheduleRequest,
    db: AsyncSession = Depends(get_db),
):
    appointment = await AppointmentService(db).reschedule(appointment_id, payload.starts_at)
    return AppointmentOut.from_model(appointment, settings.clinic_timezone)
