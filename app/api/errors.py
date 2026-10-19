from fastapi import Request
from fastapi.responses import JSONResponse
from app.services.booking import (
    AppointmentAlreadyCancelled, AppointmentNotActive, BookingError, LeadTimeViolation,
    NotFound, OutsideWorkingHours, SlotInPast, SlotMisaligned, SlotTaken,
)

_STATUS: dict[type[BookingError], int] = {
    NotFound: 404,
    SlotTaken: 409,
    AppointmentAlreadyCancelled: 409,
    AppointmentNotActive: 409,
    SlotInPast: 422,
    LeadTimeViolation: 422,
    SlotMisaligned: 422,
    OutsideWorkingHours: 422,
}

async def booking_error_handler(request: Request, exc: BookingError) -> JSONResponse:
    status = next((code for cls, code in _STATUS.items() if isinstance(exc, cls)), 400)
    return JSONResponse(
        status_code=status,
        content={
            "code": exc.code,
            "detail": exc.message,
            "request_id": getattr(request.state, "request_id", None),
        },
    )
