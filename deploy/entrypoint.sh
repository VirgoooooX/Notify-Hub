#!/bin/sh
set -eu

if [ "${1:-}" = "migrate" ]; then
  cd /app/backend
  exec alembic upgrade head
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

(cd /app/backend && alembic upgrade head)

exec uvicorn app.main:app \
  --host "${APP_HOST:-0.0.0.0}" \
  --port "${APP_PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
