# 情报 RAG 原型

一个用于可观测开源情报 RAG 的模块化单体原型：`backend`（FastAPI）提供检索问答 API，`worker` 处理异步任务，`frontend` 为 Web 界面，外挂 PostgreSQL（事实来源）+ Qdrant（向量检索）。

## 架构总览

```text
frontend (网页) ── HTTP ──> backend (FastAPI)
                              │
            ┌─────────────────┼──────────────────┐
            ▼                 ▼                  ▼
      PostgreSQL (事实)    Qdrant (向量)      LLM / Embedding(硅基流动等)
                              ▲
                              │
   forum-crawler / video-crawler / literature worker（采集与入库，cron 调度）
```

## 本地环境配置

推荐使用 [uv](https://github.com/astral-sh/uv) 管理环境；依赖版本由 `uv.lock` 精确保留。

```bash
test -f .env || cp .env.example .env
uv sync                                  # 基础依赖 + dev
# 采集/向量相关额外依赖（按需）：
uv sync --extra video-keyframes --extra local-embeddings
# 文献研究需 extra literature + playwright 浏览器
uv sync --extra literature && uv run playwright install chromium
```

> 不使用 uv 时，可用 `python3 -m venv .venv` + `pip install -e ".[dev]"`（Debian/Ubuntu 缺 ensurepip 需先装 `python3.12-venv`），或直接 `make install`。

常用开发命令见 `Makefile`：

```bash
make infra-up       # 启动 postgres + qdrant
make migrate        # 执行数据库迁移 alembic upgrade head
make api            # 启动后端 API（热重载）
make worker-once    # 跑一次异步 worker 处理待办 run
make frontend       # 启动前端开发服务器
make test / lint    # 单测 / 代码检查
make seed / smoke   # 播种演示数据 / 冒烟验证
```

> 默认 `docker-compose.yml` 将 PostgreSQL 映射到 `127.0.0.1:54329`（与 `.env` 中 `DATABASE_URL` 一致）；生产部署用 `docker-compose.deploy.yml`（含 app + postgres + qdrant 全套，端口绑定 localhost）。

## 数据采集（两种来源）

### 1. 论坛采集（`skills/forum-crawler`）

覆盖两类公开、免鉴权论坛：

- **Discourse 论坛**（`discuss.ardupilot.org`、`discuss.px4.io`）：关键词经 `/search.json` 检索命中帖子，取全文增量入库。
- **DJI 官方论坛**（`bbs.dji.com`）：按设备系列导入主题与评论。

```bash
# 搜索候选（不入库）
.venv/bin/python skills/forum-crawler/scripts/discourse_search.py \
  --query "GPS spoofing" --forums ardupilot px4 --max-results 10 --json
# 搜索 → 查重 → 落库
.venv/bin/python skills/forum-crawler/scripts/discourse_ingest.py \
  --query "GPS spoofing" --forums ardupilot px4 --max-results 10 --request-delay 0.3 --json
```

帖子按四层落库（SourceSite/CrawlRun 控制层 + RawPage + Author/ContentItem + ContentChunk），去重键为 `stable_hash("{source_id}:{canonical_url}:post:{content_hash}")`，重复帖自动跳过。

> 注意：Discourse 全文检索对冗长组合词命中很差，用短概念词（如 `GPS spoofing`、`MAVLink security`、`flyaway`）。

### 2. 视频采集（`skills/video-crawler`）

搜索无人机相关公开 YouTube 视频，以真实字幕 + 场景关键帧 + VLM 中文摘要入库。仅在公共接口范围内工作，不绕过登录/验证码。

```bash
# 单条视频 → 字幕 + 关键帧 + VLM 摘要 + 入库
uv run --extra video-keyframes --extra local-embeddings python3 \
  skills/video-crawler/scripts/youtube_transcript_evidence.py \
  --video-url "https://www.youtube.com/watch?v=VIDEO_ID" --language en --no-whisper-fallback --json
# 全链路：搜索 → 去重 → 逐个入库
uv run --extra video-keyframes --extra local-embeddings python3 \
  skills/video-crawler/scripts/search_and_ingest.py \
  --query "drone GPS spoofing" --caption-only --language en --video-limit 3 --json
```

- 搜索走 YouTube Data API v3，需要 `YOUTUBE_API_KEY`；`--caption-only` 只选有字幕的候选。
- 视频流下载需 `HTTPS_PROXY` 指向海外/香港出口，否则 googlevideo.com 返回 403。
- 摘要写入 `content_items.cleaned_text`，字幕合并为 `transcript_segment` 块，去重键 `video-evidence:<canonical_url>`。

### 定时调度（cron）

本机论坛与视频采集统一用用户级 cron 每 2 小时触发一次，`flock -n` 防止任务重叠，日志追加到 `/home/gaowei/*_collect.log`：

```cron
0 */2 * * *  cd /home/gaowei/projects/RAG_drift && flock -n /tmp/video_collect.lock bash skills/video-crawler/scripts/run_scheduled_collect.sh >> /home/gaowei/video_collect.log 2>&1
0 */2 * * *  cd /home/gaowei/projects/RAG_drift && flock -n /tmp/discourse_crawler.lock bash skills/forum-crawler/scripts/run_scheduled_discourse.sh >> /home/gaowei/discourse_collect.log 2>&1
```

## 文献研究（`scripts/run_literature_worker.py`）

按需跑 IEEE 文献调研：搜索 → 挑选 → 深度分析 → 生成报告，写入 `literature_*` 表。需要 DeepSeek 摘要（`DEEPSEEK_API_KEY`）与 `literature` extra。详见 [docs/literature-research.md](docs/literature-research.md)。

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

默认情况下，冒烟脚本会使用 Docker Compose 中的 PostgreSQL 和 Qdrant 服务。这个 Docker/Qdrant 模式是完整的跨进程向量冒烟路径：worker 将向量写入 Qdrant，API 再从同一个向量库读回。该脚本会产生本地副作用：当缺少 `.env` 时可能从 `.env.example` 创建 `.env`，除非显式跳过否则会启动 Docker 服务，执行 Alembic 迁移，播种演示数据，处理一个目标播种 run，将演示向量写入配置的本地向量后端，并通过 API 校验 `/runs`、`/search` 和 `/answer`。在默认 Docker/Qdrant 路径中，`/search` 检查会断言响应 trace 中存在真实向量检索。

默认的安全保护会拒绝非本地 API、数据库和 `QDRANT_URL` 配置。如果设置了 `ALLOW_NONLOCAL_SMOKE_DB=1`，脚本也会导出 `ALLOW_NONLOCAL_DEMO_SEED=1`，从而让 Alembic 和播种步骤共享同一个显式数据库覆盖配置。只有在你有意让冒烟脚本使用非本地 `QDRANT_URL` 时，才设置 `ALLOW_NONLOCAL_SMOKE_VECTOR=1`。

如果 PostgreSQL 已经可用，并且你想跳过 Docker Compose 服务，可以使用本地内存向量路径作为便捷模式，同时用相同的向量配置启动 API：

```bash
# 终端 1
QDRANT_URL=memory://smoke-demo uvicorn app.main:app --app-dir backend --reload
# 终端 2
SKIP_DOCKER=1 QDRANT_URL=memory://smoke-demo bash scripts/smoke_demo.sh
```

由于 `memory://...` 和 `:memory:` 向量存储是进程本地的，独立的 API 和 worker 进程不会共享内存向量。在这个便捷模式下，冒烟脚本会有意跳过跨进程向量 trace 断言，但保留 SQL/source/canonical 的 `/search` 和 `/answer` 检查。需要真实向量冒烟时，请使用默认 Docker/Qdrant 路径。

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
cd frontend && npm install && npm run dev -- --host 0.0.0.0
```

## 向量 / Embedding 配置

默认使用**硅基流动 BGE-M3（1024 维）**做中英多语言检索。相关变量在 `.env`：

| 变量 | 说明 |
|---|---|
| `EMBEDDING_PROVIDER` | 默认 `siliconflow`（远程 API）；本地可换 `sentence-transformers` |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION` | 默认 `BAAI/bge-m3` / `1024` |
| `EMBEDDING_API_KEY` | 可选；未设置时复用 `VLM_API_KEY` |
| `QDRANT_URL` | 默认 `http://localhost:6333`；`memory://` 为进程本地内存模式 |
| `QDRANT_COLLECTION` | 默认 `content_chunks_bge_m3_v1` |

- `QDRANT_URL=memory://...` 与 `:memory:` 是进程本地便捷模式，不是跨进程冒烟，也不是真实 Qdrant 故障时的自动回退；摄取失败会记录失败/部分 chunk 索引状态，而非静默切内存。
- **切换 embedding 模型后必须重建 Qdrant 集合**（维度/语义空间不同）：

```bash
python3 scripts/reindex_embeddings.py --reset-collection
```

## 核心不变量

- 在调用抽取 agent 之前，必须先持久化 raw pages。
- PostgreSQL 是事实来源（source of truth）。
- Qdrant 只保存用于检索的 chunk 向量和 payload。
- 前端只调用后端 API。
- 搜索和回答响应会暴露证据对象，其中包含 source 和 content 链接。

## 详细文档

- [部署指南](docs/deployment-guide.md) · [Docker 部署](docs/docker-deploy.md)
- [文献研究](docs/literature-research.md)
- 采集工作流见 `skills/forum-crawler/SKILL.md` 与 `skills/video-crawler/SKILL.md`