# 无人机论坛 / 视频采集 + RAG 部署指南

本文档用于在一台**全新服务器**上部署本项目并启动定时采集任务。整体分两组：

- **RAG 服务**（Postgres + Qdrant + API/前端）→ 用 Docker Compose 部署。
- **定时采集器**（论坛 + YouTube 视频）→ 跑在**宿主机**上，直接写同一个 Postgres/Qdrant。

> 关键点：采集脚本需要访问 `app.*` 后端代码与向量库，因此必须在宿主机装一份 Python
> 项目环境，并让 `.env` 里的 DB/Qdrant 地址指向 Docker 暴露的端口。

---

## 0. 架构与端口约定

```
宿主机
├─ Docker Compose
│   ├─ postgres   → 127.0.0.1:54329   (DB  intelligence_rag)
│   ├─ qdrant     → 127.0.0.1:6333    (向量库)
│   └─ app        → 0.0.0.0:8000      (RAG API + 前端，构建自 Dockerfile)
└─ 定时采集器（cron）
    ├─ run_scheduled_discourse.sh  → .venv/bin/python  (论坛)
    └─ run_scheduled_collect.sh    → uv run --extra ... (YouTube 视频)
```

---

## 1. 前置条件（宿主机）

| 组件 | 用途 | 检查 |
|---|---|---|
| Docker + Compose | 跑 Postgres/Qdrant/API | `docker --version` |
| Python 3.12 | 项目 venv（采集器运行环境） | `python3 --version` |
| uv（可选但推荐） | 视频采集的 extra 依赖 | `uv --version` |
| git / 代码拷贝 | 拉取仓库 | `git --version` |
| ffmpeg | 视频关键帧/转码 | `ffmpeg -version` |

安装示例（Ubuntu/Debian）：

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 python3.12 python3.12-venv ffmpeg git
sudo systemctl enable --now docker
# uv（若需）
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 2. 步骤 A：部署 RAG 服务（Docker）

```bash
cd /path/to/RAG_drift

# 仅构建并启动 postgres+qdrant（本地开发用 docker-compose.yml）
# 生产部署用 docker-compose.deploy.yml（含 app API 容器）
docker compose -f docker-compose.deploy.yml up -d --build
```

Compose 会自动构建 `app`（Dockerfile：前端 build + 后端安装 + ffmpeg），并拉起
postgres（`127.0.0.1:54329`）与 qdrant（`127.0.0.1:6333`）。

**验证服务健康：**

```bash
curl -fsS http://127.0.0.1:8000/health
# 期望返回 {"status":"ok"} 之类；app 容器会等待 postgres/qdrant 就绪
docker ps   # 三个容器均为 healthy/running
```

> 若目标机器无法访问 Docker Hub / 需要离线，可先在内网机器 `docker save` 镜像为 tar，
> 拷贝到目标机后 `docker load`，再 `docker compose up -d`（不 `--build`）。

---

## 3. 步骤 B：宿主采集环境

采集器直接读写 Docker 暴露的 DB/Qdrant，需要在宿主机装一份项目 Python 环境。

### 3.1 创建 venv 并安装项目

```bash
cd /path/to/RAG_drift
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev,local-embeddings,video-keyframes,video-asr]"
```

- `.[dev]`：跑采集与冒烟需要的后端依赖。
- `local-embeddings`：sentence-transformers，入库时本地嵌入。
- `video-keyframes`：curl_cffi/opencv/scenedetect，视频关键帧必装。
- `video-asr`：faster-whisper，仅在需要无字幕视频 ASR 回退时安装。

### 3.2 配置 .env

复制模板并填写：

```bash
cp .env.example .env
nano .env
```

**宿主机采集器必须指向 Docker 暴露的端口**，至少包含：

```dotenv
DATABASE_URL=postgresql+psycopg://intelligence:intelligence@127.0.0.1:54329/intelligence_rag
SYNC_DATABASE_URL=postgresql+psycopg://intelligence:intelligence@127.0.0.1:54329/intelligence_rag
QDRANT_URL=http://127.0.0.1:6333
QDRANT_COLLECTION=content_chunks_bge_m3_v1

EMBEDDING_PROVIDER=siliconflow
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIMENSION=1024
# EMBEDDING_API_KEY=  # 可选，未设则复用 VLM_API_KEY

# 必填 key
VLM_API_KEY=你的VLM_Key
YOUTUBE_API_KEY=你的YouTube_API_Key
DEEPSEEK_API_KEY=你的DeepSeek_Key   # 文献 worker 需要

# 可选默认值
# VLM_BASE_URL=https://api.siliconflow.cn/v1
# VLM_MODEL=Qwen/Qwen3-Omni-30B-A3B-Instruct
# EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
```

**验证宿主能连上容器 DB / Qdrant：**

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, 'backend')
from sqlalchemy import create_engine, text
from app.core.config import get_settings
s = get_settings()
e = create_engine(s.sync_database_url)
with e.connect() as c:
    print("DB OK, source_sites =", c.execute(text("select count(*) from source_sites")).scalar())
PY
curl -fsS http://127.0.0.1:6333/healthz
```

---

## 4. 步骤 C：定时采集任务（cron）

采集器跑在宿主机，写入同一套 DB。依赖 cron 守护进程：

```bash
sudo systemctl enable --now cron      # Debian/Ubuntu；CentOS 为 crond
```

### 4.1 配置定时脚本的本地化路径

- **论坛** `skills/forum-crawler/scripts/run_scheduled_discourse.sh`：默认用 `$PWD/.venv/bin/python`，一般无需改。
- **视频** `skills/video-crawler/scripts/run_scheduled_collect.sh`：
  - 第 12 行硬编码 `UV=/home/gaowei/.local/bin/uv` —— 新服务器请改成实际 uv 路径；也建议改成可覆盖：
    ```bash
    UV="${UV:-$(command -v uv)}"
    ```
  - 默认代理 `http://127.0.0.1:7897`：视频流必须走国外/香港代理，否则 YouTube 返回 403。请改为服务器上可用的代理地址。

### 4.2 添加 cron（每 2 小时）

```bash
crontab -e
# 加入以下两行
0 */2 * * *  cd /path/to/RAG_drift && flock -n /tmp/video_collect.lock bash skills/video-crawler/scripts/run_scheduled_collect.sh >> /home/采集用户/video_collect.log 2>&1
0 */2 * * *  cd /path/to/RAG_drift && flock -n /tmp/discourse_crawler.lock bash skills/forum-crawler/scripts/run_scheduled_discourse.sh >> /home/采集用户/discourse_collect.log 2>&1
```

- `flock -n` 防单次运行未结束就重复触发。
- 视频与论坛用不同 lock 文件，互不阻塞。
- 每 2 小时自动轮换搜索主题，靠"去重键"实现增量（重复帖/视频自动跳过）。

### 4.3 首次手动触发并验证

```bash
# 论坛
bash skills/forum-crawler/scripts/run_scheduled_discourse.sh
# 视频
bash skills/video-crawler/scripts/run_scheduled_collect.sh
```

论坛输出应含 `"status":"success"` 且 `posts_created`（首次>0）或 `deduped`（增量）。
视频输出应含 `outcome` 为 `success` 的候选，并能在库中查到。

### 4.4 查看日志

```bash
tail -f /home/采集用户/discourse_collect.log
tail -f /home/采集用户/video_collect.log
```

---

## 5. 步骤 D：核验 RAG 检索

采集完成后，走 RAG API 验证证据可检索（需在容器外或内均可调用）：

```bash
curl -fsS -X POST http://127.0.0.1:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"GPS spoofing","mode":"search","top_k":3}' | python3 -m json.tool
```

DB 侧核验（宿主 venv）：

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0,'backend')
from sqlalchemy import create_engine,text
from app.core.config import get_settings
e=create_engine(get_settings().sync_database_url)
with e.connect() as c:
    print('posts:', c.execute(text("select count(*) from content_items where item_type='post'")).scalar())
    print('videos:', c.execute(text("select count(*) from content_items where item_type='video_summary'")).scalar())
PY
```

---

## 6. 排障速查

| 现象 | 原因与解决 |
|---|---|
| 视频抓取 403 | 代理缺失/失效。确认 `HTTPS_PROXY` 指向可用海外/香港节点；用 `skills/video-crawler/scripts/youtube_stream_probe.py` 探测 |
| 论坛入库报 SQL 错/连不上库 | `.env` 的 `DATABASE_URL`/`SYNC_DATABASE_URL` 端口未指向容器的 `54329` |
| cron 不触发 | `cron`/`crond` 守护进程未启动；`systemctl enable --now cron` |
| vlm 报缺 `VLM_API_KEY` | `.env` 未填 `VLM_API_KEY` |
| uv 找不到 | `run_scheduled_collect.sh` 里硬编码 uv 路径；改为 `$(command -v uv)` |
| 端口被占 | compose 映射 `54329/6333/8000` 需空闲；可改 compose 端口 |

---

## 7. 一句话快速清单

1. `docker compose -f docker-compose.deploy.yml up -d --build`
2. `curl -fsS http://127.0.0.1:8000/health`
3. `python3 -m venv .venv && .venv/bin/pip install -e ".[dev,local-embeddings,video-keyframes]"`
4. `cp .env.example .env` 并填 key + 指向 `54329/6333`
5. 把两个定时脚本的 `uv` 路径/代理改成本机
6. `crontab -e` 加两行，`flock` 防重叠
7. 手动跑一次，`tail` 日志确认 `success`