#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -eq 0 ]]; then
  set -- api
fi

wait_for_postgres() {
  python - <<'PY'
import os
import sys
import time

from sqlalchemy import create_engine, text

url = os.environ.get("SYNC_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not url:
    print("SYNC_DATABASE_URL or DATABASE_URL must be set", file=sys.stderr)
    sys.exit(1)

timeout = int(os.environ.get("WAIT_TIMEOUT_SECONDS", "90"))
deadline = time.monotonic() + timeout
last_error: Exception | None = None

while time.monotonic() < deadline:
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
        print("PostgreSQL is reachable")
        sys.exit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(2)

print(f"Timed out waiting for PostgreSQL: {last_error}", file=sys.stderr)
sys.exit(1)
PY
}

wait_for_qdrant() {
  python - <<'PY'
import os
import sys
import time
import urllib.error
import urllib.request

url = os.environ.get("QDRANT_URL", "")
if not url or url == ":memory:" or url.startswith("memory://"):
    print("Skipping Qdrant wait for in-memory vector backend")
    sys.exit(0)

health_url = url.rstrip("/") + "/healthz"
timeout = int(os.environ.get("WAIT_TIMEOUT_SECONDS", "90"))
deadline = time.monotonic() + timeout
last_error: Exception | None = None

while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(health_url, timeout=3) as response:
            if 200 <= response.status < 300:
                print("Qdrant is reachable")
                sys.exit(0)
            last_error = RuntimeError(f"unexpected HTTP status {response.status}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        last_error = exc
    time.sleep(2)

print(f"Timed out waiting for Qdrant at {health_url}: {last_error}", file=sys.stderr)
sys.exit(1)
PY
}

if [[ "$1" == "api" ]]; then
  shift
  wait_for_postgres
  wait_for_qdrant
  if [[ "${RUN_MIGRATIONS:-1}" == "1" ]]; then
    alembic upgrade head
  fi
  exec uvicorn "${APP_MODULE:-app.main:app}" \
    --host "${API_HOST:-0.0.0.0}" \
    --port "${API_PORT:-8000}" \
    "$@"
fi

exec "$@"
