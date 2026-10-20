import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from starlette.requests import Request

from app.db.session import SessionFactory
from tests.conftest import future_slot

pytestmark = pytest.mark.asyncio
CLINIC = ZoneInfo("Africa/Nairobi")

def body_for(seeded, slot, **extra):
    payload = {
        "doctor_id": str(seeded["doctor"].id),
        "patient_id": str(seeded["patient"].id),
        "starts_at": slot.isoformat(),
    }
    payload.update(extra)
    return payload

async def book(client, seeded, day, hour, **extra):
    return await client.post("/appointments", json=body_for(seeded, future_slot(day, hour), **extra))

async def test_idempotent_replay_and_conflict(client, seeded):
    payload = body_for(seeded, future_slot(40, 10))
    headers = {"Idempotency-Key": f"key-{uuid.uuid4()}"}
    first = await client.post("/appointments", json=payload, headers=headers)
    assert first.status_code == 201, first.text
    replay = await client.post("/appointments", json=payload, headers=headers)
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]

    changed = dict(payload)
    changed["starts_at"] = future_slot(40, 11).isoformat()
    conflict = await client.post("/appointments", json=changed, headers=headers)
    assert conflict.status_code == 400
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

async def test_get_and_events_success(client, seeded):
    created = await book(client, seeded, 41, 10)
    appt_id = created.json()["id"]
    got = await client.get(f"/appointments/{appt_id}")
    assert got.status_code == 200
    assert got.json()["id"] == appt_id
    events = await client.get(f"/appointments/{appt_id}/events")
    assert events.status_code == 200
    assert isinstance(events.json(), list)

async def test_cancel_success_and_twice(client, seeded):
    created = await book(client, seeded, 42, 10)
    appt_id = created.json()["id"]
    first = await client.patch(f"/appointments/{appt_id}/cancel", json={"reason": "no longer needed"})
    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    again = await client.patch(f"/appointments/{appt_id}/cancel", json={"reason": "again"})
    assert again.status_code == 409
    assert again.json()["code"] == "APPOINTMENT_ALREADY_CANCELLED"

async def test_reschedule_success_and_not_booked(client, seeded):
    created = await book(client, seeded, 43, 10)
    appt_id = created.json()["id"]
    moved = await client.patch(
        f"/appointments/{appt_id}/reschedule",
        json={"starts_at": future_slot(43, 14).isoformat()},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["starts_at"] != created.json()["starts_at"]

    cancelled = await client.patch(f"/appointments/{appt_id}/cancel", json={"reason": "test"})
    assert cancelled.status_code == 200
    blocked = await client.patch(
        f"/appointments/{appt_id}/reschedule",
        json={"starts_at": future_slot(43, 15).isoformat()},
    )
    assert blocked.status_code == 409

async def test_approve_success_and_non_pending(client, seeded):
    pending = await book(client, seeded, 44, 10, requires_approval=True)
    assert pending.json()["status"] == "pending"
    appt_id = pending.json()["id"]
    approved = await client.patch(f"/appointments/{appt_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "booked"
    again = await client.patch(f"/appointments/{appt_id}/approve")
    assert again.status_code == 409

async def test_reject_success_and_non_pending(client, seeded):
    pending = await book(client, seeded, 45, 10, requires_approval=True)
    appt_id = pending.json()["id"]
    rejected = await client.patch(f"/appointments/{appt_id}/reject", json={"reason": "fully booked"})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    again = await client.patch(f"/appointments/{appt_id}/reject", json={"reason": "again"})
    assert again.status_code == 409

async def test_complete_and_no_show(client, seeded):
    one = await book(client, seeded, 46, 10)
    done = await client.patch(f"/appointments/{one.json()['id']}/complete")
    assert done.status_code == 200
    assert done.json()["status"] == "completed"

    two = await book(client, seeded, 46, 11)
    missing = await client.patch(f"/appointments/{two.json()['id']}/no-show")
    assert missing.status_code == 200
    assert missing.json()["status"] == "no_show"

    three = await book(client, seeded, 46, 12)
    cancelled = await client.patch(f"/appointments/{three.json()['id']}/cancel",
                                   json={"reason": "test cleanup"})
    assert cancelled.status_code == 200
    blocked = await client.patch(f"/appointments/{three.json()['id']}/complete")
    assert blocked.status_code == 409

async def test_get_unknown_appointment_404(client):
    r = await client.get(f"/appointments/{uuid.uuid4()}")
    assert r.status_code == 404

async def test_availability_unknown_and_inactive_doctor(client, seeded):
    day = (datetime.now(CLINIC) + timedelta(days=3)).date().isoformat()
    missing = await client.get(f"/doctors/{uuid.uuid4()}/availability", params={"date": day})
    assert missing.status_code == 404

    async with SessionFactory() as db:
        clinic_id = seeded["doctor"].clinic_id
        inactive_id = (await db.execute(text(
            "INSERT INTO doctor (clinic_id, name, is_active) VALUES (:c, 'Inactive', false) RETURNING id"
        ), {"c": clinic_id})).scalar()
        await db.commit()
    inactive = await client.get(f"/doctors/{inactive_id}/availability", params={"date": day})
    assert inactive.status_code == 404

async def test_health_and_ready(client):
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/ready")).status_code == 200

async def test_unhandled_exception_handler():
    from app.main import unhandled
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [],
                       "query_string": b"", "state": {}})
    response = await unhandled(request, RuntimeError("boom"))
    assert response.status_code == 500

async def test_patient_upcoming_and_cursor(client, seeded):
    patient_id = seeded["patient"].id
    for hour in (10, 11):
        created = await book(client, seeded, 47, hour)
        assert created.status_code == 201
    first = await client.get(f"/patients/{patient_id}/appointments", params={"limit": 1})
    assert first.status_code == 200
    assert len(first.json()["items"]) == 1
    assert first.json()["next_cursor"] is not None

    cursor = first.json()["next_cursor"]
    second = await client.get(f"/patients/{patient_id}/appointments",
                              params={"limit": 1, "cursor": cursor})
    assert second.status_code == 200
    assert second.json()["items"][0]["id"] != first.json()["items"][0]["id"]

    missing = await client.get(f"/patients/{uuid.uuid4()}/appointments")
    assert missing.status_code == 404

async def test_sweep_expires_stale_holds(client, seeded):
    slot = future_slot(48, 10)
    async with SessionFactory() as db:
        stale_id = (await db.execute(text(
            "INSERT INTO appointment (doctor_id, patient_id, starts_at, ends_at, duration_minutes,"
            " status, hold_expires_at)"
            " VALUES (:d, :p, :s, :e, 30, 'pending', now() - interval '1 hour') RETURNING id"
        ), {"d": seeded["doctor"].id, "p": seeded["patient"].id,
            "s": slot, "e": slot + timedelta(minutes=30)})).scalar()
        await db.commit()

    from app.services.appointments import AppointmentService
    async with SessionFactory() as db:
        await AppointmentService(db).sweep_expired()
        await db.commit()
        status = (await db.execute(text(
            "select status from appointment where id = :i"
        ), {"i": stale_id})).scalar()
    assert status == "expired"
