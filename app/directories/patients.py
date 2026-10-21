import uuid
from typing import Protocol, cast

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.directories.contracts import PatientView
from app.directories.fixtures import FIXTURE_PATIENTS
from app.models.patient import Patient


class PatientDirectory(Protocol):
    """Port: the only way the domain learns about a patient."""

    async def get(self, patient_id: uuid.UUID) -> PatientView | None: ...


class LocalPatientDirectory:
    """Demo placeholder. Backed by seeded rows until the patient service is wired."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, patient_id: uuid.UUID) -> PatientView | None:
        patient = await self.session.get(Patient, patient_id)
        if patient is None:
            return None
        return PatientView(id=patient.id, name=patient.name)


class FixturePatientDirectory:
    def __init__(self, session: AsyncSession | None = None) -> None:
        self.session = session

    async def get(self, patient_id: uuid.UUID) -> PatientView | None:
        return FIXTURE_PATIENTS.get(patient_id)


class HttpPatientDirectory:
    """Adapter for the external patient service.

    Contract: GET {base}/patients/{id} -> {"id","name"}; 404 means not found.
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
        finally:
            if self.client is None:
                await client.aclose()
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = cast(dict[str, object], response.json())
        return PatientView(id=uuid.UUID(str(payload["id"])), name=str(payload["name"]))


def build_patient_directory(session: AsyncSession) -> PatientDirectory:
    if settings.directory_mode == "fixture":
        return FixturePatientDirectory(session)
    if settings.directory_mode == "http":
        if not settings.patients_url:
            raise RuntimeError(
                "CLINIC_PATIENTS_URL is required when CLINIC_DIRECTORY_MODE=http"
            )
        return HttpPatientDirectory(settings.patients_url)
    return LocalPatientDirectory(session)
