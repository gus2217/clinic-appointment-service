import uuid
from datetime import UTC, time, timedelta

import httpx
import pytest

from app.directories import fixtures
from app.directories.contracts import DoctorView, PatientView, WorkingHours
from app.directories.doctors import HttpDoctorDirectory, build_doctor_directory
from app.directories.errors import DirectoryUnavailable
from app.directories.patients import HttpPatientDirectory, build_patient_directory


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


async def test_http_doctor_directory_time_off_follows_contract():
    from datetime import datetime

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
    assert rows[0].starts_at.astimezone(UTC).hour == 6


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
    from datetime import datetime

    adapter = build_doctor_directory()
    known = next(iter(fixtures.FIXTURE_DOCTORS))
    view = await adapter.get(known)
    assert view is not None and view.name == fixtures.FIXTURE_DOCTORS[known].name
    assert await adapter.get(uuid.uuid4()) is None
    now = datetime.now(UTC)
    assert await adapter.time_off(known, now, now + timedelta(days=1)) == ()


def test_http_mode_selects_remote_adapter(monkeypatch):
    from app.directories import doctors as module

    monkeypatch.setattr(module.settings, "directory_mode", "http")
    monkeypatch.setattr(module.settings, "doctors_url", "http://dir")
    assert isinstance(module.build_doctor_directory(), module.HttpDoctorDirectory)


def test_http_mode_without_url_fails_fast(monkeypatch):
    from app.directories import doctors as module

    monkeypatch.setattr(module.settings, "directory_mode", "http")
    monkeypatch.setattr(module.settings, "doctors_url", "")
    with pytest.raises(RuntimeError):
        module.build_doctor_directory()


async def test_http_patient_directory_404_returns_none():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
        base_url="http://dir",
    )
    assert await HttpPatientDirectory("http://dir", client=client).get(uuid.uuid4()) is None
    await client.aclose()


async def test_http_patient_directory_5xx_raises():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        base_url="http://dir",
    )
    with pytest.raises(DirectoryUnavailable):
        await HttpPatientDirectory("http://dir", client=client).get(uuid.uuid4())
    await client.aclose()


async def test_http_patient_directory_timeout_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://dir")
    with pytest.raises(DirectoryUnavailable):
        await HttpPatientDirectory("http://dir", client=client).get(uuid.uuid4())
    await client.aclose()


async def test_http_patient_directory_invalid_payload_raises():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"id": str(uuid.uuid4())})
        ),
        base_url="http://dir",
    )
    with pytest.raises(DirectoryUnavailable):
        await HttpPatientDirectory("http://dir", client=client).get(uuid.uuid4())
    await client.aclose()


def test_patient_http_mode_without_url_fails_fast(monkeypatch):
    from app.directories import patients as module

    monkeypatch.setattr(module.settings, "directory_mode", "http")
    monkeypatch.setattr(module.settings, "patients_url", "")
    with pytest.raises(RuntimeError):
        build_patient_directory()
