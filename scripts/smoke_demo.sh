#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

prefer_command() {
  local configured="$1"
  local venv_path="$2"
  local fallback="$3"

  if [[ -n "$configured" ]]; then
    printf '%s\n' "$configured"
  elif [[ -x "$venv_path" ]]; then
    printf '%s\n' "$venv_path"
  else
    printf '%s\n' "$fallback"
  fi
}

PYTHON="$(prefer_command "${PYTHON:-}" "$ROOT/.venv/bin/python" "python3")"
ALEMBIC="$(prefer_command "${ALEMBIC:-}" "$ROOT/.venv/bin/alembic" "alembic")"
API_BASE="${API_BASE:-http://localhost:8000}"
export API_BASE
WORKER_JSON="$(mktemp)"
RUN_JSON="$(mktemp)"
SEARCH_JSON="$(mktemp)"
ANSWER_JSON="$(mktemp)"
trap 'rm -f "$WORKER_JSON" "$RUN_JSON" "$SEARCH_JSON" "$ANSWER_JSON"' EXIT

require_local_api_base() {
  if [[ "${ALLOW_NONLOCAL_SMOKE_API:-0}" == "1" ]]; then
    return 0
  fi

  "$PYTHON" - <<'PY'
import os
import sys
from urllib.parse import urlparse


def safe_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.hostname is None:
        return "<invalid-or-local-api-base>"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return parsed._replace(netloc=host, params="", query="", fragment="").geturl()


def is_local_api_base(value: str) -> bool:
    host = urlparse(value).hostname
    if host is None:
        return False
    normalized = host.lower()
    return normalized in {"localhost", "127.0.0.1", "::1"}


api_base = os.environ.get("API_BASE", "http://localhost:8000")
if not is_local_api_base(api_base):
    print(
        "Refusing to run smoke demo against a non-local API_BASE. "
        "Set ALLOW_NONLOCAL_SMOKE_API=1 to override. Offending setting: "
        f"API_BASE={safe_url(api_base)}",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
}

require_local_smoke_db() {
  if [[ "${ALLOW_NONLOCAL_SMOKE_DB:-0}" == "1" ]]; then
    return 0
  fi

  "$PYTHON" - <<'PY'
import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlparse


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


LOCAL_DATABASE_HOSTS = {"localhost", "127.0.0.1", "::1"}


def query_host_values(parsed):
    return [
        value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() == "host"
    ]


def is_local_database_host(value: str) -> bool:
    return value.lower() in LOCAL_DATABASE_HOSTS


def safe_query_host(value: str) -> str:
    return (value or "<empty>").replace("\r", "%0D").replace("\n", "%0A")


def safe_query_string(parsed) -> str:
    return "&".join(f"host={safe_query_host(host)}" for host in query_host_values(parsed))


def safe_url(value: str) -> str:
    parsed = urlparse(value)
    safe_query = safe_query_string(parsed)
    if parsed.hostname is None:
        scheme = parsed.scheme or "database"
        if scheme.lower().startswith("sqlite") or value in {":memory:", ""}:
            return f"{scheme}:<local>"
        result = f"{scheme}://<missing-host>"
        return f"{result}?{safe_query}" if safe_query else result
    netloc = parsed.hostname
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return parsed._replace(netloc=netloc, params="", query=safe_query, fragment="").geturl()


def is_local_database_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme.startswith("sqlite") or value == ":memory:":
        return True
    if any(not is_local_database_host(host) for host in query_host_values(parsed)):
        return False
    host = parsed.hostname
    if host is None:
        # A hostless non-sqlite database URL is not clearly local; require an override.
        return False
    return is_local_database_host(host)


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

assert_worker_result() {
  "$PYTHON" - "$1" <<'PY'
import json
import sys
from pathlib import Path
from typing import Any

with Path(sys.argv[1]).open() as handle:
    payload: dict[str, Any] = json.load(handle)

if payload != {"claimed": 1, "succeeded": 1, "partial": 0, "failed": 0}:
    raise SystemExit(
        "Targeted worker run did not complete exactly one run successfully: "
        f"{payload!r}"
    )
PY
}

assert_run_status_success() {
  "$PYTHON" - "$1" "$2" <<'PY'
import json
import sys
from pathlib import Path
from typing import Any

with Path(sys.argv[1]).open() as handle:
    payload: dict[str, Any] = json.load(handle)
expected_run_id = sys.argv[2]

if payload.get("id") != expected_run_id:
    raise SystemExit(
        f"/runs returned id {payload.get('id')!r}; expected {expected_run_id!r}"
    )
if payload.get("status") != "success":
    raise SystemExit(
        f"/runs/{expected_run_id} status was {payload.get('status')!r}; expected 'success'"
    )
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
from typing import Any


def evidence_text(item: dict[str, Any]) -> str:
    fields = ("title", "snippet", "canonical_url", "url", "source_url")
    return " ".join(str(item.get(field) or "") for field in fields).casefold()


with Path(sys.argv[1]).open() as handle:
    payload = json.load(handle)

supporting_evidence = payload.get("supporting_evidence")
if not isinstance(supporting_evidence, list) or not supporting_evidence:
    raise SystemExit("/answer supporting_evidence list was empty")
answer = payload.get("answer")
if not isinstance(answer, str) or "[1]" not in answer:
    raise SystemExit('/answer did not include citation "[1]"')
answer_text = answer.casefold()
if "telemetry" not in answer_text or "settings" not in answer_text:
    raise SystemExit("/answer text did not reference demo telemetry/settings content")
if not any(
    "telemetry" in evidence_text(item) and "settings" in evidence_text(item)
    for item in supporting_evidence
    if isinstance(item, dict)
):
    raise SystemExit("/answer supporting_evidence did not reference demo telemetry/settings")
PY
}

if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
  printf 'Created .env from .env.example for smoke demo defaults.\n'
fi
require_local_smoke_db
if [[ "${ALLOW_NONLOCAL_SMOKE_DB:-0}" == "1" ]]; then
  # The smoke DB override covers both Alembic and seed_demo.py so it does not fail halfway.
  export ALLOW_NONLOCAL_DEMO_SEED=1
  printf 'ALLOW_NONLOCAL_SMOKE_DB=1; exporting ALLOW_NONLOCAL_DEMO_SEED=1 for seed_demo.py.\n'
fi
require_local_api_base

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
printf '\nWorker run for %s\n' "$RUN_ID"
"$PYTHON" scripts/run_worker_once.py --json --require-success --run-id "$RUN_ID" | tee "$WORKER_JSON"
assert_worker_result "$WORKER_JSON"

wait_for_api

printf '\nGET /runs/$RUN_ID (%s)\n' "$RUN_ID"
curl -fsS "$API_BASE/runs/$RUN_ID" | tee "$RUN_JSON"
printf '\n'
assert_run_status_success "$RUN_JSON" "$RUN_ID"

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
