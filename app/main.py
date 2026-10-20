import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.api.errors import booking_error_handler
from app.api.routes import appointments, availability, health, patients
from app.core.config import settings
from app.db.session import engine
from app.services.booking import BookingError


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if settings.environment == "production" and settings.auth_mode == "none":
        raise RuntimeError(
            "CLINIC_AUTH_MODE=none is refused when CLINIC_ENVIRONMENT=production"
        )
    yield
    await engine.dispose()


app = FastAPI(title="Clinic Booking API", version="1.0.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(appointments.router)
app.include_router(availability.router)
app.include_router(patients.router)


@app.middleware("http")
async def request_id(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request.state.request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    return response


app.add_exception_handler(BookingError, booking_error_handler)  # type: ignore[arg-type]


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "detail": "Unexpected error",
                 "request_id": getattr(request.state, "request_id", None)},
    )
