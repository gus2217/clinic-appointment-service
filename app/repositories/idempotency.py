import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency import IdempotencyKey


def hash_request(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()

class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find(self, actor_id: str, key: str) -> IdempotencyKey | None:
        result: IdempotencyKey | None = await self.session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.actor_id == actor_id, IdempotencyKey.key == key
            )
        )
        return result

    async def store(
        self, actor_id: str, key: str, request_hash: str, status_code: int,
        body: dict[str, object],
    ) -> IdempotencyKey:
        row = IdempotencyKey(
            actor_id=actor_id, key=key, request_hash=request_hash,
            status_code=status_code, response_body=body,
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.find(actor_id, key)
            if existing is None:
                raise
            return existing
        return row
