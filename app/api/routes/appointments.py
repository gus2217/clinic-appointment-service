import uuid
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import authz
from app.api.deps import Principal, get_db, get_principal
from app.api.idempotency import IdempotencyGuard
from app.api.schemas import (
    AppointmentCreate,
    AppointmentOut,
    CancelRequest,
    EventOut,
    RejectRequest,
    RescheduleRequest,
)
from app.core.config import settings
from app.models.appointment import Appointment
from app.repositories.idempotency import hash_request
from app.services.appointments import AppointmentService
from app.services.booking import BookingService

router = APIRouter(prefix="/appointments", tags=["appointments"])


def _guard(
    db: AsyncSession,
    response: Response,
    principal: Principal,
    idempotency_key: str | None,
    endpoint: str,
    payload: dict[str, object],
) -> IdempotencyGuard:
    return IdempotencyGuard(
        session=db,
        response=response,
        actor_id=principal.actor_id,
        endpoint=endpoint,
        key=idempotency_key,
        request_hash=hash_request(payload) if idempotency_key else "",
    )


async def _mutate(
    *,
    db: AsyncSession,
    response: Response,
    principal: Principal,
    idempotency_key: str | None,
    endpoint: str,
    request_payload: dict[str, object],
    authorize: Callable[[], None],
    action: Callable[[], Awaitable[Appointment]],
    success_status: int = status.HTTP_200_OK,
) -> AppointmentOut:
    """Run one mutating handler under the idempotency guard.

    Authorisation happens before the guard so a replay can never bypass it.
    """
    authorize()
    guard = _guard(db, response, principal, idempotency_key, endpoint, request_payload)
    async with guard:
        if guard.replayed is not None:
            return guard.replay(AppointmentOut)
        appointment = await action()
        body = AppointmentOut.from_model(appointment, settings.clinic_timezone)
        await guard.complete(body.model_dump(mode="json"), success_status)
        return body


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
async def create(
    payload: AppointmentCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AppointmentOut:
    service = BookingService(db)
    return await _mutate(
        db=db,
        response=response,
        principal=principal,
        idempotency_key=idempotency_key,
        endpoint="POST /appointments",
        request_payload=payload.model_dump(mode="json"),
        authorize=lambda: authz.can_book(principal, str(payload.patient_id)),
        action=lambda: service.book(
            payload.doctor_id, payload.patient_id, payload.starts_at,
            payload.requires_approval,
        ),
        success_status=status.HTTP_201_CREATED,
    )


@router.get("/{appointment_id}", response_model=AppointmentOut)
async def get_appointment(
    appointment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> AppointmentOut:
    service = AppointmentService(db)
    appointment = await service.get(appointment_id)
    authz.can_view(principal, appointment)
    return AppointmentOut.from_model(appointment, settings.clinic_timezone)


@router.get("/{appointment_id}/events", response_model=list[EventOut])
async def list_events(
    appointment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[EventOut]:
    service = AppointmentService(db)
    appointment = await service.get(appointment_id)
    authz.can_view(principal, appointment)
    events = await service.events_for(appointment_id)
    return [EventOut.from_model(e) for e in events]


@router.patch("/{appointment_id}/cancel", response_model=AppointmentOut)
async def cancel(
    appointment_id: uuid.UUID, payload: CancelRequest, response: Response,
    db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AppointmentOut:
    service = AppointmentService(db)
    appointment = await service.get(appointment_id)
    return await _mutate(
        db=db, response=response, principal=principal,
        idempotency_key=idempotency_key,
        endpoint="PATCH /appointments/{appointment_id}/cancel",
        request_payload={"appointment_id": str(appointment_id), "reason": payload.reason},
        authorize=lambda: authz.can_cancel(principal, appointment),
        action=lambda: service.cancel(
            appointment_id, payload.reason, principal.actor_id, principal.role
        ),
    )


@router.patch("/{appointment_id}/reschedule", response_model=AppointmentOut)
async def reschedule(
    appointment_id: uuid.UUID, payload: RescheduleRequest, response: Response,
    db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AppointmentOut:
    service = AppointmentService(db)
    appointment = await service.get(appointment_id)
    return await _mutate(
        db=db, response=response, principal=principal,
        idempotency_key=idempotency_key,
        endpoint="PATCH /appointments/{appointment_id}/reschedule",
        request_payload={
            "appointment_id": str(appointment_id),
            "starts_at": payload.starts_at.isoformat(),
        },
        authorize=lambda: authz.can_reschedule(principal, appointment),
        action=lambda: service.reschedule(appointment_id, payload.starts_at),
    )


@router.patch("/{appointment_id}/approve", response_model=AppointmentOut)
async def approve(
    appointment_id: uuid.UUID, response: Response,
    db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AppointmentOut:
    service = AppointmentService(db)
    appointment = await service.get(appointment_id)
    return await _mutate(
        db=db, response=response, principal=principal,
        idempotency_key=idempotency_key,
        endpoint="PATCH /appointments/{appointment_id}/approve",
        request_payload={"appointment_id": str(appointment_id)},
        authorize=lambda: authz.can_clinical(principal, appointment),
        action=lambda: service.approve(
            appointment_id, principal.actor_id, principal.role
        ),
    )


@router.patch("/{appointment_id}/reject", response_model=AppointmentOut)
async def reject(
    appointment_id: uuid.UUID, payload: RejectRequest, response: Response,
    db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AppointmentOut:
    service = AppointmentService(db)
    appointment = await service.get(appointment_id)
    return await _mutate(
        db=db, response=response, principal=principal,
        idempotency_key=idempotency_key,
        endpoint="PATCH /appointments/{appointment_id}/reject",
        request_payload={"appointment_id": str(appointment_id), "reason": payload.reason},
        authorize=lambda: authz.can_clinical(principal, appointment),
        action=lambda: service.reject(
            appointment_id, payload.reason, principal.actor_id, principal.role
        ),
    )


@router.patch("/{appointment_id}/complete", response_model=AppointmentOut)
async def complete(
    appointment_id: uuid.UUID, response: Response,
    db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AppointmentOut:
    service = AppointmentService(db)
    appointment = await service.get(appointment_id)
    return await _mutate(
        db=db, response=response, principal=principal,
        idempotency_key=idempotency_key,
        endpoint="PATCH /appointments/{appointment_id}/complete",
        request_payload={"appointment_id": str(appointment_id)},
        authorize=lambda: authz.can_clinical(principal, appointment),
        action=lambda: service.complete(
            appointment_id, principal.actor_id, principal.role
        ),
    )


@router.patch("/{appointment_id}/no-show", response_model=AppointmentOut)
async def no_show(
    appointment_id: uuid.UUID, response: Response,
    db: AsyncSession = Depends(get_db), principal: Principal = Depends(get_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AppointmentOut:
    service = AppointmentService(db)
    appointment = await service.get(appointment_id)
    return await _mutate(
        db=db, response=response, principal=principal,
        idempotency_key=idempotency_key,
        endpoint="PATCH /appointments/{appointment_id}/no-show",
        request_payload={"appointment_id": str(appointment_id)},
        authorize=lambda: authz.can_clinical(principal, appointment),
        action=lambda: service.no_show(
            appointment_id, principal.actor_id, principal.role
        ),
    )
