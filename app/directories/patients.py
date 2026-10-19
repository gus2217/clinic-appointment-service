import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.patient import Patient

class PatientDirectory:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, patient_id: uuid.UUID) -> Patient | None:
        return await self.session.get(Patient, patient_id)
