# Intelligence RAG Prototype

A modular monolith prototype for observable open-source intelligence RAG.

## Local run

```bash
cp .env.example .env
python3 -m pip install -e ".[dev]"
docker compose up -d postgres qdrant
alembic upgrade head
python3 scripts/seed_demo.py
uvicorn app.main:app --app-dir backend --reload
```

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
