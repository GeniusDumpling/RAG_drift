# Docker Deploy Design

## Context

The project is an observable open-source intelligence RAG prototype with a FastAPI backend, React/Vite frontend, ingestion worker scripts, PostgreSQL as the source of truth, and Qdrant as the vector index. The existing `docker-compose.yml` only starts PostgreSQL and Qdrant for local development. The goal is to package the project into a portable Docker image that can be deployed on another single server with minimal manual setup.

## Goals

- Build one application image containing the backend API, worker code, Alembic migrations, utility scripts, and built frontend assets.
- Provide a deployment Compose file that starts the application, PostgreSQL, and Qdrant together.
- Keep deployment configuration external to the image via environment variables.
- Preserve the current API paths such as `/health`, `/sources`, `/search`, and `/answer`.
- Serve the frontend from the same application container and same origin as the API.
- Provide scripts for local image build and tar export so the image can be copied to another server.
- Keep the deployment suitable for a single-server demo/prototype rather than high-availability production.

## Non-goals

- Do not introduce Nginx or a separate frontend runtime container.
- Do not add Kubernetes, Swarm, or multi-host orchestration.
- Do not add automatic always-on distributed worker scheduling.
- Do not bake `.env`, credentials, local virtualenvs, local caches, local databases, or node modules into the image.
- Do not require sentence-transformers model downloads by default; keep deterministic embeddings as the portable default.

## Recommended Approach

Use a single application image with Compose-managed dependencies.

The image will be built with a multi-stage `Dockerfile`:

1. A frontend build stage installs frontend dependencies and runs `npm run build`.
2. A Python runtime stage installs the Python package and copies backend, worker, scripts, Alembic files, and frontend `dist` output into the final image.
3. A container entrypoint waits for PostgreSQL and Qdrant, runs Alembic migrations, and starts Uvicorn.

`docker-compose.deploy.yml` will define:

- `app`: the packaged project image, exposing host port `8000` by default.
- `postgres`: `postgres:16-alpine` with a persistent volume.
- `qdrant`: `qdrant/qdrant:v1.12.4` with a persistent volume.

## Application Runtime

The `app` service will run Uvicorn on `0.0.0.0:8000`. It will use service-network URLs:

- `DATABASE_URL=postgresql+psycopg://intelligence:intelligence@postgres:5432/intelligence_rag`
- `SYNC_DATABASE_URL=postgresql+psycopg://intelligence:intelligence@postgres:5432/intelligence_rag`
- `QDRANT_URL=http://qdrant:6333`

The default deployment environment will use:

- `APP_ENV=prod`
- `EMBEDDING_PROVIDER=deterministic`
- `EMBEDDING_MODEL=deterministic-hash-v1`
- `LLM_PROVIDER=fake`

These defaults can be overridden in Compose or by an external env file.

## Frontend Static Serving

FastAPI will serve the built frontend assets from the application image. The frontend will continue using same-origin relative API requests because `VITE_API_BASE_URL` defaults to an empty string.

Static serving behavior:

- API routes remain at their current root paths.
- `/` serves `index.html` when frontend assets are present.
- Static assets are served from the frontend build output.
- Unknown browser navigation paths can fall back to `index.html` only when they are not known API paths. This keeps API 404 behavior explicit while supporting frontend navigation if needed.

## Worker Usage

This deployment will not start a continuous worker daemon by default. Operators can run worker commands on demand inside the image, for example:

```bash
docker compose -f docker-compose.deploy.yml run --rm app python scripts/run_worker_once.py --json --require-success
```

This keeps the v1 deployment simple and observable. A future design can add a dedicated worker service if periodic or long-running ingestion becomes necessary.

## Packaging Scripts

Add scripts:

- `scripts/docker_build.sh`: build the application image with configurable image name and tag.
- `scripts/docker_save.sh`: save the built image to a `.tar` file for transfer to another server.

The scripts should use safe shell settings and avoid reading or printing secret-bearing `.env` values.

## Deployment Documentation

Add `docs/docker-deploy.md` with commands for:

- building the image locally;
- exporting it as a tar archive;
- loading it on another server;
- starting the Compose stack;
- checking health and logs;
- seeding demo data if desired;
- running worker-once;
- stopping services and preserving/removing volumes.

## Error Handling

The entrypoint should fail fast if required dependencies never become reachable. It should:

- wait for PostgreSQL TCP connectivity before migration;
- wait for Qdrant HTTP health before API startup;
- run `alembic upgrade head` before starting Uvicorn;
- exit with a non-zero status on migration failure.

Compose healthchecks should help operators inspect whether `postgres`, `qdrant`, and `app` are ready.

## Testing and Verification

Implementation should be verified by:

- existing backend/worker tests where practical;
- frontend build;
- Docker image build;
- `docker compose -f docker-compose.deploy.yml config`;
- starting the stack and checking `GET /health` if Docker is available in the environment.

If the local environment cannot run Docker, validate syntax and scripts statically and document the limitation.

## Scope Boundaries

This design is intentionally scoped to portable single-server deployment. It does not try to solve production-grade secret management, TLS termination, autoscaling, background queue durability, or multi-replica worker coordination.
