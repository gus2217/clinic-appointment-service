"""Concurrent booking load: one free slot, many requests, exactly one winner."""
import asyncio
import statistics
import sys
import time as clock
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from httpx import ASGITransport, AsyncClient

from app.directories import fixtures
from app.main import app

CLINIC = ZoneInfo("Africa/Nairobi")

async def main(concurrency: int = 50) -> None:
    doctor = next(iter(fixtures.FIXTURE_DOCTORS))
    patient = next(iter(fixtures.FIXTURE_PATIENTS))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        slot = None
        for offset in range(8, 30):
            day = (datetime.now(CLINIC) + timedelta(days=offset)).date()
            response = await client.get(
                f"/doctors/{doctor}/availability", params={"date": day.isoformat()}
            )
            slots = response.json().get("slots", [])
            if slots:
                slot = slots[0]["starts_at"]
                break
        if slot is None:
            print("no free slot found")
            return
        print("slot:", slot)
        body = {"doctor_id": str(doctor), "patient_id": str(patient), "starts_at": slot}

        async def one() -> tuple[int, float]:
            started = clock.perf_counter()
            response = await client.post("/appointments", json=body)
            return response.status_code, clock.perf_counter() - started

        results = await asyncio.gather(*[one() for _ in range(concurrency)])
    codes = [code for code, _ in results]
    durations = sorted(duration for _, duration in results)
    p50 = statistics.median(durations)
    p95 = durations[int(len(durations) * 0.95) - 1]
    print(f"requests={concurrency} 201={codes.count(201)} 409={codes.count(409)}")
    print(f"p50={p50*1000:.1f}ms p95={p95*1000:.1f}ms")
    if codes.count(201) != 1:
        raise SystemExit("FAIL: expected exactly one successful booking")

if __name__ == "__main__":
    asyncio.run(main())
