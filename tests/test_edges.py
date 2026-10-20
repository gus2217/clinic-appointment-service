import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.db.session import SessionFactory
from tests.conftest import future_slot

pytestmark = pytest.mark.asyncio
CLINIC = ZoneInfo("Africa/Nairobi")

@pytest.fixture()
def auth_on(monkeypatch):
    from app.api import auth
    monkeypatch.setattr(auth.settings, "auth_mode", "gateway")
    return auth

async def _book(client, seeded, day, hour, headers=None, **extra):
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(day, hour).isoformat()}
    body.update(extra)
    return await client.post("/appointments", json=body, headers=headers or {})

async def test_naive_datetime_is_clinic_local(client, seeded):
    slot = future_slot(30, 10).replace(tzinfo=None)
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": slot.isoformat()}
    r = await client.post("/appointments", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["starts_at"].endswith("+03:00")

async def test_doctor_without_hours_has_no_slots(client):
    async with SessionFactory() as db:
        clinic = (await db.execute(text("select id from clinic limit 1"))).scalar()
        if clinic is None:
            await db.execute(text("INSERT INTO clinic (name) VALUES ('Edge Clinic')"))
            await db.commit()
            clinic = (await db.execute(text("select id from clinic limit 1"))).scalar()
        doctor_id = (await db.execute(text(
            "INSERT INTO doctor (clinic_id, name) VALUES (:c, 'No Hours') RETURNING id"
        ), {"c": clinic})).scalar()
        await db.commit()
    day = (datetime.now(CLINIC) + timedelta(days=3)).date()
    r = await client.get(f"/doctors/{doctor_id}/availability", params={"date": day.isoformat()})
    assert r.status_code == 200
    assert r.json()["slots"] == []

async def test_doctor_cannot_reschedule(client, seeded, auth_on):
    admin = {"X-Actor-Id": "admin-1", "X-Actor-Role": "admin"}
    created = await _book(client, seeded, 31, 10, headers=admin)
    appt_id = created.json()["id"]
    headers = {"X-Actor-Id": str(seeded["doctor"].id), "X-Actor-Role": "doctor"}
    r = await client.patch(f"/appointments/{appt_id}/reschedule",
                           json={"starts_at": future_slot(31, 11).isoformat()}, headers=headers)
    assert r.status_code == 403

async def test_doctor_can_cancel_own(client, seeded, auth_on):
    admin = {"X-Actor-Id": "admin-1", "X-Actor-Role": "admin"}
    created = await _book(client, seeded, 31, 12, headers=admin)
    headers = {"X-Actor-Id": str(seeded["doctor"].id), "X-Actor-Role": "doctor"}
    r = await client.patch(f"/appointments/{created.json()['id']}/cancel",
                           json={"reason": "doctor unavailable"}, headers=headers)
    assert r.status_code == 200

async def test_admin_can_list_patient(client, seeded, auth_on):
    headers = {"X-Actor-Id": "admin-1", "X-Actor-Role": "admin"}
    r = await client.get(f"/patients/{seeded['patient'].id}/appointments", headers=headers)
    assert r.status_code == 200

async def test_patient_cannot_list_other(client, seeded, auth_on):
    headers = {"X-Actor-Id": str(uuid.uuid4()), "X-Actor-Role": "patient"}
    r = await client.get(f"/patients/{seeded['patient'].id}/appointments", headers=headers)
    assert r.status_code == 403

async def _create(client, seeded, day, hour, headers):
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(day, hour).isoformat()}
    return await client.post("/appointments", json=body, headers=headers)

async def test_get_as_wrong_patient_403(client, seeded, auth_on):
    admin = {"X-Actor-Id": "admin-1", "X-Actor-Role": "admin"}
    created = await _create(client, seeded, 32, 10, admin)
    headers = {"X-Actor-Id": str(uuid.uuid4()), "X-Actor-Role": "patient"}
    r = await client.get(f"/appointments/{created.json()['id']}", headers=headers)
    assert r.status_code == 403

async def test_events_as_wrong_patient_403(client, seeded, auth_on):
    admin = {"X-Actor-Id": "admin-1", "X-Actor-Role": "admin"}
    created = await _create(client, seeded, 32, 11, admin)
    headers = {"X-Actor-Id": str(uuid.uuid4()), "X-Actor-Role": "patient"}
    r = await client.get(f"/appointments/{created.json()['id']}/events", headers=headers)
    assert r.status_code == 403

async def test_cancel_as_wrong_patient_403(client, seeded, auth_on):
    admin = {"X-Actor-Id": "admin-1", "X-Actor-Role": "admin"}
    created = await _create(client, seeded, 32, 12, admin)
    headers = {"X-Actor-Id": str(uuid.uuid4()), "X-Actor-Role": "patient"}
    r = await client.patch(f"/appointments/{created.json()['id']}/cancel",
                           json={"reason": "not mine"}, headers=headers)
    assert r.status_code == 403

async def test_doctor_cannot_book(client, seeded, auth_on):
    headers = {"X-Actor-Id": str(seeded["doctor"].id), "X-Actor-Role": "doctor"}
    r = await _create(client, seeded, 32, 13, headers)
    assert r.status_code == 403

async def test_complete_and_noshow_require_booked(client, seeded):
    pending = await client.post("/appointments", json={
        "doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
        "starts_at": future_slot(33, 10).isoformat(), "requires_approval": True,
    })
    appt_id = pending.json()["id"]
    assert (await client.patch(f"/appointments/{appt_id}/complete")).status_code == 409
    assert (await client.patch(f"/appointments/{appt_id}/no-show")).status_code == 409

async def test_reject_booked_409(client, seeded):
    booked = await client.post("/appointments", json={
        "doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
        "starts_at": future_slot(33, 11).isoformat(),
    })
    r = await client.patch(f"/appointments/{booked.json()['id']}/reject",
                           json={"reason": "too late"})
    assert r.status_code == 409

