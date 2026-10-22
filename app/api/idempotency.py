from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from fastapi import Response, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency import IdempotencyKey
from app.repositories.idempotency import IdempotencyRepository
from app.services.booking import IdempotencyConflict, IdempotencyInProgress

# A claim older than this is considered abandoned by a crashed worker.
IN_PROGRESS_TTL = timedelta(seconds=60)

ModelT = TypeVar("ModelT", bound=BaseModel)


class IdempotencyGuard:
    """Claim, replay or release an Idempotency-Key around one mutating handler.

    The claim is committed before the handler runs so a concurrent duplicate is
    rejected instead of racing. The handler's writes and the stored response are
    then committed together, so a crash cannot leave an appointment without its
    idempotency record (or the reverse).
    """

    def __init__(
        self,
        session: AsyncSession,
        response: Response,
        actor_id: str,
        endpoint: str,
        key: str | None,
        request_hash: str,
    ) -> None:
        self.session = session
        self.response = response
        self.actor_id = actor_id
        self.endpoint = endpoint
        self.key = key or ""
        self.request_hash = request_hash
        self.replayed: dict[str, Any] | None = None
        self.row: IdempotencyKey | None = None
        self.claimed = False

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    async def __aenter__(self) -> "IdempotencyGuard":
        if not self.enabled:
            return self
        repo = IdempotencyRepository(self.session)
        existing = await repo.find(self.actor_id, self.endpoint, self.key)
        if existing is None:
            try:
                self.row = await repo.claim(
                    self.actor_id, self.endpoint, self.key, self.request_hash
                )
                self.claimed = True
                await self.session.commit()
            except IntegrityError:
                await self.session.rollback()
                self.row = None
                self.claimed = False
                existing = await repo.find(self.actor_id, self.endpoint, self.key)
                if existing is None:
                    raise
        if existing is not None:
            await self._inspect(repo, existing)
        return self

    async def _inspect(self, repo: IdempotencyRepository, row: IdempotencyKey) -> None:
        if row.state == "completed":
            if row.request_hash != self.request_hash:
                raise IdempotencyConflict("Idempotency key reused with a different request")
            self.replayed = row.response_body or {}
            self.response.headers["Idempotent-Replayed"] = "true"
            return
        if row.created_at < datetime.now(UTC) - IN_PROGRESS_TTL:
            self.row = await repo.reclaim(row, self.request_hash)
            self.claimed = True
            await self.session.commit()
            return
        raise IdempotencyInProgress(
            "A request with this idempotency key is still in progress"
        )

    def replay(self, model: type[ModelT]) -> ModelT:
        self.response.status_code = status.HTTP_200_OK
        return model(**(self.replayed or {}))

    async def complete(self, body: dict[str, Any], status_code: int) -> None:
        if self.row is not None:
            self.row.state = "completed"
            self.row.status_code = status_code
            self.row.response_body = body
        await self.session.commit()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        if exc_type is None:
            return
        await self.session.rollback()
        if self.claimed and self.row is not None:
            await IdempotencyRepository(self.session).release(self.row)
            await self.session.commit()
