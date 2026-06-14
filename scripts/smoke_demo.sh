#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

cp -n .env.example .env || true
if [[ "${SKIP_DOCKER:-0}" != "1" ]]; then
  docker compose up -d postgres qdrant
else
  printf '\nSKIP_DOCKER=1; skipping docker compose up -d postgres qdrant\n'
  export QDRANT_URL="${QDRANT_URL:-memory://smoke-demo}"
fi
alembic upgrade head
python3 scripts/seed_demo.py
python3 scripts/run_worker_once.py

API_BASE="${API_BASE:-http://localhost:8000}"

printf '\nGET /health\n'
curl -fsS "$API_BASE/health"

printf '\nPOST /search\n'
curl -fsS -X POST "$API_BASE/search" \
  -H 'Content-Type: application/json' \
  -d '{"query":"telemetry settings","mode":"search","filters":{},"top_k":5}'

printf '\nPOST /answer\n'
curl -fsS -X POST "$API_BASE/answer" \
  -H 'Content-Type: application/json' \
  -d '{"query":"How is telemetry configured?","mode":"answer","filters":{},"top_k":5}'

printf '\nSmoke demo completed.\n'
