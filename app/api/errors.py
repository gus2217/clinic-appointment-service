from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.directories.errors import DirectoryUnavailable
from app.services.booking import BookingError


def _body(request: Request, code: str, detail: str) -> dict[str, object]:
    return {
        "code": code,
        "detail": detail,
        "request_id": getattr(request.state, "request_id", None),
    }


async def booking_error_handler(request: Request, exc: BookingError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=_body(request, exc.code, exc.message),
    )


async def directory_unavailable_handler(
    request: Request, exc: DirectoryUnavailable
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=_body(request, "DIRECTORY_UNAVAILABLE",
                      "The doctor or patient directory is unavailable"),
        headers={"Retry-After": str(settings.directory_retry_after_seconds)},
    )
