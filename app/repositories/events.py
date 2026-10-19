import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.event import AppointmentEvent

class EventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    def add(self, event: AppointmentEvent) -> None:
        self.session.add(event)

    async def for_appointment(self, appointment_id: uuid.UUID) -> list[AppointmentEvent]:
        rows = await self.session.scalars(
            select(AppointmentEvent)
            .where(AppointmentEvent.appointment_id == appointment_id)
            .order_by(AppointmentEvent.created_at.asc(), AppointmentEvent.id.asc())
        )
        return list(rows.all())
