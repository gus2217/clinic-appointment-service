import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import future_slot

pytestmark = pytest.mark.asyncio

async def test_health_and_root(client):
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/ready")).json() == {"status": "ready"}
    assert (await client.get("/")).status_code == 200

async def test_availability(client, seeded):
    d = future_slot(2).date().isoformat()
    r = await client.get(f"/doctors/{seeded['doctor'].id}/availability", params={"date": d})
    assert r.status_code == 200
    assert r.json()["slots"]

async def test_book_and_conflict(client, seeded):
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(2, 10).isoformat()}
    r1 = await client.post("/appointments", json=body)
    assert r1.status_code == 201, r1.text
    assert r1.json()["starts_at"].endswith("+03:00")
    r2 = await client.post("/appointments", json=body)
    assert r2.status_code == 409
    assert r2.json()["code"] == "SLOT_TAKEN"

async def test_outside_hours(client, seeded):
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(3, 20).isoformat()}
    r = await client.post("/appointments", json=body)
    assert r.status_code == 422
    assert r.json()["code"] == "OUTSIDE_WORKING_HOURS"

async def test_past(client, seeded):
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()}
    r = await client.post("/appointments", json=body)
    assert r.status_code == 422
    assert r.json()["code"] == "SLOT_IN_PAST"

async def test_cancel_and_double_cancel(client, seeded):
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(2, 11).isoformat()}
    created = await client.post("/appointments", json=body)
    appt_id = created.json()["id"]
    c1 = await client.patch(f"/appointments/{appt_id}/cancel", json={"reason": "unwell"})
    assert c1.status_code == 200 and c1.json()["status"] == "cancelled"
    c2 = await client.patch(f"/appointments/{appt_id}/cancel", json={"reason": "again"})
    assert c2.status_code == 409
    assert c2.json()["code"] == "APPOINTMENT_ALREADY_CANCELLED"

async def test_cancel_frees_slot(client, seeded):
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(2, 12).isoformat()}
    created = await client.post("/appointments", json=body)
    await client.patch(f"/appointments/{created.json()['id']}/cancel", json={"reason": "free"})
    again = await client.post("/appointments", json=body)
    assert again.status_code == 201

async def test_reschedule(client, seeded):
    s1 = future_slot(2, 13)
    s2 = future_slot(2, 14)
    created = await client.post("/appointments", json={
        "doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
        "starts_at": s1.isoformat()})
    appt_id = created.json()["id"]
    rs = await client.patch(f"/appointments/{appt_id}/reschedule", json={"starts_at": s2.isoformat()})
    assert rs.status_code == 200, rs.text
    assert datetime.fromisoformat(rs.json()["starts_at"]) == s2
    freed = await client.post("/appointments", json={
        "doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
        "starts_at": s1.isoformat()})
    assert freed.status_code == 201

async def test_get_and_patient_list(client, seeded):
    created = await client.post("/appointments", json={
        "doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
        "starts_at": future_slot(3, 10).isoformat()})
    appt_id = created.json()["id"]
    assert (await client.get(f"/appointments/{appt_id}")).status_code == 200
    listed = await client.get(f"/patients/{seeded['patient'].id}/appointments", params={"limit": 10})
    assert listed.status_code == 200
    assert listed.json()["items"]

async def test_idempotency(client, seeded):
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(4, 10).isoformat()}
    headers = {"Idempotency-Key": str(uuid.uuid4()), "X-Actor-Id": "patient-1"}
    r1 = await client.post("/appointments", json=body, headers=headers)
    r2 = await client.post("/appointments", json=body, headers=headers)
    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r2.headers["Idempotent-Replayed"] == "true"
    assert r1.json() == r2.json()
