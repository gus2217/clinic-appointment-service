#!/bin/sh
set -e

alembic upgrade head

# No --proxy-headers on purpose: the TCP peer must stay the gateway so the
# trusted-proxy CIDR check cannot be bypassed with a forged X-Forwarded-For.
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --timeout-graceful-shutdown 30
