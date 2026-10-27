# 情报 RAG 原型

面向无人机技术、故障与安全、产业供应链和学术研究，提供公开信息采集、原始证据保存、内容加工、检索问答和视频知识图谱浏览。

本文依据 2026 年 10 月 21 日的代码与验收文档更新。文中的历史验收结果不代表当前服务状态；实时数据量与任务结果以实际运行环境为准。

系统整体采集与大模型分析流程、各子项进展：见 [系统总述与进展](docs/system-overview-and-progress.md)。

最近一周进展：见 [项目进展周报（2026-10-16—2026-10-22）](docs/weekly-progress-2026-10-22.md)。

部署到另一台 Linux 服务器：见 [Docker + systemd 部署指南](docs/linux-server-deployment.md)。
包含目标机代理/端口配置、五类定时任务、持久化、镜像发布及验证步骤；采集任务通过容器运行，无需宿主机 Python 环境。已有部署的升级先按指南核对数据卷和备份。

一个用于可观测开源情报 RAG 的模块化单体原型：`backend`（FastAPI）提供检索问答 API，`worker` 处理异步任务，`frontend` 为 Web 界面，外挂 PostgreSQL（源内容）+ Qdrant（向量检索）+ Neo4j（视频图谱与构图任务）。

## 架构总览

```text
frontend (网页) ── HTTP ──> backend (FastAPI)
                              ├── PostgreSQL（源内容、采集任务）
                              ├── Qdrant（内容向量）
                              ├── Neo4j（视频图谱、构图任务和检查点）
                              └── LLM / Embedding API

forum-crawler / video-crawler / ifixit_rag ──> PostgreSQL / Qdrant
supplier-information / literature worker ──> PostgreSQL（专题业务表）
build_graph.py ── 读取 PostgreSQL 源材料 ──> 模型抽取 ──> Neo4j
```

论坛、视频和 iFixit 维修指南接入通用内容检索；供应链和文献研究保存于专用表，尚未统一接入 `/search`。视频图谱提供独立检索接口，`/answer` 尚未自动融合图谱结果。

## 功能与目录

前端包括总览、数据源、运行记录、内容详情、数据库、检索、AI 问答、供应链、文献研究和视频知识图谱十类视图。数据库浏览需要启用 `DATABASE_BROWSER_ENABLED`；采集任务控制需部署调度桥接。

| 目录 | 职责 |
| --- | --- |
| `backend/app/api` | FastAPI 接口与依赖注入 |
| `backend/app/models`、`repositories`、`schemas` | 数据模型、持久化访问与接口契约 |
| `backend/app/services` | 混合检索、问答、向量索引与视频图谱 |
| `backend/app/literature` | IEEE 文献研究流程 |
| `backend/alembic` | 数据库迁移；当前 head 为 `0007_chat_sessions` |
| `worker/app` | 通用采集任务领取、内容规范化与切片索引 |
| `frontend` | React + TypeScript + Vite 控制台 |
| `tools` | 论坛、视频、供应链、视频图谱及 iFixit 工具 |
| `scripts` | worker、演示、重建索引与调度桥接入口 |
| `deploy`、`docker` | 任务 runner、systemd 模板及容器入口 |
| `docs` | 设计、部署、运行说明与验收记录 |

## 本地环境配置

需要 Python 3.11+、Node.js/npm，以及 Docker Compose（用于本地数据库）。推荐使用 [uv](https://github.com/astral-sh/uv) 管理环境；依赖版本由 `uv.lock` 保留。以下命令在仓库根目录执行。

```bash
test -f .env || cp .env.example .env
uv sync --extra dev                      # 基础依赖 + 开发工具
# 采集/向量相关额外依赖（按需）：
uv sync --extra dev --extra video-keyframes --extra supplier
# 本地 embedding 另加 --extra local-embeddings；本地语音转录另加 --extra video-asr
# 文献研究需 extra literature + playwright 浏览器
uv sync --extra dev --extra literature
.venv/bin/python -m playwright install chromium
```

每次 `uv sync` 应同时列出仍需保留的 extras。视频处理还需系统可用的 `ffmpeg`；`supplier` extra 提供采集脚本使用的 PyYAML、python-dotenv 和浏览器相关依赖。

> 不使用 uv 时，可用 `python3 -m venv .venv` + `pip install -e ".[dev]"`（Debian/Ubuntu 缺 ensurepip 需先装 `python3.12-venv`），或直接 `make install`。

先编辑 `.env`：

- **本地数据库端口**：当前 `docker-compose.yml` 映射为 `55432:5432`，而 `.env.example` 和后端默认值仍使用 `54329`。使用此 Compose 时，将 `DATABASE_URL`、`SYNC_DATABASE_URL` 的主机端口都改为 `55432`。开发 Compose 的 PostgreSQL、Qdrant 映射未显式限制为 localhost。
- **向量服务**：默认使用 SiliconFlow BGE-M3，填写 `EMBEDDING_API_KEY`，或提供可复用的 `VLM_API_KEY`。
- **答案生成**：填写 `DEEPSEEK_API_KEY`；缺少密钥时问答返回证据摘录。
- **图谱连接**：默认 `VIDEO_GRAPH_BACKEND=postgres`。宿主机使用 Neo4j 时，`NEO4J_URI` 应为 `bolt://localhost:7687`；模板中的 `bolt://neo4j:7687` 用于 Compose 容器网络。

启动本地服务：

```bash
docker compose up -d postgres qdrant
.venv/bin/alembic upgrade head
# 终端 1：后端，默认 http://localhost:8000；接口文档 /docs
make api
```

```bash
# 终端 2：前端，默认 http://localhost:5173
npm --prefix frontend ci
VITE_API_BASE_URL=http://localhost:8000 npm --prefix frontend run dev -- --host 127.0.0.1
```

生产构建使用 `frontend/.env.production` 的同源 API 地址，由 FastAPI 提供构建后的页面及接口。

常用开发命令见 `Makefile`：

```bash
make infra-up       # 启动 postgres + qdrant
make migrate        # 执行数据库迁移 alembic upgrade head
make api            # 启动后端 API（热重载）
make worker-once    # 跑一次异步 worker 处理待办 run
make frontend       # 启动前端开发服务器
make test           # 默认 Python 测试集合；需独立测试数据库
make lint           # Ruff + mypy
make seed           # 播种演示数据
make smoke          # 冒烟验证；包含播种和处理目标 run
```

生产部署用 `docker-compose.deploy.yml`（app + PostgreSQL + Qdrant），应用默认绑定 `127.0.0.1:8000`，数据库不映射宿主机端口。Neo4j 使用附加配置 `docker-compose.neo4j.yml`；部署和迁移步骤见文末文档。

## 数据采集与专题分析

### 1. 论坛采集（`tools/forum-crawler`）

覆盖两类公开、免鉴权论坛：

- **Discourse 论坛**（`discuss.ardupilot.org`、`discuss.px4.io`）：从公开帖子流发现主帖和新回复，按主题补齐公开正文；支持独立历史回填游标和旧主题复核。
- **DJI 官方论坛**（`bbs.dji.com`）：按设备系列导入主题与评论。

```bash
# 日常增量归档：保存原始响应与完整主题快照
.venv/bin/python tools/forum-crawler/scripts/discourse_ingest.py --json
# 独立加工：分类过滤、主帖/回复关联与切片，不访问论坛或向量模型
.venv/bin/python tools/forum-crawler/scripts/discourse_process.py --json
# 加工并向量化，重试此前失败的索引任务
.venv/bin/python tools/forum-crawler/scripts/discourse_process.py --embed --json
# 历史分批回填，反复运行从持久化游标继续
.venv/bin/python tools/forum-crawler/scripts/discourse_ingest.py \
  --mode backfill --max-pages 10 --max-topics 20 --json
# 搜索预览（不入库，不作为常态增量入口）
.venv/bin/python tools/forum-crawler/scripts/discourse_search.py \
  --query "GPS spoofing" --forums ardupilot px4 --max-results 10 --json
```

原始快照先提交，再独立生成内容与切片。帖子按规范化论坛地址与 `post_id` 保持稳定身份，正文或上下文变化时更新切片；过期切片退出检索。发现游标推进表示主题已进入可靠队列，不表示正文与索引已经处理完成。

首次增量只从最新一页开始，完整历史需另行回填。同步状态和主题任务辅助表由脚本创建，业务表仍需先执行 Alembic 迁移。命令、过滤策略、恢复与调度说明见 [论坛采集 README](tools/forum-crawler/scripts/README.md)。

### 2. 视频采集（`tools/video-crawler`）

搜索无人机相关公开 YouTube 视频，以真实字幕 + 场景关键帧 + VLM 中文摘要入库。仅在公共接口范围内工作，不绕过登录/验证码。

```bash
# 单条视频 → 字幕 + 关键帧 + VLM 摘要 + 入库
.venv/bin/python \
  tools/video-crawler/scripts/youtube_transcript_evidence.py \
  --video-url "https://www.youtube.com/watch?v=VIDEO_ID" --language en --no-whisper-fallback --json
# 全链路：搜索 → 去重 → 逐个入库
.venv/bin/python \
  tools/video-crawler/scripts/main.py \
  --query "drone GPS spoofing" --caption-only --language en --video-limit 1 --json
```

- 搜索走 YouTube Data API v3，需要 `YOUTUBE_API_KEY`；定时模式会持久化轮换关键词、跨页跳过已入库候选，直至达到成功入库目标或耗尽页面。
- 视频流下载依赖可达的网络出口；受限网络需配置 `HTTPS_PROXY`，否则可能返回 403。
- 摘要写入 `content_items.cleaned_text`，字幕合并为 `transcript_segment` 块，去重键 `video-evidence:<canonical_url>`。

### 3. iFixit 维修指南（`tools/ifixit_rag`）

将本地 Markdown/JSON 指南解析为保留步骤顺序、安全提示、前置关系与媒体引用的数据包，再写入已有内容表和向量集合。额外依赖见 `tools/ifixit_rag/requirements.txt`；正式索引需与嵌入模型一致的本地 tokenizer。

```bash
# 输入为本地指南目录；输出目录必须尚不存在
.venv/bin/python -m tools.ifixit_rag prepare \
  --input data/ifixit数据 --output data/processed/ifixit/run-001 \
  --tokenizer /path/to/local/bge-m3-tokenizer
.venv/bin/python -m tools.ifixit_rag validate --bundle data/processed/ifixit/run-001
.venv/bin/python -m tools.ifixit_rag import \
  --bundle data/processed/ifixit/run-001 --dry-run
# 确认数据包后导入并索引
.venv/bin/python -m tools.ifixit_rag import \
  --bundle data/processed/ifixit/run-001 --index \
  --tokenizer /path/to/local/bge-m3-tokenizer
```

内容类型为 `repair_guide`，可通过现有 `/search` 检索。2026-10-21 验收记录为 391 篇指南、2,028 个步骤和 3,502 个成功索引切片；该次历史验收发现型号关键词压过部件语义的问题；现已按完整型号和查询覆盖度[调整排序并验证](docs/retrieval-ranking-fix-2026-10-21.md)，仍需固定问题集评估。图片映射尚未完整核验，未进行图片向量化。使用与边界见 [工具说明](tools/ifixit_rag/README.md) 和 [正式导入结果](docs/ifixit-production-import-2026-10-21.md)。

### 4. 供应链分析（`tools/supplier-information`）

百度定向搜索与网页正文采集后，调用模型抽取供应关系，再由独立流程复核证据并保存 `supplier_relations`、`supplier_verifications`。需 `supplier` extra、`BAIDU_QIANFAN_API_KEY` 和 `DEEPSEEK_API_KEY`，任务入口为 `pipeline.py` 和 `supplier_verify.py`；部署环境统一通过下文任务 runner 执行。

### 5. 文献研究（`scripts/run_literature_worker.py`）

按需跑 IEEE 文献调研：搜索 → 挑选 → 深度分析 → 生成报告，写入 `literature_*` 表。需要 DeepSeek 摘要（`DEEPSEEK_API_KEY`）与 `literature` extra。详见 [docs/literature-research.md](docs/literature-research.md)。

```bash
.venv/bin/python scripts/run_literature_worker.py
```

同一个浏览器 profile 仅由一个文献 worker 使用。首次需以 `LITERATURE_HEADLESS=false` 在浏览器完成授权登录。

## 定时调度与运行控制

部署提供五类 systemd 定时任务，通过 `deploy/run-job.sh` 启动 jobs 容器。下表描述仓库模板；目标机安装启用后才会实际执行。

| 任务 | runner 参数 | 模板执行时间 |
| --- | --- | --- |
| 视频采集 | `video-collect` | 偶数小时 :00，服务器本地时区 |
| 论坛增量 | `discourse-collect` | 偶数小时 :30，北京时间 |
| 论坛历史回填 | `discourse-backfill` | 每小时 :05，北京时间 |
| 供应关系抽取 | `supplier-pipeline` | 偶数小时 :15，服务器本地时区 |
| 供应关系验证 | `supplier-verify` | 奇数小时 :45，服务器本地时区 |

```bash
# 部署环境依赖检查，不采集
bash deploy/run-job.sh video-collect --validate
# 手动执行一次论坛增量归档与加工
bash deploy/run-job.sh discourse-collect
```

论坛增量和历史回填共用互斥锁，默认 `FORUM_EMBED=0`；要在调度任务中写入向量，设置 `FORUM_EMBED=1`。历史游标、任务积压、加工结果和向量状态需分别观察，进程退出成功不等于有新增内容。

前端任务控制依赖宿主机受限调度桥接，配置见 [定时任务控制](docs/scheduled-task-control.md)。安装步骤见 [Linux 部署指南](docs/linux-server-deployment.md)，论坛专用用户级 timer 见 [论坛采集说明](tools/forum-crawler/scripts/README.md)；同一采集任务应只启用一套调度。

## 检索与 AI 问答

检索结合 PostgreSQL 关键词匹配和 Qdrant 语义向量，保留完整型号短语，按查询覆盖度和向量相关性排序，并在截取结果前分散来源。返回来源、内容链接及字幕时间等证据。问答先扩展召回候选，再从数据库读取完整命中切片，并在预算内补充相邻切片；最多使用 8 条证据、12,000 字符上下文。

| 接口 | 用途 |
| --- | --- |
| `POST /search` | 混合检索 |
| `POST /answer` | JSON 问答，保留单轮兼容能力 |
| `POST /answer/stream` | SSE 流式问答，支持停止生成 |
| `POST /chat-sessions` | 创建会话 |
| `GET /chat-sessions` | 分页查询历史会话 |
| `GET /chat-sessions/{id}` | 恢复会话和历史问答 |

问答请求可带 `session_id`。追问结合历史改写为独立检索问题，再依据本轮证据生成带 `[n]` 引用的答案。前端支持会话恢复；中断、模型故障及证据不足均记录生成状态。

答案生成与追问改写独立使用 `DEEPSEEK_*` 配置；`LLM_PROVIDER` 控制查询优化 agent，默认 `fake` 为规则实现，设为 `deepseek` 才启用模型优化。缺少生成密钥或模型失败时返回明确标注的证据摘录；没有证据时不调用生成模型。

协议、上下文预算与持久化说明见 [AI 问答](docs/ai-chat.md)。2026-10-21 的 [部署验收](docs/ai-chat-deployment-2026-10-21.md) 记录了两轮真实流式调用和追问改写，不代表已完成所有场景的回答质量评估。

## 演示路径

先完成上文的依赖安装、数据库端口与向量服务配置。

可以选择冒烟脚本路径或手动路径。不要先手动播种/处理某个 run，随后又运行冒烟脚本并期望它校验同一个 run：冒烟脚本会自行播种并处理它自己的目标 run。

### 冒烟脚本路径（API 已运行）

在一个终端中启动 API：

```bash
.venv/bin/uvicorn app.main:app --app-dir backend --reload
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
QDRANT_URL=memory://smoke-demo .venv/bin/uvicorn app.main:app --app-dir backend --reload
# 终端 2
SKIP_DOCKER=1 QDRANT_URL=memory://smoke-demo bash scripts/smoke_demo.sh
```

由于 `memory://...` 和 `:memory:` 向量存储是进程本地的，独立的 API 和 worker 进程不会共享内存向量。在这个便捷模式下，冒烟脚本会有意跳过跨进程向量 trace 断言，但保留 SQL/source/canonical 的 `/search` 和 `/answer` 检查。需要真实向量冒烟时，请使用默认 Docker/Qdrant 路径。

### 手动演示路径

启动基础设施并运行迁移：

```bash
test -f .env || cp .env.example .env
docker compose up -d postgres qdrant
.venv/bin/alembic upgrade head
```

播种演示数据，提取播种得到的 run id 和 source id，并且只处理该 run：

```bash
SEED_JSON="$(.venv/bin/python scripts/seed_demo.py)"
RUN_ID="$(printf '%s' "$SEED_JSON" | .venv/bin/python -c 'import json, sys; print(json.load(sys.stdin)["run_id"])')"
SOURCE_ID="$(printf '%s' "$SEED_JSON" | .venv/bin/python -c 'import json, sys; print(json.load(sys.stdin)["source_id"])')"
.venv/bin/python scripts/run_worker_once.py --json --require-success --run-id "$RUN_ID"
```

启动 API：

```bash
.venv/bin/uvicorn app.main:app --app-dir backend --reload
```

在同一个保留 `SOURCE_ID` 的终端中请求搜索和回答接口；使用新终端时先设置为播种得到的来源 ID：

```bash
curl -fsS -X POST http://localhost:8000/search \
  -H 'Content-Type: application/json' \
  -d "{\"query\":\"telemetry settings\",\"mode\":\"search\",\"filters\":{\"source_site_id\":\"$SOURCE_ID\"},\"top_k\":5}"

curl -fsS -X POST http://localhost:8000/answer \
  -H 'Content-Type: application/json' \
  -d "{\"query\":\"How is telemetry configured?\",\"mode\":\"answer\",\"filters\":{\"source_site_id\":\"$SOURCE_ID\"},\"top_k\":5}"
```

### 前端

按上文“本地环境配置”启动前端，进入“检索”或“AI 问答”页面。

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
- **切换 embedding 模型后需重新生成向量**，即使维度相同也不能混用不同语义空间。推荐更新 `.env` 中的模型、维度及 `QDRANT_COLLECTION` 为新的集合，再运行：

```bash
.venv/bin/python scripts/reindex_embeddings.py
```

`--reset-collection` 会删除并重建目标集合，仅在确认需要清空该集合时使用。iFixit 的版本校验、真实 tokenizer 校验和旧向量清理应使用其专用 `index` / `cleanup` 命令，全局重建脚本不负责这些步骤。

## 无人机信息（第一版）

前端「工作空间 → 无人机信息」展示本地收集的 DJI 型号与技术参数，支持型号搜索、用途分类、参数分组和参数内容搜索。页面保留官方来源、采集时间、参数适用条件，以及索引中的资料缺失说明。

后端直接读取 `data/DJI无人机文档/技术参数索引.csv` 和各型号的 `技术参数.html`，无需导入数据库。`GET /drones` 返回型号与收集概况，`GET /drones/{slug}` 返回按分组整理的参数。更新目录中的索引和文件后，重新进入页面即可读取新内容。

本地运行可通过 `DRONE_DOCUMENTS_DIR` 指定文档目录，默认是项目根目录下的 `data/DJI无人机文档`。Docker Compose 将宿主机 `DJI_DOCUMENTS_DIR`（默认同一目录）以只读方式挂载到容器 `/data/dji-documents`；资料不打包进镜像。部署前应将该目录同步至宿主机，并重新构建和启动应用：

```bash
docker compose -f docker-compose.deploy.yml up -d --build app
```

## 视频知识图谱（第一版）

视频知识图谱第一版提供历史视频构图、增量轮询、字幕证据定位和前端浏览代码，入口位于 `tools/video-knowledge-graph/`。迁移、模型配置、使用命令及 GraphRAG 接口说明见 [视频知识图谱说明](tools/video-knowledge-graph/README.md)。

2026-10-23 已正式部署白色背景的 AntV G6 图谱工作台，仅保留交互视图，支持拖拽、悬停邻居与连线高亮、入链/出链与字幕证据面板。图谱与供应链、AI 问答共用左侧导航，移除图谱顶部标题、说明及系统顶栏，图谱区域铺满导航右侧的可用空间；从左侧“视频知识图谱”进入，或访问 `http://localhost:8000/?view=VideoGraph`，镜像和回退说明见 [当前部署记录](docs/graph-ui-navigation-2026-10-23.md)。

当前正式版以力导向布局提供限量概览：桌面默认最多 300 个节点、500 条关系，手动展开最多 500 个节点、1,000 条关系；移动端最多 150 个节点、300 条关系。Neo4j 保留全部图谱，可通过搜索与聚焦查询其他内容，详见 [展示数量与性能配置](docs/graph-ui-performance-2026-10-23.md)。

2026-10-26 调整展示选取规则：隐藏少于 5 个节点的独立子图，优先展示连接较多、相互关联紧密的区域；先保留保证连通的骨架，再补充节点间关系，避免数量截断后出现碎片。搜索、筛选和邻居展开沿用此规则，底层数据与字幕证据完整保留，详见 [连通图谱展示说明](docs/graph-ui-connected-2026-10-26.md)。

图谱工具栏现支持 **2D / 3D** 切换，桌面首次进入默认三维、手机默认二维，并记住用户选择。三维模式使用 G6 官方扩展，支持球体光照、点击节点居中并围绕该节点上下左右 360° 连续旋转、视角平移、节点拖拽、悬停高亮、关系证据及邻居展开，保留现有数据预算和小图过滤；无 WebGL 支持时自动使用二维。操作、部署与回退见 [3D 图谱说明](docs/graph-ui-3d-2026-10-26.md)。

默认 `VIDEO_GRAPH_BACKEND=postgres`；2026-10-21 的 [迁移记录](docs/neo4j-migration-report-2026-10-21.md) 已完成历史数据导入和正式环境 Neo4j 切换。迁移步骤（包括租约和检查点）见 [Neo4j 迁移指南](docs/neo4j-migration.md)。使用 Neo4j 时，PostgreSQL 继续保存源内容，图谱、构图任务、租约及检查点以 Neo4j 为准。

```bash
# 只读盘点，不调用抽取模型
.venv/bin/python tools/video-knowledge-graph/build_graph.py inventory --limit 20
# 排队并最多处理一个视频；相关视频的各个切片分别调用模型
.venv/bin/python tools/video-knowledge-graph/build_graph.py run --limit 1
```

构图需 `DEEPSEEK_API_KEY`。`--limit` 仅限制处理数量，`run` 仍会扫描并排队整个范围；限定视频使用 `--content-id VIDEO_UUID`。实体和关系来自已保存的字幕证据，无字幕时仅生成摘要实体提及；图谱接口为 `/video-graph/*`。失败、部分完成、检查点恢复与增量轮询见工具说明。

## 开发验证

```bash
make test
make lint
npm --prefix frontend test
npm --prefix frontend run build
# iFixit 测试不在 make test 的默认集合中
.venv/bin/pytest -q tools/ifixit_rag/tests
```

后端与 worker 的数据库测试会清空并重建测试库中的应用表。提前创建独立的 `intelligence_rag_test`，设置环境变量 `TEST_DATABASE_URL` 为完整连接串；使用开发 Compose 时主机端口为 `55432`，测试默认值仍为 `54329`。保护检查要求库名以 `_test` 结尾。

Neo4j、视频图谱和 iFixit 集成测试使用各自文档指定的测试连接，部分测试在未提供连接时跳过。本 README 更新仅核对文档与代码，完整测试和外部服务可用性以实际验证结果为准。

## 核心不变量

- 在调用抽取 agent 之前，必须先持久化 raw pages。
- PostgreSQL 保存源内容事实；使用 Neo4j 图谱后端时，图谱及构图任务以 Neo4j 为准。
- Qdrant 只保存用于检索的 chunk 向量和 payload。
- 前端只调用后端 API。
- 搜索和回答响应会暴露证据对象，其中包含 source 和 content 链接。

## 详细文档

- [系统总述与进展](docs/system-overview-and-progress.md) · [历史数据库概览（2026-10-09）](docs/intelligence-database-summary.md)
- [Linux / WSL 部署](docs/linux-server-deployment.md) · [Docker 部署](docs/docker-deploy.md) · [部署验证记录](docs/deployment-validation.md)
- [论坛采集与加工](tools/forum-crawler/scripts/README.md) · [定时任务控制](docs/scheduled-task-control.md)
- [通用网页采集：百度搜索、原文证据及标准化 JSONL](tools/web-crawler/README.md)
- [AI 问答实现](docs/ai-chat.md) · [AI 问答部署验收（2026-10-21）](docs/ai-chat-deployment-2026-10-21.md)
- [型号检索排序修复与验证（2026-10-21）](docs/retrieval-ranking-fix-2026-10-21.md)
- [视频知识图谱](tools/video-knowledge-graph/README.md) · [Neo4j 迁移指南](docs/neo4j-migration.md) · [迁移结果](docs/neo4j-migration-report-2026-10-21.md)
- [iFixit 导入工具](tools/ifixit_rag/README.md) · [正式导入验收（2026-10-21）](docs/ifixit-production-import-2026-10-21.md)
- [文献研究](docs/literature-research.md)
