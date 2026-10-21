import asyncio
import time as clock_module
import uuid
from collections.abc import Callable, Hashable
from datetime import datetime, time
from typing import Protocol, cast

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.directories import fixtures
from app.directories.cache import TTLCache
from app.directories.contracts import DoctorView, TimeOff, WorkingHours
from app.directories.errors import DirectoryUnavailable


class DoctorDirectory(Protocol):
    """Port: the only way the domain learns about a doctor."""

    async def get(self, doctor_id: uuid.UUID) -> DoctorView | None: ...

    async def time_off(
        self, doctor_id: uuid.UUID, start: datetime, end: datetime, *, fresh: bool = False
    ) -> tuple[TimeOff, ...]: ...


class FixtureDoctorDirectory:
    """In-memory demo adapter: no database rows, deterministic ids."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self.session = session

    async def get(self, doctor_id: uuid.UUID) -> DoctorView | None:
        return fixtures.FIXTURE_DOCTORS.get(doctor_id)

    async def time_off(
        self, doctor_id: uuid.UUID, start: datetime, end: datetime, *, fresh: bool = False
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

    Profile responses are cached for minutes, time-off for seconds, and writes
    pass fresh=True. Upstream failures raise DirectoryUnavailable so the API
    fails closed with 503 instead of guessing availability.
    """

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        *,
        profile_ttl: float | None = None,
        time_off_ttl: float | None = None,
        clock: Callable[[], float] = clock_module.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client
        self._profiles: TTLCache[DoctorView] = TTLCache(
            settings.directory_cache_ttl_seconds if profile_ttl is None else profile_ttl,
            clock,
        )
        self._time_off: TTLCache[tuple[TimeOff, ...]] = TTLCache(
            settings.directory_time_off_ttl_seconds if time_off_ttl is None else time_off_ttl,
            clock,
        )
        self._locks: dict[Hashable, asyncio.Lock] = {}

    def _lock(self, key: Hashable) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _request(
        self, path: str, params: dict[str, str] | None = None
    ) -> httpx.Response:
        client = self.client or httpx.AsyncClient(
            timeout=settings.directory_timeout_seconds
        )
        try:
            response = await client.get(f"{self.base_url}{path}", params=params)
        except httpx.HTTPError as exc:
            raise DirectoryUnavailable(f"directory request failed: {exc}") from exc
        finally:
            if self.client is None:
                await client.aclose()
        if response.status_code >= 500:
            raise DirectoryUnavailable(f"directory returned {response.status_code}")
        return response

    async def get(self, doctor_id: uuid.UUID) -> DoctorView | None:
        cached = self._profiles.get(doctor_id)
        if cached is not None:
            return cached
        async with self._lock(("profile", doctor_id)):
            cached = self._profiles.get(doctor_id)
            if cached is not None:
                return cached
            response = await self._request(f"/doctors/{doctor_id}")
            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                raise DirectoryUnavailable(f"directory returned {response.status_code}")
            try:
                payload = cast(dict[str, object], response.json())
                view = self._parse_doctor(payload)
            except (KeyError, TypeError, ValueError) as exc:
                raise DirectoryUnavailable("directory returned an invalid doctor payload") from exc
            self._profiles.set(doctor_id, view)
            return view

    async def time_off(
        self, doctor_id: uuid.UUID, start: datetime, end: datetime, *, fresh: bool = False
    ) -> tuple[TimeOff, ...]:
        key = (doctor_id, start.isoformat(), end.isoformat())
        if not fresh:
            cached = self._time_off.get(key)
            if cached is not None:
                return cached
        async with self._lock(("time-off", key)):
            if not fresh:
                cached = self._time_off.get(key)
                if cached is not None:
                    return cached
            response = await self._request(
                f"/doctors/{doctor_id}/time-off",
                params={"from": start.isoformat(), "to": end.isoformat()},
            )
            if response.status_code == 404:
                return ()
            if response.status_code >= 400:
                raise DirectoryUnavailable(f"directory returned {response.status_code}")
            try:
                payload = cast(list[dict[str, object]], response.json())
                rows = tuple(
                    TimeOff(
                        datetime.fromisoformat(str(entry["starts_at"])),
                        datetime.fromisoformat(str(entry["ends_at"])),
                    )
                    for entry in payload
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise DirectoryUnavailable("directory returned an invalid time-off payload") from exc
            self._time_off.set(key, rows)
            return rows

    @staticmethod
    def _parse_doctor(payload: dict[str, object]) -> DoctorView:
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


def build_doctor_directory(session: AsyncSession | None = None) -> DoctorDirectory:
    if settings.directory_mode == "http":
        if not settings.doctors_url:
            raise RuntimeError(
                "CLINIC_DOCTORS_URL is required when CLINIC_DIRECTORY_MODE=http"
            )
        return HttpDoctorDirectory(settings.doctors_url)
    return FixtureDoctorDirectory(session)
