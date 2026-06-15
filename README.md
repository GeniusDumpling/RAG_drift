# 情报 RAG 原型

一个用于可观测开源情报 RAG 的模块化单体原型。

## 本地环境配置

```bash
test -f .env || cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## 演示路径

可以选择冒烟脚本路径或手动路径。不要先手动播种/处理某个 run，随后又运行冒烟脚本并期望它校验同一个 run：冒烟脚本会自行播种并处理它自己的目标 run。

### 冒烟脚本路径（API 已运行）

在一个终端中启动 API：

```bash
uvicorn app.main:app --app-dir backend --reload
```

在另一个终端中运行冒烟脚本：

```bash
bash scripts/smoke_demo.sh
```

默认情况下，冒烟脚本会使用 Docker Compose 中的 PostgreSQL 和 Qdrant 服务。这个 Docker/Qdrant 模式是完整的跨进程向量冒烟路径：worker 将向量写入 Qdrant，API 再从同一个向量库读回。该脚本会产生本地副作用：当缺少 `.env` 时可能从 `.env.example` 创建 `.env`，除非显式跳过否则会启动 Docker 服务，执行 Alembic 迁移，播种演示数据，处理一个目标播种 run，将演示向量写入配置的本地向量后端，并通过 API 校验 `/runs`、`/search` 和 `/answer`。在默认 Docker/Qdrant 路径中，`/search` 检查现在会断言响应 trace 中存在真实向量检索：已尝试向量检索、未失败、返回了命中，并且至少产生一个 `vector` 或 `hybrid` 证据匹配。

默认的安全保护会拒绝非本地 API、数据库和 `QDRANT_URL` 配置。如果设置了 `ALLOW_NONLOCAL_SMOKE_DB=1`，脚本也会导出 `ALLOW_NONLOCAL_DEMO_SEED=1`，从而让 Alembic 和播种步骤共享同一个显式数据库覆盖配置。只有在你有意让冒烟脚本使用非本地 `QDRANT_URL` 时，才设置 `ALLOW_NONLOCAL_SMOKE_VECTOR=1`；保护逻辑的错误信息会打印已脱敏、无凭据的 URL。

如果 PostgreSQL 已经可用，并且你想跳过 Docker Compose 服务，可以使用本地内存向量路径作为便捷模式，同时用相同的向量配置启动 API：

```bash
# 终端 1
QDRANT_URL=memory://smoke-demo uvicorn app.main:app --app-dir backend --reload

# 终端 2
SKIP_DOCKER=1 QDRANT_URL=memory://smoke-demo bash scripts/smoke_demo.sh
```

由于 `memory://...` 和 `:memory:` 向量存储是进程本地的，独立的 API 和 worker 进程不会共享内存向量。在这个便捷模式下，冒烟脚本会有意跳过跨进程向量 trace 断言，但仍保留 SQL/source/canonical 的 `/search` 和 `/answer` 检查；这些请求可能通过 SQL 关键词回退成功，而不是通过一次向量往返成功。需要真实向量冒烟时，请使用默认 Docker/Qdrant 路径。

### 手动演示路径

启动基础设施并运行迁移：

```bash
test -f .env || cp .env.example .env
docker compose up -d postgres qdrant
alembic upgrade head
```

播种演示数据，提取播种得到的 run id 和 source id，并且只处理该 run：

```bash
SEED_JSON="$(python3 scripts/seed_demo.py)"
printf '%s\n' "$SEED_JSON"
RUN_ID="$(printf '%s' "$SEED_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)["run_id"])')"
SOURCE_ID="$(printf '%s' "$SEED_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)["source_id"])')"
python3 scripts/run_worker_once.py --json --require-success --run-id "$RUN_ID"
```

启动 API：

```bash
uvicorn app.main:app --app-dir backend --reload
```

在另一个终端中请求搜索和回答接口：

```bash
curl -fsS -X POST http://localhost:8000/search \
  -H 'Content-Type: application/json' \
  -d "{\"query\":\"telemetry settings\",\"mode\":\"search\",\"filters\":{\"source_site_id\":\"$SOURCE_ID\"},\"top_k\":5}"

curl -fsS -X POST http://localhost:8000/answer \
  -H 'Content-Type: application/json' \
  -d "{\"query\":\"How is telemetry configured?\",\"mode\":\"answer\",\"filters\":{\"source_site_id\":\"$SOURCE_ID\"},\"top_k\":5}"
```

### 前端

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

该未合并原型分支的迁移策略：初始迁移可以就地编辑。如果你已经将它应用到了本地开发/测试数据库，请在重新运行迁移前重置该数据库。

### 向量后端配置

`QDRANT_URL=memory://...` 和 `QDRANT_URL=:memory:` 会选择显式的内存向量后端，用于本地开发和测试。它们是进程本地的便捷模式，不是跨进程向量冒烟，也不是实际 Qdrant 故障时的自动回退。如果配置了真实 Qdrant URL，而 Qdrant 连接或 HTTP 调用失败，摄取流程会记录失败/部分 chunk 索引状态，而不是静默切换到内存存储。除非设置 `ALLOW_NONLOCAL_SMOKE_VECTOR=1`，否则冒烟脚本只接受内存向量或本地 Qdrant 主机（`localhost`、`127.0.0.1` 或 `::1`）。

如果在 Debian/Ubuntu 上运行 `python3 -m venv` 时提示 `ensurepip` 不可用，请安装 `python3.12-venv` 或 `python3-venv` 后重试。也可以使用 `make install` 创建 `.venv`；当 `uv` 可用时，它会回退到 `uv venv --seed`。

## 核心不变量

- 在调用抽取 agent 之前，必须先持久化 raw pages。
- PostgreSQL 是事实来源（source of truth）。
- Qdrant 只保存用于检索的 chunk 向量和 payload。
- 前端只调用后端 API。
- 搜索和回答响应会暴露证据对象，其中包含 source 和 content 链接。
