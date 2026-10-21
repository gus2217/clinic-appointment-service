import uuid
from datetime import datetime, time
from typing import Protocol, cast

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.directories import fixtures
from app.directories.contracts import DoctorView, TimeOff, WorkingHours


class DoctorDirectory(Protocol):
    """Port: the only way the domain learns about a doctor."""

    async def get(self, doctor_id: uuid.UUID) -> DoctorView | None: ...

    async def time_off(
        self, doctor_id: uuid.UUID, start: datetime, end: datetime
    ) -> tuple[TimeOff, ...]: ...


class FixtureDoctorDirectory:
    """In-memory demo adapter: no database rows, deterministic ids."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self.session = session

    async def get(self, doctor_id: uuid.UUID) -> DoctorView | None:
        return fixtures.FIXTURE_DOCTORS.get(doctor_id)

    async def time_off(
        self, doctor_id: uuid.UUID, start: datetime, end: datetime
    ) -> tuple[TimeOff, ...]:
        return tuple(
            interval
            for owner, interval in fixtures.FIXTURE_TIME_OFF
            if owner == doctor_id and interval.starts_at < end and interval.ends_at > start
        )


class HttpDoctorDirectory:
    """Adapter for the external doctor service.

    Contract:
      GET {base}/doctors/{id} ->
        {"id","name","is_active","slot_minutes",
         "working_hours":[{"weekday","start_time","end_time"}]}
      GET {base}/doctors/{id}/time-off?from=&to= -> [{"starts_at","ends_at"}]
      404 means the doctor does not exist.
    """

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client

    async def _get(self, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        client = self.client or httpx.AsyncClient(
            timeout=settings.directory_timeout_seconds
        )
        try:
            return await client.get(f"{self.base_url}{path}", params=params)
        finally:
            if self.client is None:
                await client.aclose()

    async def get(self, doctor_id: uuid.UUID) -> DoctorView | None:
        response = await self._get(f"/doctors/{doctor_id}")
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

    async def time_off(
        self, doctor_id: uuid.UUID, start: datetime, end: datetime
    ) -> tuple[TimeOff, ...]:
        response = await self._get(
            f"/doctors/{doctor_id}/time-off",
            params={"from": start.isoformat(), "to": end.isoformat()},
        )
        if response.status_code == 404:
            return ()
        response.raise_for_status()
        payload = cast(list[dict[str, object]], response.json())
        return tuple(
            TimeOff(
                datetime.fromisoformat(str(entry["starts_at"])),
                datetime.fromisoformat(str(entry["ends_at"])),
            )
            for entry in payload
        )


def build_doctor_directory(session: AsyncSession | None = None) -> DoctorDirectory:
    if settings.directory_mode == "http":
        if not settings.doctors_url:
            raise RuntimeError(
                "CLINIC_DOCTORS_URL is required when CLINIC_DIRECTORY_MODE=http"
            )
        return HttpDoctorDirectory(settings.doctors_url)
    return FixtureDoctorDirectory(session)
