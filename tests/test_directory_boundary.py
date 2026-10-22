import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.directories.cache import TTLCache
from app.directories.doctors import HttpDoctorDirectory
from app.directories.errors import DirectoryUnavailable


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def doctor_payload(doctor_id: uuid.UUID) -> dict[str, object]:
    return {
        "id": str(doctor_id), "name": "Dr. Remote", "is_active": True,
        "slot_minutes": 30,
        "working_hours": [{"weekday": 0, "start_time": "09:00:00", "end_time": "17:00:00"}],
    }


async def test_profile_is_cached():
    calls = {"n": 0}
    doctor_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=doctor_payload(doctor_id))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://dir")
    adapter = HttpDoctorDirectory("http://dir", client=client)
    assert await adapter.get(doctor_id)
    assert await adapter.get(doctor_id)
    await client.aclose()
    assert calls["n"] == 1


async def test_profile_cache_expires():
    clock = Clock()
    calls = {"n": 0}
    doctor_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=doctor_payload(doctor_id))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://dir")
    adapter = HttpDoctorDirectory("http://dir", client=client, profile_ttl=10, clock=clock)
    await adapter.get(doctor_id)
    clock.now = 11
    await adapter.get(doctor_id)
    await client.aclose()
    assert calls["n"] == 2


async def test_time_off_is_cached_and_fresh_bypasses():
    clock = Clock()
    calls = {"n": 0}
    doctor_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://dir")
    adapter = HttpDoctorDirectory("http://dir", client=client, time_off_ttl=60, clock=clock)
    start = datetime(2026, 11, 2, tzinfo=UTC)
    await adapter.time_off(doctor_id, start, start + timedelta(days=1))
    await adapter.time_off(doctor_id, start, start + timedelta(days=1))
    assert calls["n"] == 1
    await adapter.time_off(doctor_id, start, start + timedelta(days=1), fresh=True)
    await client.aclose()
    assert calls["n"] == 2


async def test_upstream_5xx_raises_directory_unavailable():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
        base_url="http://dir",
    )
    adapter = HttpDoctorDirectory("http://dir", client=client)
    with pytest.raises(DirectoryUnavailable):
        await adapter.get(uuid.uuid4())
    await client.aclose()


async def test_upstream_timeout_raises_directory_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://dir")
    adapter = HttpDoctorDirectory("http://dir", client=client)
    now = datetime.now(UTC)
    with pytest.raises(DirectoryUnavailable):
        await adapter.time_off(uuid.uuid4(), now, now)
    await client.aclose()


async def test_availability_returns_503_with_retry_after(client, monkeypatch):
    from app.api.routes import availability as route

    class Down:
        async def get(self, doctor_id: uuid.UUID) -> None:
            raise DirectoryUnavailable("down")

        async def time_off(self, doctor_id: uuid.UUID, start: datetime,
                           end: datetime, *, fresh: bool = False) -> None:
            raise DirectoryUnavailable("down")

    monkeypatch.setattr(route, "build_doctor_directory", lambda db: Down())
    r = await client.get(f"/doctors/{uuid.uuid4()}/availability", params={"date": "2026-11-02"})
    assert r.status_code == 503
    assert r.json()["code"] == "DIRECTORY_UNAVAILABLE"
    assert r.headers.get("retry-after")


async def test_version_endpoint(client):
    r = await client.get("/version")
    assert r.status_code == 200
    assert "commit" in r.json()


async def test_build_shares_process_wide_caches(monkeypatch):
    from app.directories import doctors as module

    monkeypatch.setattr(module.settings, "directory_mode", "http")
    monkeypatch.setattr(module.settings, "doctors_url", "http://dir")
    first = module.build_doctor_directory()
    second = module.build_doctor_directory()
    assert isinstance(first, module.HttpDoctorDirectory)
    assert first._profiles is second._profiles
    assert first._time_off is second._time_off
    assert first._locks is second._locks


async def test_shared_cache_skips_second_upstream_call():
    from app.directories.cache import TTLCache

    calls = {"first": 0, "second": 0}
    doctor_id = uuid.uuid4()

    def first_handler(request: httpx.Request) -> httpx.Response:
        calls["first"] += 1
        return httpx.Response(200, json=doctor_payload(doctor_id))

    def second_handler(request: httpx.Request) -> httpx.Response:
        calls["second"] += 1
        return httpx.Response(500)

    first_client = httpx.AsyncClient(
        transport=httpx.MockTransport(first_handler), base_url="http://dir"
    )
    second_client = httpx.AsyncClient(
        transport=httpx.MockTransport(second_handler), base_url="http://dir"
    )
    profiles = TTLCache(600)
    time_off = TTLCache(60)
    locks: dict[object, object] = {}

    first = HttpDoctorDirectory(
        "http://dir", client=first_client,
        profile_cache=profiles, time_off_cache=time_off, locks=locks,
    )
    second = HttpDoctorDirectory(
        "http://dir", client=second_client,
        profile_cache=profiles, time_off_cache=time_off, locks=locks,
    )

    assert await first.get(doctor_id)
    assert await second.get(doctor_id)
    await first_client.aclose()
    await second_client.aclose()
    assert calls == {"first": 1, "second": 0}


def test_cache_clear_removes_entries():
    cache: TTLCache[int] = TTLCache(ttl_seconds=60)
    cache.set("k", 1)
    assert cache.get("k") == 1
    cache.clear()
    assert cache.get("k") is None
