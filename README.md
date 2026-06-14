# Intelligence RAG Prototype

A modular monolith prototype for observable open-source intelligence RAG.

## Local setup

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Demo paths

Use either the smoke path or the manual path. Do not manually seed/process a run and then
run the smoke script expecting it to verify that same run: the smoke script seeds and
processes its own targeted run.

### One-command smoke path

Start the API in one terminal:

```bash
uvicorn app.main:app --app-dir backend --reload
```

Run the smoke script in another terminal:

```bash
bash scripts/smoke_demo.sh
```

By default, the smoke script uses the Docker Compose PostgreSQL and Qdrant services. It
may create `.env` from `.env.example` when `.env` is missing, runs Alembic migrations,
seeds demo data, processes one targeted seeded run, and verifies `/runs`, `/search`, and
`/answer` against the API. If `ALLOW_NONLOCAL_SMOKE_DB=1` is set, the script also
exports `ALLOW_NONLOCAL_DEMO_SEED=1` so the Alembic and seed steps share one explicit DB
override.

If PostgreSQL is already available and you want to skip Docker Compose services, use the
local-memory-vector path and start the API with the same vector setting:

```bash
# Terminal 1
QDRANT_URL=memory://smoke-demo uvicorn app.main:app --app-dir backend --reload

# Terminal 2
SKIP_DOCKER=1 QDRANT_URL=memory://smoke-demo bash scripts/smoke_demo.sh
```

### Manual demo path

Start infrastructure and run migrations:

```bash
cp .env.example .env
docker compose up -d postgres qdrant
alembic upgrade head
```

Seed demo data, extract the seeded run id, and process exactly that run:

```bash
SEED_JSON="$(python3 scripts/seed_demo.py)"
printf '%s\n' "$SEED_JSON"
RUN_ID="$(printf '%s' "$SEED_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)["run_id"])')"
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
  -d '{"query":"telemetry settings","mode":"search","filters":{},"top_k":5}'

curl -fsS -X POST http://localhost:8000/answer \
  -H 'Content-Type: application/json' \
  -d '{"query":"How is telemetry configured?","mode":"answer","filters":{},"top_k":5}'
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
vector backend for local development and tests. They are not automatic fallbacks
for real Qdrant outages. If a real Qdrant URL is configured and Qdrant connection
or HTTP calls fail, ingestion records failed/partial chunk indexing status instead
of silently switching to memory storage.

If `python3 -m venv` reports that `ensurepip` is unavailable on Debian/Ubuntu,
install `python3.12-venv` or `python3-venv` and retry. Alternatively, `make install`
creates `.venv` and falls back to `uv venv --seed` when `uv` is available.

## Core invariants

- Raw pages are persisted before extraction agent calls.
- PostgreSQL is the source of truth.
- Qdrant stores chunk vectors and payloads for retrieval only.
- Frontend calls backend APIs only.
- Search and answer responses expose evidence objects with source and content links.
