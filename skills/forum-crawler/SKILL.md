---
name: forum-crawler
description: 从公共无人机论坛采集帖子并入库 RAG。含 Discourse（ArduPilot/PX4）关键词检索、增量入库；以及 DJI 官方论坛系列主题/评论导入。
---

# Forum Crawler — 论坛帖子证据入库

## 适用范围

从公开、免鉴权的论坛接口采集无人机相关内容（型号、零件、漏洞、安全），并落库 RAG。
当前覆盖两类数据源：

1. **Discourse 论坛**（`discuss.ardupilot.org`、`discuss.px4.io`）：用关键词经 `/search.json` 检索命中帖子。
2. **DJI 官方论坛**（`bbs.dji.com`）：按设备系列导入主题与评论。

只使用公开无鉴权的 API，不绕过登录、验证码或限流。

## 数据落库格式（四层）

每次采集统一映射为四层存储，与 `ingest_dji_lito.py` 一致：

- **控制层**：`SourceSite`（数据源）+ `CrawlJob`（任务）+ `CrawlRun`（单次运行，含 `CrawlRunEvent` 事件流）。
- **RawPage**：原始 API JSON 快照（`raw_json`）+ 清洗后原文（`raw_text`）+ `body_hash`。
- **Author + ContentItem**：结构化帖子，`item_type="post"`，`canonical_url` 为 `/t/{slug}/{id}/{post}` 规范链接。
- **ContentChunk**：`build_chunks` 切分 + `ingest_source: discourse_api` 元数据，待 Qdrant 嵌入。

去重键：`stable_hash("{source_id}:{canonical_url}:post:{content_hash}")`。

## 脚本清单（`scripts/`）

| 脚本 | 作用 |
|---|---|
| `discourse_search.py` | 仅搜索+规范化候选，输出 JSON，不入库。 |
| `discourse_ingest.py` | 搜索→查重→`/posts/{id}.json` 取全文→落库四层。 |
| `run_scheduled_discourse.sh` | 定时增量采集入口（主题轮换）。 |
| `ingest_dji_lito.py` | DJI 官方论坛系列主题/评论导入。 |
| `demo_proxy.py` | 前端演示代理（DJI demo）。 |

## Discourse 采集：搜索

```bash
python3 skills/forum-crawler/scripts/discourse_search.py \
  --query "GPS spoofing" --forums ardupilot px4 --max-results 10 --json
```

- `--forums` 可多选（`ardupilot` `px4`；`dronecode` 已 301 合并进 `px4`）。
- `search.json` 的 `post` 对象不带标题，标题/链接在顶层 `topics` 集合，需按 `topic_id` 映射补全。
- `limit` 参数会被 Discourse 忽略（要 5 条常返回 50 条），脚本内按 `--max-results` 截断。

## Discourse 采集：入库 + 增量

```bash
cd /home/gaowei/projects/RAG_drift
.venv/bin/python skills/forum-crawler/scripts/discourse_ingest.py \
  --query "GPS spoofing" --forums ardupilot px4 \
  --max-results 10 --request-delay 0.3 --json
```

- `--query` 必填；`--max-results` 1-50；`--request-delay` 控制请求频率；`--language` 默认 `en`。
- 正文取自 `/posts/{id}.json` 的 `raw` 字段（无需 HTML 解析）。
- 每个候选独立 `try/except`，单条失败不影响其余。
- 增量靠去重键：重复帖自动跳过，只入新帖。

## 定时常驻采集

```bash
bash skills/forum-crawler/scripts/run_scheduled_discourse.sh
```

- 每 2 小时触发一次；按 `date +%s / 7200 % N` 轮换 10 个搜索主题，覆盖型号/零件/漏洞/安全。
- 单次对 ardupilot、px4 各取 10 条候选并增量入库。

**搜索词注意事项**：Discourse 全文检索对冗长组合词/含特殊字符的词命中很差
（如 "drone firmware vulnerability CVE" → 0 帖）。用短的高命中概念词，例如：
`GPS spoofing`、`GPS jamming`、`MAVLink security`、`EKF failsafe`、`ESC failure`、
`flyaway`、`compass calibration`、`firmware vulnerability`、`RTK`、`propeller`。

### 本机实际使用：cron（与 video 采集统一）

> **NOTE**：本开发机（`cron.service` 守护进程运行中）实际采用用户级 **cron** 调度，
> 与 video 采集共用同一机制。已安装到当前用户 crontab：

```cron
0 */2 * * * cd /home/gaowei/projects/RAG_drift && flock -n /tmp/discourse_crawler.lock bash skills/forum-crawler/scripts/run_scheduled_discourse.sh >> /home/gaowei/discourse_collect.log 2>&1
```

- 每 2 小时触发一次，日志追加到 `/home/gaowei/discourse_collect.log`。
- 用 `flock -n /tmp/discourse_crawler.lock` 防止单次运行未结束就重复触发。
- 同一台机器上 video 采集为独立 lock（`/tmp/video_collect.lock`），互不阻塞。

### 备选：systemd user timer（无需 cron 时）

本机未采用；仅当目标环境没有 cron 时应改用此方案。可作为 `--user` timer 运行：

```ini
# /etc/systemd/system/discourse-crawler.timer
[Unit]
Description=Discourse forum incremental crawler

[Timer]
OnCalendar=*-*-* 0/2:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# /etc/systemd/system/discourse-crawler.service
[Unit]
Description=Discourse forum incremental crawl (one run)

[Service]
Type=oneshot
WorkingDirectory=/home/gaowei/projects/RAG_drift
ExecStart=/bin/bash skills/forum-crawler/scripts/run_scheduled_discourse.sh
```

## DJI 官方论坛（历史）

见 `ingest_dji_lito.py`，使用 `bbs.dji.com/api/v2/forum/...` 公开接口导入系列主题与评论。
命令示例：

```bash
cd /root/intelligence-rag
.venv/bin/python skills/dji-lito-demo-ingestion/scripts/ingest_dji_lito.py \
  --series lito --thread-limit 5 --reply-limit 20 --json
```

## 完成核验

1. 顶层 `status` 为 `success`，单个论坛 `status` 为 `success`/`partial`。
2. `raw_pages_created`、`posts_created`、`chunks_created` 均 > 0（首次）或 `deduped` 增加（增量）。
3. 数据库中 `structured_by='discourse_api_importer'` 且 `item_type='post'` 的记录等于入库数；
   `content_chunks` 的块数与 `chunked_count` 一致。