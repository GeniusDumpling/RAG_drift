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

### 本地语义 Embedding 配置

默认配置使用 `EMBEDDING_PROVIDER=deterministic`，这是可复现的 hash 向量，只用于测试和演示链路。若要启用真实语义向量检索，可以安装本地 embedding 依赖并使用 BGE small zh：

```bash
source .venv/bin/activate
python -m pip install -e ".[dev,local-embeddings]"
```

然后设置：

```bash
export EMBEDDING_PROVIDER=sentence-transformers
export EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
export QDRANT_COLLECTION=content_chunks_bge_small_zh_v1
```

第一次运行会下载模型文件。切换 embedding 模型后必须重建 Qdrant 向量索引，因为旧 collection 中的向量维度和语义空间不同：

```bash
python3 scripts/reindex_embeddings.py --reset-collection
```

回滚到 deterministic demo 模式：

```bash
export EMBEDDING_PROVIDER=deterministic
export EMBEDDING_MODEL=deterministic-hash-v1
export QDRANT_COLLECTION=content_chunks_v1
```

如果在 Debian/Ubuntu 上运行 `python3 -m venv` 时提示 `ensurepip` 不可用，请安装 `python3.12-venv` 或 `python3-venv` 后重试。也可以使用 `make install` 创建 `.venv`；当 `uv` 可用时，它会回退到 `uv venv --seed`。

## FlyForum 视频 RAG Demo（2026-06-24）

一个最小可演示的视频 RAG 链路：从 FlyForum 公网页面发现直链视频 URL，通过硅基流动 VLM 生成中文描述，存入 PostgreSQL，用 BGE-M3 embedding 写入 Qdrant 1024 维 collection，并在现有 Search 页面展示视频播放器和完整描述。

**只支持：**

- `https://www.flyforum.cn/forum.php` 及同域公开论坛页/帖子页
- HTML 中直接出现的 `<video src>`、`<source src>`，以及 `.mp4`、`.webm`、`.m3u8` 结尾的链接
- 默认最多 3 个页面、1 个视频，请求间隔至少 1 秒

**明确不做：** 登录、验证码、JS 播放器逆向、隐藏流解析、FFmpeg、全站爬取、定时调度、新页面。

### 前置条件

```bash
# 设置环境变量（替换 <set-locally> 为真实 API key）
export SILICONFLOW_API_KEY='<set-locally>'
export EMBEDDING_PROVIDER=siliconflow
export QDRANT_COLLECTION=flyforum_video_bge_m3
```

确保 PostgreSQL 和 Qdrant 已运行：

```bash
docker compose up -d postgres qdrant
alembic upgrade head
```

### 手动创建 Qdrant 1024 维 collection

视频 demo 使用 1024 维 BGE-M3 embedding，需要在 Qdrant 中创建独立 collection：

```bash
curl -s -X PUT 'http://localhost:6333/collections/flyforum_video_bge_m3' \
  -H 'Content-Type: application/json' \
  -d '{"vectors": {"size": 1024, "distance": "Cosine"}}'
```

### 运行导入脚本

```bash
python scripts/ingest_flyforum_video_demo.py --page-limit 3 --video-limit 1 --json
```

输出示例：

```json
{
  "status": "success",
  "run_id": "f503f878-ede6-4b50-baa9-24e28acf059c",
  "pages_discovered": 2,
  "pages_fetched": 2,
  "pages_parsed": 2,
  "video_discovered": 0,
  "video_analyzed": 0,
  "video_failed": 0,
  "chunked_count": 0,
  "embedded_count": 0,
  "deduped_count": 0,
  "error_count": 0
}
```

> **注意：** 公开页面没有直接视频 URL 时，脚本可能成功结束但 `video_discovered_count=0`。这表示当前采样页面没有满足 demo 规则的直链视频链接，不代表 PostgreSQL/Qdrant 故障。不要为了"找出视频"绕过登录、验证码或站点限制。

### 直接视频 URL 导入方法

已知视频 URL 时，可以跳过页面发现步骤，直接测试 VLM + PostgreSQL + Qdrant 完整链路：

```bash
# 1. 启动 API（使用 1024 维 collection）
QDRANT_COLLECTION=flyforum_video_bge_m3 \
EMBEDDING_PROVIDER=siliconflow \
uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000

# 2. 在另一个终端，验证搜索能够命中视频：
curl -s -X POST http://localhost:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"无人机","mode":"search","top_k":5,"filters":{}}' | python3 -m json.tool
```

### 核对 PostgreSQL 与 Qdrant 一致性

```bash
psql -h 127.0.0.1 -p 54329 -U intelligence -d intelligence_rag -c "
  select
    ci.id as content_item_id,
    ci.canonical_url as video_url,
    cc.id as chunk_id,
    cc.vector_point_id,
    cc.embed_status
  from content_items ci
  join content_chunks cc on cc.content_item_id = ci.id
  where ci.item_type = 'video_description'
  order by ci.created_at desc, cc.chunk_index;
"
```

期望：每一行 `chunk_id::text = vector_point_id` 且 `embed_status=success`。

### 前端验证

启动前端后，在导航栏点击 **检索问答 Search**，输入查询（如"无人机失控"），在 Item Type 下拉选择 `video_description`，点击 **检索 Search**。结果中的视频证据会显示 `<video>` 播放器和可折叠的完整中文描述。

### 环境变量参考

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SILICONFLOW_API_KEY` | — | 硅基流动 API key（必填） |
| `SILICONFLOW_BASE_URL` | `https://api.siliconflow.cn/v1` | API 地址 |
| `EMBEDDING_PROVIDER` | `fake` | `siliconflow` 时启用远程 embedding |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | 向量模型 |
| `EMBEDDING_DIMENSION` | `1024` | 向量维度 |
| `VLM_MODEL` | `Qwen/Qwen3-Omni-30B-A3B-Instruct` | 视频描述模型 |
| `QDRANT_COLLECTION` | `content_chunks_v1` | 视频 demo 需设为 `flyforum_video_bge_m3` |

## 核心不变量

- 在调用抽取 agent 之前，必须先持久化 raw pages。
- PostgreSQL 是事实来源（source of truth）。
- Qdrant 只保存用于检索的 chunk 向量和 payload。
- 前端只调用后端 API。
- 搜索和回答响应会暴露证据对象，其中包含 source 和 content 链接。
