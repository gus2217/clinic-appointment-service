import uuid
from datetime import time
from typing import Protocol, cast

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.directories.contracts import DoctorView, WorkingHours
from app.models.doctor import Doctor


class DoctorDirectory(Protocol):
    """Port: the only way the domain learns about a doctor."""

    async def get(self, doctor_id: uuid.UUID) -> DoctorView | None: ...


class LocalDoctorDirectory:
    """Demo placeholder. Backed by seeded rows until the doctor service is wired."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, doctor_id: uuid.UUID) -> DoctorView | None:
        doctor = await self.session.get(
            Doctor, doctor_id, options=[selectinload(Doctor.hours)]
        )
        if doctor is None:
            return None
        return DoctorView(
            id=doctor.id,
            name=doctor.name,
            is_active=doctor.is_active,
            slot_minutes=doctor.slot_minutes,
            hours=tuple(
                WorkingHours(h.weekday, h.start_time, h.end_time) for h in doctor.hours
            ),
        )


class HttpDoctorDirectory:
    """Adapter for the external doctor service.

    Contract: GET {base}/doctors/{id} ->
      {"id", "name", "is_active", "slot_minutes",
       "working_hours": [{"weekday", "start_time", "end_time"}]}
    404 means the doctor does not exist.
    """

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client

    async def get(self, doctor_id: uuid.UUID) -> DoctorView | None:
        client = self.client or httpx.AsyncClient(
            timeout=settings.directory_timeout_seconds
        )
        try:
            response = await client.get(f"{self.base_url}/doctors/{doctor_id}")
        finally:
            if self.client is None:
                await client.aclose()
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = cast(dict[str, object], response.json())
        hours = cast(list[dict[str, object]], payload.get("working_hours") or [])
        return DoctorView(
            id=uuid.UUID(str(payload["id"])),
            name=str(payload["name"]),
            is_active=bool(payload.get("is_active", True)),
            slot_minutes=int(cast(int, payload.get("slot_minutes", 30))),
            hours=tuple(
                WorkingHours(
                    int(cast(int, entry["weekday"])),
                    time.fromisoformat(str(entry["start_time"])),
                    time.fromisoformat(str(entry["end_time"])),
                )
                for entry in hours
            ),
        )


def build_doctor_directory(session: AsyncSession) -> DoctorDirectory:
    if settings.directory_mode == "http":
        if not settings.doctors_url:
            raise RuntimeError(
                "CLINIC_DOCTORS_URL is required when CLINIC_DIRECTORY_MODE=http"
            )
        return HttpDoctorDirectory(settings.doctors_url)
    return LocalDoctorDirectory(session)
