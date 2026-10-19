import asyncio

import pytest

from tests.conftest import future_slot

pytestmark = pytest.mark.asyncio

async def test_parallel_booking_one_winner(client, seeded):
    body = {"doctor_id": str(seeded["doctor"].id), "patient_id": str(seeded["patient"].id),
            "starts_at": future_slot(6, 10).isoformat()}
    results = await asyncio.gather(*[client.post("/appointments", json=body) for _ in range(10)])
    codes = [r.status_code for r in results]
    assert codes.count(201) == 1, codes
    assert codes.count(409) == 9, codes
