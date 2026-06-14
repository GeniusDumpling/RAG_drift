#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
ALEMBIC="${ALEMBIC:-alembic}"
API_BASE="${API_BASE:-http://localhost:8000}"
SEARCH_JSON="$(mktemp)"
ANSWER_JSON="$(mktemp)"
trap 'rm -f "$SEARCH_JSON" "$ANSWER_JSON"' EXIT

require_local_smoke_db() {
  if [[ "${ALLOW_NONLOCAL_SMOKE_DB:-0}" == "1" ]]; then
    return 0
  fi

  "$PYTHON" - <<'PY'
import os
import sys
from pathlib import Path
from urllib.parse import urlparse


def dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def configured_value(name: str) -> str:
    return os.environ.get(name) or env_file.get(name) or env_example.get(name) or ""


def safe_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.hostname is None:
        return value
    netloc = parsed.hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return parsed._replace(netloc=netloc).geturl()


def is_local_database_url(value: str) -> bool:
    if not value:
        return True
    host = urlparse(value).hostname
    if host is None:
        return True
    normalized = host.lower()
    return normalized == "localhost" or normalized == "::1" or normalized.startswith("127.")


env_file = dotenv_values(Path(".env"))
env_example = dotenv_values(Path(".env.example"))
unsafe = []
for variable in ("DATABASE_URL", "SYNC_DATABASE_URL"):
    value = configured_value(variable)
    if not is_local_database_url(value):
        unsafe.append(f"{variable}={safe_url(value)}")

if unsafe:
    print(
        "Refusing to run smoke demo against a non-local database. "
        "Set ALLOW_NONLOCAL_SMOKE_DB=1 to override. Offending setting(s): "
        + ", ".join(unsafe),
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
}

wait_for_tcp() {
  local host="$1"
  local port="$2"
  local label="$3"

  for _attempt in {1..30}; do
    if "$PYTHON" -c 'import socket, sys; sock = socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=1); sock.close()' "$host" "$port" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  printf 'Timed out waiting for %s on %s:%s\n' "$label" "$host" "$port" >&2
  return 1
}

wait_for_http() {
  local url="$1"
  local label="$2"

  for _attempt in {1..30}; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  printf 'Timed out waiting for %s at %s\n' "$label" "$url" >&2
  return 1
}

wait_for_api() {
  for _attempt in {1..30}; do
    if curl -fsS "$API_BASE/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  printf 'Timed out waiting for API at %s\n' "$API_BASE" >&2
  return 1
}

extract_seed_run_id() {
  SEED_JSON="$1" "$PYTHON" - <<'PY'
import json
import os

payload = json.loads(os.environ["SEED_JSON"])
run_id = payload.get("run_id")
if not isinstance(run_id, str) or not run_id:
    raise SystemExit("seed_demo.py did not return a run_id")
print(run_id)
PY
}

assert_search_response() {
  "$PYTHON" - "$1" <<'PY'
import json
import sys
from pathlib import Path
from typing import Any


def evidence_text(item: dict[str, Any]) -> str:
    fields = ("title", "snippet", "canonical_url", "url", "source_url")
    return " ".join(str(item.get(field) or "") for field in fields).casefold()


with Path(sys.argv[1]).open() as handle:
    payload = json.load(handle)

evidence = payload.get("evidence")
if not isinstance(evidence, list) or not evidence:
    raise SystemExit("/search evidence list was empty")
if not any("telemetry" in evidence_text(item) and "settings" in evidence_text(item) for item in evidence if isinstance(item, dict)):
    raise SystemExit("/search evidence did not reference demo telemetry/settings in title/snippet/url")
PY
}

assert_answer_response() {
  "$PYTHON" - "$1" <<'PY'
import json
import sys
from pathlib import Path

with Path(sys.argv[1]).open() as handle:
    payload = json.load(handle)

supporting_evidence = payload.get("supporting_evidence")
if not isinstance(supporting_evidence, list) or not supporting_evidence:
    raise SystemExit("/answer supporting_evidence list was empty")
answer = payload.get("answer")
if not isinstance(answer, str) or "[1]" not in answer:
    raise SystemExit('/answer did not include citation "[1]"')
PY
}

cp -n .env.example .env || true
require_local_smoke_db

if [[ "${SKIP_DOCKER:-0}" != "1" ]]; then
  if docker compose up --help 2>&1 | grep -q -- '--wait'; then
    docker compose up -d --wait postgres qdrant
  else
    docker compose up -d postgres qdrant
    wait_for_tcp 127.0.0.1 54329 postgres
    wait_for_http "${QDRANT_URL:-http://localhost:6333}" qdrant
  fi
else
  printf '\nSKIP_DOCKER=1; skipping docker compose up -d postgres qdrant\n'
  export QDRANT_URL="${QDRANT_URL:-memory://smoke-demo}"
fi

"$ALEMBIC" upgrade head
SEED_JSON="$($PYTHON scripts/seed_demo.py)"
printf '%s\n' "$SEED_JSON"
RUN_ID="$(extract_seed_run_id "$SEED_JSON")"
"$PYTHON" scripts/run_worker_once.py --run-id "$RUN_ID"

wait_for_api

printf '\nGET /health\n'
curl -fsS "$API_BASE/health"
printf '\n'

printf '\nPOST /search\n'
curl -fsS -X POST "$API_BASE/search" \
  -H 'Content-Type: application/json' \
  -d '{"query":"telemetry settings","mode":"search","filters":{},"top_k":5}' | tee "$SEARCH_JSON"
printf '\n'
assert_search_response "$SEARCH_JSON"

printf '\nPOST /answer\n'
curl -fsS -X POST "$API_BASE/answer" \
  -H 'Content-Type: application/json' \
  -d '{"query":"How is telemetry configured?","mode":"answer","filters":{},"top_k":5}' | tee "$ANSWER_JSON"
printf '\n'
assert_answer_response "$ANSWER_JSON"

printf '\nSmoke demo completed.\n'
