#!/bin/sh
set -eu

# Migrations serialise on a PostgreSQL advisory lock, so two instances starting at
# once cannot race the schema. A short retry absorbs a cold database or a dropped
# connection without crash-looping the container.
python - <<'PY'
import os
import sys
import time

import psycopg
from alembic import command
from alembic.config import Config

LOCK_ID = 727042
url = os.environ["CLINIC_DATABASE_URL"]
for prefix in ("postgresql+psycopg://", "postgres://"):
    if url.startswith(prefix):
        url = "postgresql://" + url[len(prefix):]


def migrate() -> None:
    with psycopg.connect(url, connect_timeout=10) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            command.upgrade(Config("alembic.ini"), "head")
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))


for attempt in range(1, 11):
    try:
        migrate()
        break
    except Exception as exc:  # noqa: BLE001 - the message is the useful part
        print(f"migration attempt {attempt}/10 failed: {exc}", file=sys.stderr)
        if attempt == 10:
            raise
        time.sleep(min(2 ** attempt, 10))
PY

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --timeout-graceful-shutdown 30
