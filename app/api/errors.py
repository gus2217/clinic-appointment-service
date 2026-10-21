from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.directories.errors import DirectoryUnavailable
from app.services.booking import (
    AppointmentAlreadyCancelled,
    AppointmentNotActive,
    BookingError,
    LeadTimeViolation,
    NotFound,
    OutsideWorkingHours,
    SlotInPast,
    SlotMisaligned,
    SlotTaken,
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


async def directory_unavailable_handler(
    request: Request, exc: DirectoryUnavailable
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "code": "DIRECTORY_UNAVAILABLE",
            "detail": "The doctor or patient directory is unavailable",
            "request_id": getattr(request.state, "request_id", None),
        },
        headers={"Retry-After": str(settings.directory_retry_after_seconds)},
    )
