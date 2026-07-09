# Docker Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the Intelligence RAG prototype as one portable application Docker image and provide Compose/scripts/docs for single-server deployment.

**Architecture:** Build a single app image that contains the FastAPI API, worker code, migration files, utility scripts, and the Vite-built frontend. Compose runs that app image together with PostgreSQL and Qdrant, while the app entrypoint waits for dependencies, runs migrations, and starts Uvicorn. FastAPI serves the frontend static build on the same origin as the API.

**Tech Stack:** Python 3.12 slim, FastAPI/Uvicorn, Alembic, SQLAlchemy/psycopg, Node 20 for frontend build, React/Vite, PostgreSQL 16 Alpine, Qdrant v1.12.4, Docker Compose.

---

## File Structure

- Modify `backend/app/main.py`: introduce `create_app()`, keep existing API routes, and add optional frontend static serving when `frontend/dist/index.html` exists.
- Create `backend/tests/test_static_frontend.py`: verify root/static asset/frontend fallback behavior and verify API-like unknown paths do not return the frontend shell.
- Create `Dockerfile`: multi-stage image build with a Node frontend builder and Python runtime stage.
- Create `.dockerignore`: prevent local virtualenvs, node modules, caches, secrets, and build outputs from entering Docker context.
- Create `docker/entrypoint.sh`: wait for PostgreSQL and Qdrant, run migrations, then start Uvicorn; also allow arbitrary commands such as worker scripts.
- Create `docker-compose.deploy.yml`: single-server deployment stack for `app`, `postgres`, and `qdrant`.
- Create `scripts/docker_build.sh`: build the app image with configurable tag.
- Create `scripts/docker_save.sh`: export the app image as a tar file for copying to another server.
- Create `docs/docker-deploy.md`: operational guide for build, export, transfer, load, start, health check, worker-once, logs, and shutdown.

---

### Task 1: Add FastAPI frontend static serving

**Files:**
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_static_frontend.py`

- [ ] **Step 1: Write the failing static frontend tests**

Create `backend/tests/test_static_frontend.py` with this content:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def _write_frontend_dist(tmp_path: Path) -> Path:
    dist_dir = tmp_path / "dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text(
        '<!doctype html><html><body><div id="root">frontend shell</div></body></html>',
        encoding="utf-8",
    )
    (assets_dir / "app.js").write_text("console.log('frontend asset');", encoding="utf-8")
    return dist_dir


def test_create_app_serves_frontend_index_and_assets(tmp_path: Path) -> None:
    dist_dir = _write_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist_dir=dist_dir))

    root_response = client.get("/")
    assert root_response.status_code == 200
    assert "frontend shell" in root_response.text

    asset_response = client.get("/assets/app.js")
    assert asset_response.status_code == 200
    assert "frontend asset" in asset_response.text


def test_create_app_uses_index_fallback_for_frontend_routes(tmp_path: Path) -> None:
    dist_dir = _write_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist_dir=dist_dir))

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "frontend shell" in response.text


def test_create_app_does_not_fallback_for_unknown_api_paths(tmp_path: Path) -> None:
    dist_dir = _write_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist_dir=dist_dir))

    response = client.get("/search/not-a-real-api-route")

    assert response.status_code == 404
    assert "frontend shell" not in response.text


def test_create_app_skips_frontend_when_dist_is_missing(tmp_path: Path) -> None:
    missing_dist = tmp_path / "missing-dist"
    client = TestClient(create_app(frontend_dist_dir=missing_dist))

    response = client.get("/")

    assert response.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail before implementation**

Run:

```bash
cd /root/intelligence-rag
pytest backend/tests/test_static_frontend.py -q
```

Expected: FAIL with an import error or attribute error because `create_app` does not exist in `app.main` yet.

- [ ] **Step 3: Implement minimal static frontend serving**

Replace `backend/app/main.py` with this content:

```python
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"
API_PATH_PREFIXES = (
    "/health",
    "/sources",
    "/jobs",
    "/runs",
    "/contents",
    "/search",
    "/answer",
    "/search-queries",
    "/database",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _is_api_like_path(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in API_PATH_PREFIXES)


def _add_frontend_static_routes(app: FastAPI, frontend_dist_dir: Path) -> None:
    index_file = frontend_dist_dir / "index.html"
    if not index_file.is_file():
        return

    assets_dir = frontend_dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def serve_frontend_root() -> FileResponse:
        return FileResponse(index_file)

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend_fallback(full_path: str) -> FileResponse:
        request_path = f"/{full_path}"
        if _is_api_like_path(request_path):
            raise HTTPException(status_code=404, detail="Not Found")

        candidate_file = frontend_dist_dir / full_path
        if candidate_file.is_file():
            return FileResponse(candidate_file)
        return FileResponse(index_file)


def create_app(frontend_dist_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Intelligence RAG API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    _add_frontend_static_routes(app, frontend_dist_dir or DEFAULT_FRONTEND_DIST_DIR)
    return app


app = create_app()
```

- [ ] **Step 4: Run the focused static frontend tests**

Run:

```bash
cd /root/intelligence-rag
pytest backend/tests/test_static_frontend.py -q
```

Expected: PASS for all tests in `backend/tests/test_static_frontend.py`.

- [ ] **Step 5: Run existing health tests to verify API behavior remains unchanged**

Run:

```bash
cd /root/intelligence-rag
pytest backend/tests/test_health.py -q
```

Expected: PASS, including `GET /health` returning `{"status": "ok", "service": "intelligence-rag-api"}`.

- [ ] **Step 6: Commit Task 1**

Run:

```bash
cd /root/intelligence-rag
git add backend/app/main.py backend/tests/test_static_frontend.py
git commit -m "feat: serve frontend static build from api"
```

Expected: commit succeeds.

---

### Task 2: Add Docker image build files and container entrypoint

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `docker/entrypoint.sh`

- [ ] **Step 1: Create `.dockerignore`**

Create `.dockerignore` with this content:

```dockerignore
.git
.gitignore
.env
.env.*
!.env.example
.venv
.venv/
__pycache__/
*.py[cod]
*.pyo
*.pyd
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
node_modules/
frontend/node_modules/
frontend/dist/
dist/
build/
logs/
*.log
.api-*.pid
.worktrees/
.superpowers/
.DS_Store
```

- [ ] **Step 2: Create Docker entrypoint script**

Create directory `docker` if it does not exist, then create `docker/entrypoint.sh` with this content:

```bash
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
```

- [ ] **Step 3: Create multi-stage Dockerfile**

Create `Dockerfile` with this content:

```dockerfile
# syntax=docker/dockerfile:1

FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=prod \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    QDRANT_COLLECTION=content_chunks_v1 \
    EMBEDDING_PROVIDER=deterministic \
    EMBEDDING_MODEL=deterministic-hash-v1 \
    LLM_PROVIDER=fake \
    AGENT_TIMEOUT_SECONDS=20 \
    WORKER_POLL_INTERVAL_SECONDS=2

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml alembic.ini README.md ./
COPY backend ./backend
COPY worker ./worker
COPY scripts ./scripts
RUN python -m pip install --upgrade pip \
    && python -m pip install .

COPY --from=frontend-builder /frontend/dist ./frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/intelligence-rag-entrypoint
RUN chmod +x /usr/local/bin/intelligence-rag-entrypoint

EXPOSE 8000
ENTRYPOINT ["intelligence-rag-entrypoint"]
CMD ["api"]
```

- [ ] **Step 4: Validate shell and Dockerfile syntax locally**

Run:

```bash
cd /root/intelligence-rag
bash -n docker/entrypoint.sh
python3 - <<'PY'
from pathlib import Path
text = Path('Dockerfile').read_text(encoding='utf-8')
required = ['FROM node:20-alpine AS frontend-builder', 'FROM python:3.12-slim AS runtime', 'ENTRYPOINT ["intelligence-rag-entrypoint"]', 'CMD ["api"]']
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f'Missing Dockerfile fragments: {missing}')
PY
```

Expected: both commands exit with status 0.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
cd /root/intelligence-rag
git add Dockerfile .dockerignore docker/entrypoint.sh
git commit -m "build: add application docker image"
```

Expected: commit succeeds.

---

### Task 3: Add deployment Compose stack

**Files:**
- Create: `docker-compose.deploy.yml`

- [ ] **Step 1: Create deploy Compose file**

Create `docker-compose.deploy.yml` with this content:

```yaml
services:
  app:
    image: ${INTELLIGENCE_RAG_IMAGE:-intelligence-rag-app:latest}
    build:
      context: .
      dockerfile: Dockerfile
    container_name: intelligence-rag-app
    environment:
      APP_ENV: prod
      API_HOST: 0.0.0.0
      API_PORT: 8000
      DATABASE_URL: postgresql+psycopg://intelligence:intelligence@postgres:5432/intelligence_rag
      SYNC_DATABASE_URL: postgresql+psycopg://intelligence:intelligence@postgres:5432/intelligence_rag
      QDRANT_URL: http://qdrant:6333
      QDRANT_COLLECTION: content_chunks_v1
      EMBEDDING_PROVIDER: deterministic
      EMBEDDING_MODEL: deterministic-hash-v1
      LLM_PROVIDER: fake
      AGENT_TIMEOUT_SECONDS: 20
      WORKER_POLL_INTERVAL_SECONDS: 2
      WAIT_TIMEOUT_SECONDS: 90
    depends_on:
      postgres:
        condition: service_healthy
      qdrant:
        condition: service_started
    ports:
      - "${APP_PORT:-8000}:8000"
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()\"",
        ]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 20s
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    container_name: intelligence-rag-postgres-deploy
    environment:
      POSTGRES_USER: intelligence
      POSTGRES_PASSWORD: intelligence
      POSTGRES_DB: intelligence_rag
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U intelligence -d intelligence_rag"]
      interval: 5s
      timeout: 3s
      retries: 20
    restart: unless-stopped

  qdrant:
    image: qdrant/qdrant:v1.12.4
    container_name: intelligence-rag-qdrant-deploy
    volumes:
      - qdrant-data:/qdrant/storage
    restart: unless-stopped

volumes:
  postgres-data:
  qdrant-data:
```

- [ ] **Step 2: Validate Compose file**

Run:

```bash
cd /root/intelligence-rag
docker compose -f docker-compose.deploy.yml config >/tmp/intelligence-rag-compose-config.yml
```

Expected: command exits with status 0 and writes normalized config to `/tmp/intelligence-rag-compose-config.yml`.

If Docker is unavailable in the environment, run this fallback YAML parse check:

```bash
cd /root/intelligence-rag
python3 - <<'PY'
from pathlib import Path
text = Path('docker-compose.deploy.yml').read_text(encoding='utf-8')
required = ['services:', 'app:', 'postgres:', 'qdrant:', 'volumes:', 'postgres-data:', 'qdrant-data:']
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f'Missing compose fragments: {missing}')
PY
```

Expected fallback: command exits with status 0.

- [ ] **Step 3: Commit Task 3**

Run:

```bash
cd /root/intelligence-rag
git add docker-compose.deploy.yml
git commit -m "deploy: add docker compose stack"
```

Expected: commit succeeds.

---

### Task 4: Add Docker build and export scripts

**Files:**
- Create: `scripts/docker_build.sh`
- Create: `scripts/docker_save.sh`

- [ ] **Step 1: Create image build script**

Create `scripts/docker_build.sh` with this content:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_REF="${1:-${INTELLIGENCE_RAG_IMAGE:-intelligence-rag-app:latest}}"

printf 'Building Intelligence RAG image: %s\n' "$IMAGE_REF"
docker build -t "$IMAGE_REF" "$ROOT_DIR"
printf 'Built image: %s\n' "$IMAGE_REF"
```

- [ ] **Step 2: Create image export script**

Create `scripts/docker_save.sh` with this content:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_REF="${1:-${INTELLIGENCE_RAG_IMAGE:-intelligence-rag-app:latest}}"
SAFE_IMAGE_REF="${IMAGE_REF//\//_}"
SAFE_IMAGE_REF="${SAFE_IMAGE_REF//:/_}"
OUTPUT_PATH="${2:-$ROOT_DIR/dist/docker/${SAFE_IMAGE_REF}.tar}"

mkdir -p "$(dirname "$OUTPUT_PATH")"
printf 'Saving Docker image %s to %s\n' "$IMAGE_REF" "$OUTPUT_PATH"
docker save "$IMAGE_REF" -o "$OUTPUT_PATH"
printf 'Saved Docker image archive: %s\n' "$OUTPUT_PATH"
```

- [ ] **Step 3: Make scripts executable and validate shell syntax**

Run:

```bash
cd /root/intelligence-rag
chmod +x scripts/docker_build.sh scripts/docker_save.sh
bash -n scripts/docker_build.sh
bash -n scripts/docker_save.sh
```

Expected: all commands exit with status 0.

- [ ] **Step 4: Commit Task 4**

Run:

```bash
cd /root/intelligence-rag
git add scripts/docker_build.sh scripts/docker_save.sh
git commit -m "build: add docker packaging scripts"
```

Expected: commit succeeds.

---

### Task 5: Add Docker deployment documentation

**Files:**
- Create: `docs/docker-deploy.md`

- [ ] **Step 1: Write deployment guide**

Create `docs/docker-deploy.md` with this content:

````markdown
# Docker 部署指南

本文档说明如何把 Intelligence RAG 原型打包成一个应用镜像，并在另一台单机服务器上通过 Docker Compose 启动应用、PostgreSQL 和 Qdrant。

## 1. 本地构建镜像

在项目根目录执行：

```bash
./scripts/docker_build.sh intelligence-rag-app:latest
```

也可以通过环境变量指定默认镜像名：

```bash
INTELLIGENCE_RAG_IMAGE=registry.example.com/intelligence-rag-app:demo ./scripts/docker_build.sh
```

## 2. 导出镜像 tar

```bash
./scripts/docker_save.sh intelligence-rag-app:latest
```

默认输出路径类似：

```text
dist/docker/intelligence-rag-app_latest.tar
```

也可以显式指定输出文件：

```bash
./scripts/docker_save.sh intelligence-rag-app:latest /tmp/intelligence-rag-app_latest.tar
```

## 3. 拷贝到目标服务器

示例：

```bash
scp dist/docker/intelligence-rag-app_latest.tar user@target-server:/tmp/
scp docker-compose.deploy.yml user@target-server:/opt/intelligence-rag/
```

目标服务器需要已安装 Docker 和 Docker Compose 插件。

## 4. 在目标服务器加载镜像

```bash
cd /opt/intelligence-rag
docker load -i /tmp/intelligence-rag-app_latest.tar
```

如果镜像名不是 `intelligence-rag-app:latest`，启动时设置 `INTELLIGENCE_RAG_IMAGE`：

```bash
export INTELLIGENCE_RAG_IMAGE=registry.example.com/intelligence-rag-app:demo
```

## 5. 启动服务

```bash
docker compose -f docker-compose.deploy.yml up -d
```

默认端口：

- Web/API: `http://服务器IP:8000`
- API 健康检查: `http://服务器IP:8000/health`

如需改宿主机端口：

```bash
APP_PORT=18000 docker compose -f docker-compose.deploy.yml up -d
```

## 6. 查看状态和日志

```bash
docker compose -f docker-compose.deploy.yml ps
docker compose -f docker-compose.deploy.yml logs -f app
```

健康检查：

```bash
curl -fsS http://127.0.0.1:8000/health
```

预期输出：

```json
{"status":"ok","service":"intelligence-rag-api"}
```

## 7. 播种演示数据

如果需要在部署环境中播种演示数据：

```bash
docker compose -f docker-compose.deploy.yml run --rm app python scripts/seed_demo.py
```

该命令会连接 Compose 内部的 PostgreSQL。

## 8. 执行一次 worker

处理指定 run：

```bash
docker compose -f docker-compose.deploy.yml run --rm app \
  python scripts/run_worker_once.py --json --require-success --run-id <RUN_ID>
```

如果只想执行默认的一次 worker 轮询：

```bash
docker compose -f docker-compose.deploy.yml run --rm app python scripts/run_worker_once.py
```

## 9. 停止服务

保留数据库和 Qdrant 数据卷：

```bash
docker compose -f docker-compose.deploy.yml down
```

同时删除数据卷：

```bash
docker compose -f docker-compose.deploy.yml down -v
```

删除数据卷会清空 PostgreSQL 和 Qdrant 数据。

## 10. 配置说明

部署 Compose 默认使用：

```text
DATABASE_URL=postgresql+psycopg://intelligence:intelligence@postgres:5432/intelligence_rag
SYNC_DATABASE_URL=postgresql+psycopg://intelligence:intelligence@postgres:5432/intelligence_rag
QDRANT_URL=http://qdrant:6333
EMBEDDING_PROVIDER=deterministic
EMBEDDING_MODEL=deterministic-hash-v1
LLM_PROVIDER=fake
```

默认 embedding 是 deterministic，适合可移植演示。若启用真实本地 embedding，需要重新构建包含 `local-embeddings` 依赖的镜像，并重建 Qdrant collection。

## 11. 注意事项

- `.env` 不会被打进镜像。
- 当前部署是单机演示形态，不包含 TLS、Nginx、多副本和高可用。
- PostgreSQL 是事实来源；Qdrant 是向量索引。
- 前端和 API 同源部署，前端默认使用相对路径请求 API。
````

- [ ] **Step 2: Validate Markdown has required operational commands**

Run:

```bash
cd /root/intelligence-rag
python3 - <<'PY'
from pathlib import Path
text = Path('docs/docker-deploy.md').read_text(encoding='utf-8')
required = [
    './scripts/docker_build.sh',
    './scripts/docker_save.sh',
    'docker load -i',
    'docker compose -f docker-compose.deploy.yml up -d',
    'curl -fsS http://127.0.0.1:8000/health',
    'python scripts/run_worker_once.py',
]
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f'Missing documentation fragments: {missing}')
PY
```

Expected: command exits with status 0.

- [ ] **Step 3: Commit Task 5**

Run:

```bash
cd /root/intelligence-rag
git add docs/docker-deploy.md
git commit -m "docs: add docker deployment guide"
```

Expected: commit succeeds.

---

### Task 6: Verify packaging end-to-end

**Files:**
- Read/verify: all files changed by Tasks 1-5

- [ ] **Step 1: Run frontend build**

Run:

```bash
cd /root/intelligence-rag/frontend
npm run build
```

Expected: `tsc -b && vite build` completes successfully and writes `frontend/dist`.

- [ ] **Step 2: Run focused backend tests**

Run:

```bash
cd /root/intelligence-rag
pytest backend/tests/test_static_frontend.py backend/tests/test_health.py -q
```

Expected: all tests pass. If local PostgreSQL test infrastructure is unavailable, record the exact failure and continue only with static validations; do not claim backend tests passed.

- [ ] **Step 3: Validate shell scripts**

Run:

```bash
cd /root/intelligence-rag
bash -n docker/entrypoint.sh
bash -n scripts/docker_build.sh
bash -n scripts/docker_save.sh
```

Expected: all syntax checks pass.

- [ ] **Step 4: Validate Compose config**

Run:

```bash
cd /root/intelligence-rag
docker compose -f docker-compose.deploy.yml config >/tmp/intelligence-rag-compose-config.yml
```

Expected: command exits with status 0. If Docker is unavailable, run the fallback fragment check from Task 3 Step 2 and record that Docker runtime validation was skipped.

- [ ] **Step 5: Build Docker image if Docker is available**

Run:

```bash
cd /root/intelligence-rag
./scripts/docker_build.sh intelligence-rag-app:local
```

Expected: Docker image build completes successfully.

If Docker is unavailable, record that image build could not be executed in this environment.

- [ ] **Step 6: Optionally start the deployment stack if Docker is available**

Run:

```bash
cd /root/intelligence-rag
INTELLIGENCE_RAG_IMAGE=intelligence-rag-app:local APP_PORT=18000 docker compose -f docker-compose.deploy.yml up -d
sleep 10
curl -fsS http://127.0.0.1:18000/health
docker compose -f docker-compose.deploy.yml down
```

Expected: health endpoint returns `{"status":"ok","service":"intelligence-rag-api"}` and the stack shuts down cleanly. If Docker is unavailable, skip this step and state that runtime Compose validation was not performed.

- [ ] **Step 7: Review git status**

Run:

```bash
cd /root/intelligence-rag
git status --short
```

Expected: no unintended files are staged or modified. `frontend/dist` and `dist/docker` should remain ignored.

- [ ] **Step 8: Final commit if verification changed tracked files**

If verification required tracked-file adjustments, commit them:

```bash
cd /root/intelligence-rag
git add <changed-tracked-files>
git commit -m "chore: finalize docker deployment packaging"
```

Expected: commit succeeds only when there are tracked changes to commit.
```
