#!/usr/bin/env bash
# Single scheduled collection; install dependencies when building, not on a timer.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd ../../.. && pwd)"
if [[ -z "${PY:-}" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then PY="$ROOT/.venv/bin/python"
  else PY="$(command -v python3 || command -v python)"; fi
fi
echo "=== $(date '+%F %T') 采集开始 ==="
exec "$PY" main.py --scheduled --json
