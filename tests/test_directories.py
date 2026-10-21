import uuid
from datetime import UTC, time

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


async def test_local_time_off_maps_to_view(seeded):
    from datetime import datetime, timedelta

    from sqlalchemy import text

    from app.db.session import SessionFactory
    from app.directories.doctors import LocalDoctorDirectory

    start = datetime.now().astimezone().replace(microsecond=0) + timedelta(days=3)
    end = start + timedelta(hours=2)
    async with SessionFactory() as db:
        await db.execute(text(
            "INSERT INTO doctor_time_off (doctor_id, starts_at, ends_at) VALUES (:d, :s, :e)"
        ), {"d": seeded["doctor"].id, "s": start, "e": end})
        await db.commit()
        rows = await LocalDoctorDirectory(db).time_off(
            seeded["doctor"].id, start - timedelta(hours=1), end + timedelta(hours=1)
        )
    assert len(rows) == 1
    assert rows[0].starts_at == start
    assert rows[0].ends_at == end


async def test_http_doctor_directory_time_off_follows_contract():
    from datetime import datetime, timedelta

    doctor_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/doctors/{doctor_id}/time-off"
        assert request.url.params.get("from")
        assert request.url.params.get("to")
        return httpx.Response(200, json=[{
            "starts_at": "2026-11-02T09:00:00+03:00",
            "ends_at": "2026-11-02T11:00:00+03:00",
        }])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://dir")
    start = datetime(2026, 11, 2, tzinfo=UTC)
    rows = await HttpDoctorDirectory("http://dir", client=client).time_off(
        doctor_id, start, start + timedelta(days=1)
    )
    await client.aclose()
    assert len(rows) == 1
    assert rows[0].starts_at.hour == 6  # 09:00+03:00 == 06:00Z


async def test_http_time_off_404_returns_empty():
    from datetime import datetime

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
        base_url="http://dir",
    )
    now = datetime.now(UTC)
    assert await HttpDoctorDirectory("http://dir", client=client).time_off(
        uuid.uuid4(), now, now
    ) == ()
    await client.aclose()


async def test_fixture_directory_is_deterministic():
    from datetime import datetime, timedelta

    from app.directories.doctors import FixtureDoctorDirectory
    from app.directories.fixtures import FIXTURE_DOCTORS

    adapter = FixtureDoctorDirectory()
    known = next(iter(FIXTURE_DOCTORS))
    view = await adapter.get(known)
    assert view is not None and view.name == FIXTURE_DOCTORS[known].name
    assert await adapter.get(uuid.uuid4()) is None
    now = datetime.now(UTC)
    assert await adapter.time_off(known, now, now + timedelta(days=1)) == ()


def test_fixture_mode_selects_fixture_adapter(monkeypatch):
    from app.directories import doctors as module

    monkeypatch.setattr(module.settings, "directory_mode", "fixture")
    assert isinstance(module.build_doctor_directory(None), module.FixtureDoctorDirectory)  # type: ignore[arg-type]
