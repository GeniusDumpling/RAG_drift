# Intelligence RAG Prototype

A modular monolith prototype for observable open-source intelligence RAG.

## Local run

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
docker compose up -d postgres qdrant
alembic upgrade head
python scripts/seed_demo.py
uvicorn app.main:app --app-dir backend --reload
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

Open the frontend after Task 11:

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

## Core invariants

- Raw pages are persisted before extraction agent calls.
- PostgreSQL is the source of truth.
- Qdrant stores chunk vectors and payloads for retrieval only.
- Frontend calls backend APIs only.
- Search and answer responses expose evidence objects with source and content links.
