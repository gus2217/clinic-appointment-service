from datetime import timedelta

import pytest
from sqlalchemy import text

from app.db.session import SessionFactory
from tests.conftest import future_slot

pytestmark = pytest.mark.asyncio


async def test_pending_hold_hides_slot_from_availability(client, seeded):
    slot = future_slot(21, 10)
    created = await client.post("/appointments", json={
        "doctor_id": str(seeded["doctor"].id),
        "patient_id": str(seeded["patient"].id),
        "starts_at": slot.isoformat(),
        "requires_approval": True,
    })
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "pending"

    r = await client.get(f"/doctors/{seeded['doctor'].id}/availability",
                         params={"date": slot.date().isoformat()})
    assert r.status_code == 200
    assert slot.isoformat() not in {s["starts_at"] for s in r.json()["slots"]}


async def test_expired_hold_does_not_hide_slot(client, seeded):
    slot = future_slot(22, 10)
    async with SessionFactory() as db:
        await db.execute(text(
            "INSERT INTO appointment (doctor_id, patient_id, starts_at, ends_at,"
            " duration_minutes, status, hold_expires_at)"
            " VALUES (:d, :p, :s, :e, 30, 'pending', now() - interval '1 hour')"
        ), {"d": seeded["doctor"].id, "p": seeded["patient"].id,
            "s": slot, "e": slot + timedelta(minutes=30)})
        await db.commit()

    r = await client.get(f"/doctors/{seeded['doctor'].id}/availability",
                         params={"date": slot.date().isoformat()})
    assert r.status_code == 200
    assert slot.isoformat() in {s["starts_at"] for s in r.json()["slots"]}
