"""EXPLAIN (ANALYZE, BUFFERS) for the availability lookup.

Small tables legitimately seq-scan, so the check disables seq scans and asserts the
lookup can be served by an index instead.
"""
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

from app.db.session import SessionFactory
from app.directories import fixtures

CLINIC = ZoneInfo("Africa/Nairobi")

async def main() -> None:
    doctor_id = next(iter(fixtures.FIXTURE_DOCTORS))
    async with SessionFactory() as db:
        rows = (await db.execute(text("select count(*) from appointment"))).scalar()
        day = datetime.now(CLINIC) + timedelta(days=1)
        start = datetime.combine(day.date(), datetime.min.time(), tzinfo=CLINIC)
        end = start + timedelta(days=1)
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        result = await db.execute(
            text(
                "EXPLAIN (ANALYZE, BUFFERS) "
                "SELECT id FROM appointment "
                "WHERE doctor_id = :doctor AND status = 'booked' "
                "AND starts_at < :end AND ends_at > :start"
            ),
            {"doctor": doctor_id, "start": start, "end": end},
        )
        plan = "\n".join(row[0] for row in result)
        print(f"appointment rows: {rows}")
        print(plan)
        if "Index Scan" not in plan and "Bitmap" not in plan:
            raise SystemExit("FAIL: lookup cannot use an index")
        print("OK: index usable")

if __name__ == "__main__":
    asyncio.run(main())
