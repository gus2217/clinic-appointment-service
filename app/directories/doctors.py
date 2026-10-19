import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.doctor import Doctor

class DoctorDirectory:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, doctor_id: uuid.UUID) -> Doctor | None:
        return await self.session.get(Doctor, doctor_id)
