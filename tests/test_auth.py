import uuid
import pytest
from tests.conftest import future_slot

pytestmark = pytest.mark.asyncio

@pytest.fixture()
def auth_on(monkeypatch):
    from app.api import deps
    monkeypatch.setattr(deps.settings, "auth_required", True)
    return deps

async def test_missing_headers_401(client, seeded, auth_on):
    r = await client.get(f"/appointments/{uuid.uuid4()}")
    assert r.status_code == 401

async def test_patient_books_own_slot(client, seeded, auth_on):
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(7, 10).isoformat()}
    headers = {"X-Actor-Id": str(seeded["patient"].id), "X-Actor-Role": "patient"}
    r = await client.post("/appointments", json=body, headers=headers)
    assert r.status_code == 201, r.text

async def test_patient_cannot_book_for_another(client, seeded, auth_on):
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(7, 11).isoformat()}
    headers = {"X-Actor-Id": str(uuid.uuid4()), "X-Actor-Role": "patient"}
    r = await client.post("/appointments", json=body, headers=headers)
    assert r.status_code == 403

async def test_patient_cannot_approve(client, seeded, auth_on):
    admin = {"X-Actor-Id": "admin-1", "X-Actor-Role": "admin"}
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(7, 12).isoformat(), "requires_approval": True}
    created = await client.post("/appointments", json=body, headers=admin)
    appt_id = created.json()["id"]
    headers = {"X-Actor-Id": str(seeded["patient"].id), "X-Actor-Role": "patient"}
    r = await client.patch(f"/appointments/{appt_id}/approve", headers=headers)
    assert r.status_code == 403

async def test_doctor_can_approve(client, seeded, auth_on):
    admin = {"X-Actor-Id": "admin-1", "X-Actor-Role": "admin"}
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(7, 13).isoformat(), "requires_approval": True}
    created = await client.post("/appointments", json=body, headers=admin)
    appt_id = created.json()["id"]
    headers = {"X-Actor-Id": str(seeded["doctor"].id), "X-Actor-Role": "doctor"}
    r = await client.patch(f"/appointments/{appt_id}/approve", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "booked"
