import uuid
from datetime import datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.db.session import SessionFactory
from tests.conftest import future_slot

pytestmark = pytest.mark.asyncio
CLINIC = ZoneInfo("Africa/Nairobi")

async def _post(client, seeded, starts_at, **extra):
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": starts_at.isoformat()}
    body.update(extra)
    return await client.post("/appointments", json=body)

async def test_book_in_past(client, seeded):
    r = await _post(client, seeded, datetime.now(CLINIC) - timedelta(days=1))
    assert r.status_code == 422
    assert r.json()["code"] == "SLOT_IN_PAST"

async def test_book_misaligned(client, seeded):
    slot = future_slot(20, 10) + timedelta(minutes=15)
    r = await _post(client, seeded, slot)
    assert r.status_code == 422
    assert r.json()["code"] == "SLOT_MISALIGNED"

async def test_book_unknown_doctor(client, seeded):
    body = {"doctor_id": str(uuid.uuid4()), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(20, 11).isoformat()}
    r = await client.post("/appointments", json=body)
    assert r.status_code == 404

async def test_book_unknown_patient(client, seeded):
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(uuid.uuid4()),
            "starts_at": future_slot(20, 11).isoformat()}
    r = await client.post("/appointments", json=body)
    assert r.status_code == 404

async def test_book_during_time_off(client, seeded):
    slot = future_slot(21, 10)
    async with SessionFactory() as db:
        await db.execute(text(
            "INSERT INTO doctor_time_off (doctor_id, starts_at, ends_at) VALUES (:d, :s, :e)"
        ), {"d": seeded["doctor"].id,
            "s": slot - timedelta(minutes=30), "e": slot + timedelta(minutes=30)})
        await db.commit()
    r = await _post(client, seeded, slot)
    assert r.status_code == 422
    assert r.json()["code"] == "OUTSIDE_WORKING_HOURS"

async def test_reschedule_into_taken_slot(client, seeded):
    taken = await _post(client, seeded, future_slot(22, 10))
    assert taken.status_code == 201
    moving = await _post(client, seeded, future_slot(22, 11))
    r = await client.patch(
        f"/appointments/{moving.json()['id']}/reschedule",
        json={"starts_at": future_slot(22, 10).isoformat()},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "SLOT_TAKEN"

async def test_book_lead_time(client, seeded):
    now = datetime.now(CLINIC).replace(second=0, microsecond=0)
    slot = (now + timedelta(minutes=30)).replace(minute=((now.minute // 30) + 1) * 30 % 60)
    r = await _post(client, seeded, slot)
    assert r.status_code == 422
    assert r.json()["code"] in {"LEAD_TIME_VIOLATION", "SLOT_IN_PAST"}

async def test_book_before_opening(client, seeded):
    r = await _post(client, seeded, future_slot(23, 8))
    assert r.status_code == 422
    assert r.json()["code"] == "OUTSIDE_WORKING_HOURS"

async def test_availability_excludes_booked_slot(client, seeded):
    slot = future_slot(24, 10)
    assert (await _post(client, seeded, slot)).status_code == 201
    r = await client.get(f"/doctors/{seeded['doctor'].id}/availability",
                         params={"date": slot.date().isoformat()})
    starts = {s["starts_at"] for s in r.json()["slots"]}
    assert slot.isoformat() not in starts

async def test_availability_excludes_time_off(client, seeded):
    day = future_slot(25, 10).date()
    async with SessionFactory() as db:
        await db.execute(text(
            "INSERT INTO doctor_time_off (doctor_id, starts_at, ends_at) VALUES (:d, :s, :e)"
        ), {"d": seeded["doctor"].id,
            "s": datetime.combine(day, dt_time(10, 0), tzinfo=CLINIC),
            "e": datetime.combine(day, dt_time(11, 0), tzinfo=CLINIC)})
        await db.commit()
    r = await client.get(f"/doctors/{seeded['doctor'].id}/availability",
                         params={"date": day.isoformat()})
    starts = {s["starts_at"] for s in r.json()["slots"]}
    assert datetime.combine(day, dt_time(10, 0), tzinfo=CLINIC).isoformat() not in starts
