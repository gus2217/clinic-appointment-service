import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency import IdempotencyKey


def hash_request(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find(self, actor_id: str, endpoint: str, key: str) -> IdempotencyKey | None:
        result: IdempotencyKey | None = await self.session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.actor_id == actor_id,
                IdempotencyKey.endpoint == endpoint,
                IdempotencyKey.key == key,
            )
        )
        return result

    async def claim(
        self, actor_id: str, endpoint: str, key: str, request_hash: str
    ) -> IdempotencyKey:
        row = IdempotencyKey(
            actor_id=actor_id, endpoint=endpoint, key=key,
            request_hash=request_hash, state="in_progress",
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def reclaim(self, row: IdempotencyKey, request_hash: str) -> IdempotencyKey:
        row.request_hash = request_hash
        row.state = "in_progress"
        row.status_code = None
        row.response_body = None
        row.created_at = datetime.now(UTC)
        await self.session.flush()
        return row

    async def release(self, row: IdempotencyKey) -> None:
        await self.session.delete(row)
        await self.session.flush()
