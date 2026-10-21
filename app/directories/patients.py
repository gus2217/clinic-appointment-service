import uuid
from typing import Protocol, cast

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.directories import fixtures
from app.directories.contracts import PatientView
from app.directories.errors import DirectoryUnavailable


class PatientDirectory(Protocol):
    """Port: the only way the domain learns about a patient."""

    async def get(self, patient_id: uuid.UUID) -> PatientView | None: ...


class FixturePatientDirectory:
    def __init__(self, session: AsyncSession | None = None) -> None:
        self.session = session

    async def get(self, patient_id: uuid.UUID) -> PatientView | None:
        return fixtures.FIXTURE_PATIENTS.get(patient_id)


class HttpPatientDirectory:
    """Adapter for the external patient service.

    Contract: GET {base}/patients/{id} -> {"id","name"}; 404 means not found.
    Upstream failures raise DirectoryUnavailable (fail closed with 503).
    """

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client

    async def get(self, patient_id: uuid.UUID) -> PatientView | None:
        client = self.client or httpx.AsyncClient(
            timeout=settings.directory_timeout_seconds
        )
        try:
            response = await client.get(f"{self.base_url}/patients/{patient_id}")
        except httpx.HTTPError as exc:
            raise DirectoryUnavailable(f"directory request failed: {exc}") from exc
        finally:
            if self.client is None:
                await client.aclose()
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise DirectoryUnavailable(f"directory returned {response.status_code}")
        try:
            payload = cast(dict[str, object], response.json())
            return PatientView(id=uuid.UUID(str(payload["id"])), name=str(payload["name"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise DirectoryUnavailable("directory returned an invalid patient payload") from exc


def build_patient_directory(session: AsyncSession | None = None) -> PatientDirectory:
    if settings.directory_mode == "http":
        if not settings.patients_url:
            raise RuntimeError(
                "CLINIC_PATIENTS_URL is required when CLINIC_DIRECTORY_MODE=http"
            )
        return HttpPatientDirectory(settings.patients_url)
    return FixturePatientDirectory(session)
