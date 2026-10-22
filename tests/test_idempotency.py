import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.api.idempotency import IdempotencyGuard
from app.db.session import SessionFactory
from app.models.appointment import Appointment
from app.models.idempotency import IdempotencyKey
from app.repositories.idempotency import IdempotencyRepository, hash_request
from tests.conftest import future_slot

pytestmark = pytest.mark.asyncio
CLINIC = ZoneInfo("Africa/Nairobi")
CREATE = "POST /appointments"


def body_for(seeded, slot, **extra):
    payload = {
        "doctor_id": str(seeded["doctor"].id),
        "patient_id": str(seeded["patient"].id),
        "starts_at": slot.isoformat(),
    }
    payload.update(extra)
    return payload


async def _count_appointments() -> int:
    async with SessionFactory() as session:
        return int(await session.scalar(select(func.count()).select_from(Appointment)) or 0)


async def test_key_is_scoped_per_endpoint(client, seeded):
    """The same key may be used on a different endpoint without conflicting."""
    key = f"scope-{uuid.uuid4()}"
    headers = {"Idempotency-Key": key, "X-Actor-Id": "actor-scope"}
    created = await client.post("/appointments", json=body_for(seeded, future_slot(50, 10)),
                                headers=headers)
    assert created.status_code == 201, created.text
    appointment_id = created.json()["id"]

    cancelled = await client.patch(f"/appointments/{appointment_id}/cancel",
                                   json={"reason": "patient request"}, headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"


async def test_patch_replay_returns_200(client, seeded):
    created = await client.post("/appointments", json=body_for(seeded, future_slot(51, 10)))
    appointment_id = created.json()["id"]
    headers = {"Idempotency-Key": f"cancel-{uuid.uuid4()}", "X-Actor-Id": "actor-cancel"}
    body = {"reason": "no longer needed"}

    first = await client.patch(f"/appointments/{appointment_id}/cancel", json=body, headers=headers)
    replay = await client.patch(f"/appointments/{appointment_id}/cancel", json=body, headers=headers)

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == first.json()


async def test_key_released_after_domain_error(client, seeded):
    """A rejected request must not burn the key: the client can retry with a fix."""
    key = f"retry-{uuid.uuid4()}"
    headers = {"Idempotency-Key": key, "X-Actor-Id": "actor-retry"}
    past = (datetime.now(CLINIC) - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    rejected = await client.post("/appointments", json=body_for(seeded, past), headers=headers)
    assert rejected.status_code == 422, rejected.text

    retried = await client.post("/appointments", json=body_for(seeded, future_slot(52, 10)),
                                headers=headers)
    assert retried.status_code == 201, retried.text


async def test_in_progress_key_returns_409(client, seeded):
    payload = body_for(seeded, future_slot(53, 10))
    key = f"busy-{uuid.uuid4()}"
    async with SessionFactory() as session:
        await IdempotencyRepository(session).claim(
            "actor-busy", CREATE, key, hash_request(payload)
        )
        await session.commit()

    response = await client.post("/appointments", json=payload,
                                 headers={"Idempotency-Key": key, "X-Actor-Id": "actor-busy"})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "IDEMPOTENCY_IN_PROGRESS"


async def test_stale_in_progress_claim_is_reclaimed(client, seeded):
    payload = body_for(seeded, future_slot(54, 10))
    key = f"stale-{uuid.uuid4()}"
    async with SessionFactory() as session:
        row = await IdempotencyRepository(session).claim(
            "actor-stale", CREATE, key, "outdated-hash"
        )
        row.created_at = datetime.now(UTC) - timedelta(minutes=5)
        await session.commit()

    response = await client.post("/appointments", json=payload,
                                 headers={"Idempotency-Key": key, "X-Actor-Id": "actor-stale"})
    assert response.status_code == 201, response.text


async def test_work_and_key_commit_together(client, seeded, monkeypatch):
    """If the key cannot be stored, the appointment must not survive either."""
    payload = body_for(seeded, future_slot(55, 10))
    before = await _count_appointments()

    async def boom(self, body, status_code):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(IdempotencyGuard, "complete", boom)
    with pytest.raises(RuntimeError):
        await client.post("/appointments", json=payload,
                          headers={"Idempotency-Key": f"atomic-{uuid.uuid4()}",
                                   "X-Actor-Id": "actor-atomic"})
    monkeypatch.undo()

    assert await _count_appointments() == before
    async with SessionFactory() as session:
        leftover = await session.scalar(
            select(func.count()).select_from(IdempotencyKey)
            .where(IdempotencyKey.actor_id == "actor-atomic")
        )
    assert leftover == 0
