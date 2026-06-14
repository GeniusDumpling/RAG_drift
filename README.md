# Intelligence RAG Prototype

A modular monolith prototype for observable open-source intelligence RAG.

## Local setup

```bash
test -f .env || cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Demo paths

Use either the smoke-script path or the manual path. Do not manually seed/process a run and then
run the smoke script expecting it to verify that same run: the smoke script seeds and
processes its own targeted run.

### Smoke-script path (API already running)

Start the API in one terminal:

```bash
uvicorn app.main:app --app-dir backend --reload
```

Run the smoke script in another terminal:

```bash
bash scripts/smoke_demo.sh
```

By default, the smoke script uses the Docker Compose PostgreSQL and Qdrant services.
This Docker/Qdrant mode is the full cross-process vector smoke path: the worker writes
vectors to Qdrant and the API reads them back from the same vector store. The script has
local side effects: it may create `.env` from `.env.example` when `.env` is missing,
starts Docker services unless skipped, runs Alembic migrations, seeds demo data,
processes one targeted seeded run, writes demo vectors to the configured local vector
backend, and verifies `/runs`, `/search`, and `/answer` against the API.

Safety guards refuse non-local API, database, and `QDRANT_URL` settings by default. If
`ALLOW_NONLOCAL_SMOKE_DB=1` is set, the script also exports
`ALLOW_NONLOCAL_DEMO_SEED=1` so the Alembic and seed steps share one explicit DB
override. Set `ALLOW_NONLOCAL_SMOKE_VECTOR=1` only when you intentionally want the smoke
script to use a non-local `QDRANT_URL`; guard error messages print sanitized URLs without
credentials.

If PostgreSQL is already available and you want to skip Docker Compose services, use the
local-memory-vector path as a convenience mode and start the API with the same vector
setting:

```bash
# Terminal 1
QDRANT_URL=memory://smoke-demo uvicorn app.main:app --app-dir backend --reload

# Terminal 2
SKIP_DOCKER=1 QDRANT_URL=memory://smoke-demo bash scripts/smoke_demo.sh
```

Because `memory://...` and `:memory:` vector stores are process-local, separate API and
worker processes do not share in-memory vectors. In that convenience mode, `/search` and
`/answer` may succeed through SQL keyword fallback rather than a cross-process vector
round trip. Use the default Docker/Qdrant path when you need the true vector smoke.

### Manual demo path

Start infrastructure and run migrations:

```bash
test -f .env || cp .env.example .env
docker compose up -d postgres qdrant
alembic upgrade head
```

Seed demo data, extract the seeded run and source ids, and process exactly that run:

```bash
SEED_JSON="$(python3 scripts/seed_demo.py)"
printf '%s\n' "$SEED_JSON"
RUN_ID="$(printf '%s' "$SEED_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)["run_id"])')"
SOURCE_ID="$(printf '%s' "$SEED_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)["source_id"])')"
python3 scripts/run_worker_once.py --json --require-success --run-id "$RUN_ID"
```

Start the API:

```bash
uvicorn app.main:app --app-dir backend --reload
```

Query search and answer from another terminal:

```bash
curl -fsS -X POST http://localhost:8000/search \
  -H 'Content-Type: application/json' \
  -d "{\"query\":\"telemetry settings\",\"mode\":\"search\",\"filters\":{\"source_site_id\":\"$SOURCE_ID\"},\"top_k\":5}"

curl -fsS -X POST http://localhost:8000/answer \
  -H 'Content-Type: application/json' \
  -d "{\"query\":\"How is telemetry configured?\",\"mode\":\"answer\",\"filters\":{\"source_site_id\":\"$SOURCE_ID\"},\"top_k\":5}"
```

### Frontend

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

Migration policy for this unmerged prototype branch: the initial migration may be edited
in place. If you have already applied it to a local dev/test database, reset that
database before rerunning migrations.

### Vector backend configuration

`QDRANT_URL=memory://...` and `QDRANT_URL=:memory:` select an explicit in-memory
vector backend for local development and tests. They are process-local convenience modes,
not a cross-process vector smoke and not automatic fallbacks for real Qdrant outages. If
a real Qdrant URL is configured and Qdrant connection or HTTP calls fail, ingestion
records failed/partial chunk indexing status instead of silently switching to memory
storage. The smoke script accepts only memory vectors or local Qdrant hosts
(`localhost`, `127.0.0.1`, or `::1`) unless `ALLOW_NONLOCAL_SMOKE_VECTOR=1` is set.

If `python3 -m venv` reports that `ensurepip` is unavailable on Debian/Ubuntu,
install `python3.12-venv` or `python3-venv` and retry. Alternatively, `make install`
creates `.venv` and falls back to `uv venv --seed` when `uv` is available.

## Core invariants

- Raw pages are persisted before extraction agent calls.
- PostgreSQL is the source of truth.
- Qdrant stores chunk vectors and payloads for retrieval only.
- Frontend calls backend APIs only.
- Search and answer responses expose evidence objects with source and content links.
