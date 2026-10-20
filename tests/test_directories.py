import uuid
from datetime import time

import httpx
import pytest

from app.db.session import SessionFactory
from app.directories.contracts import DoctorView, PatientView, WorkingHours
from app.directories.doctors import (
    HttpDoctorDirectory,
    LocalDoctorDirectory,
)
from app.directories.patients import HttpPatientDirectory, LocalPatientDirectory

pytestmark = pytest.mark.asyncio


async def test_local_doctor_directory_maps_to_view(seeded):
    async with SessionFactory() as db:
        view = await LocalDoctorDirectory(db).get(seeded["doctor"].id)
    assert view is not None
    assert view.id == seeded["doctor"].id
    assert view.name == "Dr. Test"
    assert view.is_active is True
    assert view.slot_minutes == 30
    assert {h.weekday for h in view.hours} == set(range(7))


async def test_local_doctor_directory_missing_returns_none():
    async with SessionFactory() as db:
        assert await LocalDoctorDirectory(db).get(uuid.uuid4()) is None


async def test_local_patient_directory_maps_to_view(seeded):
    async with SessionFactory() as db:
        view = await LocalPatientDirectory(db).get(seeded["patient"].id)
    assert view == PatientView(id=seeded["patient"].id, name="Patient One")


async def test_http_doctor_directory_follows_contract():
    doctor_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/doctors/{doctor_id}"
        return httpx.Response(200, json={
            "id": str(doctor_id), "name": "Dr. Remote", "is_active": True,
            "slot_minutes": 30,
            "working_hours": [{"weekday": 0, "start_time": "09:00:00", "end_time": "17:00:00"}],
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://dir")
    view = await HttpDoctorDirectory("http://dir", client=client).get(doctor_id)
    await client.aclose()
    assert view == DoctorView(
        id=doctor_id, name="Dr. Remote", is_active=True, slot_minutes=30,
        hours=(WorkingHours(0, time(9, 0), time(17, 0)),),
    )


async def test_http_doctor_directory_404_returns_none():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
        base_url="http://dir",
    )
    assert await HttpDoctorDirectory("http://dir", client=client).get(uuid.uuid4()) is None
    await client.aclose()


async def test_http_patient_directory_follows_contract():
    patient_id = uuid.uuid4()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"id": str(patient_id), "name": "Remote Patient"})
        ),
        base_url="http://dir",
    )
    view = await HttpPatientDirectory("http://dir", client=client).get(patient_id)
    await client.aclose()
    assert view == PatientView(id=patient_id, name="Remote Patient")


def test_http_mode_without_url_fails_fast(monkeypatch):
    from app.directories import doctors as module

    monkeypatch.setattr(module.settings, "directory_mode", "http")
    monkeypatch.setattr(module.settings, "doctors_url", "")
    with pytest.raises(RuntimeError):
        module.build_doctor_directory(None)  # type: ignore[arg-type]


def test_http_mode_selects_remote_adapter(monkeypatch):
    from app.directories import doctors as module

    monkeypatch.setattr(module.settings, "directory_mode", "http")
    monkeypatch.setattr(module.settings, "doctors_url", "http://dir")
    assert isinstance(module.build_doctor_directory(None), HttpDoctorDirectory)  # type: ignore[arg-type]
