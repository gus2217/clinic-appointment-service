import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.db.session import SessionFactory
from tests.conftest import future_slot

pytestmark = pytest.mark.asyncio
CLINIC = ZoneInfo("Africa/Nairobi")

async def _book(client, seeded, day, hour=10, requires_approval=False, headers=None):
    body = {
        "doctor_id": str(seeded["doctor"].id),
        "patient_id": str(seeded["patient"].id),
        "starts_at": future_slot(day, hour).isoformat(),
        "requires_approval": requires_approval,
    }
    return await client.post("/appointments", json=body, headers=headers or {})

async def test_approve_pending(client, seeded):
    created = await _book(client, seeded, 10, 10, requires_approval=True)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "pending"
    appt_id = created.json()["id"]
    r = await client.patch(f"/appointments/{appt_id}/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "booked"
    events = await client.get(f"/appointments/{appt_id}/events")
    assert events.status_code == 200
    assert [e["event_type"] for e in events.json()] == ["approved"]

async def test_reject_pending(client, seeded):
    created = await _book(client, seeded, 10, 11, requires_approval=True)
    appt_id = created.json()["id"]
    r = await client.patch(f"/appointments/{appt_id}/reject", json={"reason": "doctor unavailable"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

async def test_complete_and_no_show(client, seeded):
    first = await _book(client, seeded, 10, 12)
    assert (await client.patch(f"/appointments/{first.json()['id']}/complete")).json()["status"] == "completed"
    second = await _book(client, seeded, 10, 13)
    assert (await client.patch(f"/appointments/{second.json()['id']}/no-show")).json()["status"] == "no_show"

async def test_cancel_pending(client, seeded):
    created = await _book(client, seeded, 10, 14, requires_approval=True)
    r = await client.patch(f"/appointments/{created.json()['id']}/cancel", json={"reason": "no longer needed"})
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"

async def test_illegal_transitions(client, seeded):
    created = await _book(client, seeded, 10, 15)
    appt_id = created.json()["id"]
    again = await client.patch(f"/appointments/{appt_id}/approve")
    assert again.status_code == 409
    cancelled = await client.patch(f"/appointments/{appt_id}/cancel", json={"reason": "done"})
    assert cancelled.status_code == 200
    resched = await client.patch(f"/appointments/{appt_id}/reschedule",
                                 json={"starts_at": future_slot(10, 16).isoformat()})
    assert resched.status_code == 409

async def test_expired_hold_is_swept(client, seeded):
    created = await _book(client, seeded, 11, 10, requires_approval=True)
    appt_id = created.json()["id"]
    async with SessionFactory() as db:
        await db.execute(text("UPDATE appointment SET hold_expires_at = now() - interval '1 hour' WHERE id = :id"),
                         {"id": appt_id})
        await db.commit()
    retry = await _book(client, seeded, 11, 10)
    assert retry.status_code == 201, retry.text

async def test_not_found_paths(client, seeded):
    missing = uuid.uuid4()
    assert (await client.get(f"/appointments/{missing}")).status_code == 404
    assert (await client.get(f"/appointments/{missing}/events")).status_code == 404
    cancel = await client.patch(f"/appointments/{missing}/cancel", json={"reason": "valid reason"})
    assert cancel.status_code == 404
    assert (await client.get(f"/doctors/{missing}/availability", params={"date": "2026-11-02"})).status_code == 404
    assert (await client.get(f"/patients/{missing}/appointments")).status_code == 404

async def test_idempotency_conflict(client, seeded):
    headers = {"Idempotency-Key": str(uuid.uuid4()), "X-Actor-Id": "p-1"}
    first = await _book(client, seeded, 12, 10, headers=headers)
    assert first.status_code == 201
    body = {
        "doctor_id": str(seeded["doctor"].id),
        "patient_id": str(seeded["patient"].id),
        "starts_at": future_slot(12, 11).isoformat(),
    }
    second = await client.post("/appointments", json=body, headers=headers)
    assert second.status_code == 400
    assert second.json()["code"] == "IDEMPOTENCY_CONFLICT"

async def test_availability_returns_doctor_timezone(client, seeded):
    day = (datetime.now(CLINIC) + timedelta(days=3)).date()
    r = await client.get(f"/doctors/{seeded['doctor'].id}/availability",
                         params={"date": day.isoformat()})
    assert r.status_code == 200
    assert r.json()["timezone"] == "Africa/Nairobi"
