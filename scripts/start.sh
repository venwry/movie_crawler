#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bind_host="${HOST:-127.0.0.1}"
if [[ "$bind_host" != "127.0.0.1" && "$bind_host" != "localhost" && "$bind_host" != "::1" && -z "${APP_PASSWORD:-}" ]]; then
  echo 'Set APP_PASSWORD before exposing Movie Harbor on the LAN. Login username: admin.' >&2
  exit 1
fi
exec .venv/bin/python -m uvicorn app.main:app --host "$bind_host" --port "${PORT:-8000}" --workers 1
