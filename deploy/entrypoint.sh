#!/bin/sh
set -eu

if [ -n "${NOTIFY_HUB_SECRET_ENCRYPTION_KEY_FILE:-}" ]; then
  export NOTIFY_HUB_SECRET_ENCRYPTION_KEY="$(tr -d '\r\n' < "$NOTIFY_HUB_SECRET_ENCRYPTION_KEY_FILE")"
fi
if [ -n "${NOTIFY_HUB_JWT_SECRET_FILE:-}" ]; then
  export NOTIFY_HUB_JWT_SECRET="$(tr -d '\r\n' < "$NOTIFY_HUB_JWT_SECRET_FILE")"
fi
if [ -n "${NOTIFY_HUB_WECOM_SECRET_FILE:-}" ]; then
  export NOTIFY_HUB_WECOM_SECRET="$(tr -d '\r\n' < "$NOTIFY_HUB_WECOM_SECRET_FILE")"
fi
if [ -n "${NOTIFY_HUB_WECOM_CALLBACK_TOKEN_FILE:-}" ]; then
  export NOTIFY_HUB_WECOM_CALLBACK_TOKEN="$(tr -d '\r\n' < "$NOTIFY_HUB_WECOM_CALLBACK_TOKEN_FILE")"
fi
if [ -n "${NOTIFY_HUB_WECOM_CALLBACK_AES_KEY_FILE:-}" ]; then
  export NOTIFY_HUB_WECOM_CALLBACK_AES_KEY="$(tr -d '\r\n' < "$NOTIFY_HUB_WECOM_CALLBACK_AES_KEY_FILE")"
fi

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
