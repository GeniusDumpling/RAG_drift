# 论坛采集与 RAG 加工

ArduPilot / PX4 使用 Discourse 公开 JSON API。采集、清洗切片、向量化可以分别重试。

## 常用命令

在仓库根目录运行，使用已安装项目依赖的 Python；连接配置读取项目 `.env` 的 `SYNC_DATABASE_URL`。

```bash
# 日常增量：发现新主帖和新回复，按主题补齐全部公开正文，只保存原始数据。
.venv/bin/python tools/forum-crawler/scripts/discourse_ingest.py --json

# 原始归档 → 分类过滤 → 主帖/回复关联 → 切片。此命令不访问论坛、不调用向量模型。
.venv/bin/python tools/forum-crawler/scripts/discourse_process.py --json

# 在加工后执行向量化，并重试此前失败的向量任务。
.venv/bin/python tools/forum-crawler/scripts/discourse_process.py --embed --json

# 历史回填：每次最多扫描 10 页、下载 20 个主题，反复执行会接着上次位置继续。
.venv/bin/python tools/forum-crawler/scripts/discourse_ingest.py \
  --mode backfill --max-pages 10 --max-topics 20 --json

# 可选：只把该日期以后出现过帖子的主题加入历史回填队列。
.venv/bin/python tools/forum-crawler/scripts/discourse_ingest.py \
  --mode backfill --since 2024-10-21 --json

# 指定单个论坛、一个或多个主题。不能跨论坛复用 topic_id。
.venv/bin/python tools/forum-crawler/scripts/discourse_ingest.py \
  --forums px4 --topic-id 49575 --json

# 修改过滤策略后，逐批重新加工最久未处理的归档，不重新抓论坛。
.venv/bin/python tools/forum-crawler/scripts/discourse_process.py \
  --reprocess --limit 20 --json

# 手动搜索预览，不入库；搜索不作为定时增量的发现入口。
.venv/bin/python tools/forum-crawler/scripts/discourse_search.py \
  --query 'GPS spoofing' --forums ardupilot px4 --max-pages 3 --json

# 执行一次与定时任务相同的采集及加工流程。
bash tools/forum-crawler/scripts/run_scheduled_discourse.sh

# 单轮历史回填及加工，使用相同互斥锁；不执行旧主题周期复核。
bash tools/forum-crawler/scripts/run_scheduled_backfill.sh
```

首次增量运行以最新公开帖子的一页（通常 50 条）为起点，不声称已经抓完整个历史。
历史回填有独立游标；新内容任务优先于历史任务，历史任务优先于周期复核。
回填使用帖子 ID 游标；`--since` 是筛选条件，不会根据一页的时间直接提前停止，避免导入/回溯日期的帖子造成漏项。
同一历史回填进度启动后应保持相同 `--since`；默认不设置日期即遍历公开历史。

`--max-results` 仍可作为 `--max-topics` 的兼容别名。旧 `--query` 入库命令仍支持，
但现在会展开命中的主题，不再只保存单个搜索命中。`dronecode` 参数归一化为 `px4`，不会重复创建来源。

## 存储与恢复

脚本第一次运行自动创建两个辅助表，需要数据库账号具有建表权限：

| 表 | 作用 |
| --- | --- |
| `forum_sync_states` | 每站增量游标、未完成扫描位置、历史回填游标、分类树缓存 |
| `forum_topic_tasks` | 每个主题的采集/加工状态、优先级、重试时间、最新完整快照引用 |

这两个表使用脚本独立的 SQLAlchemy metadata，不加入应用模型/Alembic 注册表。
`source_sites`、`crawl_jobs`、`crawl_runs`、`raw_pages`、`authors`、`content_items`、`content_chunks`
仍使用已有应用结构，需要事先完成项目数据库迁移。辅助表的创建不会自动升级未来版本的列结构。

- 原始 HTTP JSON 响应逐批保存到 `raw_pages`；即使一个长主题后来缺少部分回复，成功下载的响应也保留。
- 以 `post_stream.stream` 为当前可见 ID 清单，批量补齐正文；缺少正文或主帖时不生成“完整”快照。
- 完整组装的主题另存为 `parser_profile=discourse_topic_v2` 快照，任务引用最新快照。
- 原始抓取和完整快照先提交，再由独立处理脚本生成帖子及切片。
- 帖子以规范化论坛地址与 `post_id` 识别；关键词、浏览量、点赞数不参与身份或正文版本判断。
- 新旧正文和处理策略一致时不重复切片；正文、父回复上下文或策略版本变化时，保留旧原始快照，淘汰旧切片并创建新切片。
- 回复同时保存 `thread_root_id` 和 `parent_item_id`；向量文本附加原问题和必要父回复上下文。
- 删除/隐藏的主题不会因为一次 403/404 被自动清空；错误任务延迟重试。完整主题快照中消失的帖子会退出检索。
- 向量化开启后，过期切片的旧向量引用保留到删除成功；失败可以再次运行 `--embed`。
- 旧版本跨关键词造成的同源重复帖子在被重新处理时归并到一个稳定记录，旧重复切片退出检索，不删除历史内容记录。

增量分页每成功一页，就在同一事务中持久化主题任务和扫描位置。扫描未触达上次游标时，
只保存扫描位置，不提前提升完成水位。达到 `--max-pages` 后下次续跑。
完成水位只表示内容已被可靠发现并进入队列，不表示正文和向量全部完成；输出的
`capture_pending`、`process_pending` 应与游标一起监控。

## 限速、隔离及调度

论坛常态增量和历史回填使用两个独立定时器，时间固定为北京时间：

| 用户级 systemd timer | 执行时间 | 单轮运行上限 |
| --- | --- | --- |
| `rag-discourse-collect.timer` | 偶数小时 30 分 | 30 分钟 |
| `rag-discourse-backfill.timer` | 每小时 05 分 | 20 分钟 |

每轮默认每站扫描 10 页、采集 20 个主题，然后处理 20 个主题快照。
历史任务使用独立游标，完成历史扫描后仍可处理积压任务；与增量任务共用互斥锁。
两个任务均继承 `.env` 中的 `FORUM_EMBED`，默认不调用向量服务。

安装用户级 timer（只替换这两个任务的四个 unit 文件，不修改其他定时任务）：

```bash
.venv/bin/python tools/forum-crawler/scripts/install_timers.py
systemd-analyze --user verify ~/.config/systemd/user/rag-discourse-{collect,backfill}.{service,timer}
systemctl --user daemon-reload
systemctl --user enable --now rag-discourse-collect.timer rag-discourse-backfill.timer
systemctl --user list-timers 'rag-discourse-*' --no-pager
journalctl --user -u rag-discourse-collect.service -u rag-discourse-backfill.service -n 50
```

可使用 `--unit-dir /tmp/forum-units` 先生成配置进行检查。
Docker 部署提供对应的 `intelligence-rag-discourse-{collect,backfill}` unit；
`deploy/run-job.sh discourse-backfill` 与增量任务共享宿主机锁。
不要在同一数据库上重复启用用户级和 Docker 部署两套 timer。

- 默认每站请求间隔 1 秒，超时 30 秒，最多额外重试 3 次。
- 对网络错误、429、暂时性 5xx 指数退避；遵循 `Retry-After`。服务端要求等待超过 60 秒时交给持久化任务稍后重试。
- 单个主题或论坛失败不会中断其他主题/论坛。部分失败返回非零退出码，并正确写入运行状态。
- 采集和加工共享每站 PostgreSQL advisory lock，防止跨进程同时修改同一个论坛。
- 定时 Shell 入口另有 `flock`，即使采集失败也会尝试处理此前保存的归档。
- 默认每轮额外复核 3 个最久未抓取且超过 24 小时的主题。旧帖编辑、可见性变化的发现延迟取决于复核预算；这不是实时事件同步保证。
- 全量历史较大时需提高 `FORUM_REFRESH_LIMIT` 或安排独立复核预算；回填与增量命令应分别持续执行。
- 不在运行时安装依赖，不修改 cron/systemd 时间表达式；宿主机和 Docker 可使用相同入口。

Shell 环境变量：

| 变量 | 默认值 |
| --- | --- |
| `FORUM_MAX_PAGES` | 10 |
| `FORUM_MAX_TOPICS` | 每站 20 |
| `FORUM_PROCESS_LIMIT` | 每站 20 |
| `FORUM_REFRESH_LIMIT` | 每站 3 |
| `FORUM_REQUEST_DELAY` | 1 秒 |
| `FORUM_EMBED` | 0（设为 1 才调用向量服务） |
| `FORUM_EMBED_LIMIT` | 每站 200 个向量任务，包括过期向量删除 |
| `FORUM_LOCK_FILE` | `/tmp/rag-forum-pipeline.lock` |
| `PY` | 优先使用项目 `.venv/bin/python`，否则系统 Python |

## 过滤策略

默认策略在 `discourse_policy.json`；`--policy /path/policy.json` 可替换。修改策略时更新 `version`，再执行 `--reprocess`。

- 分类按完整父子路径判断，技术子分类继承技术父分类。
- 招聘、买卖等分类和明显推广标题默认拒绝进入向量索引。
- General / Uncategorized 等混合分类会检查技术词和上下文，不直接丢弃整个分类。
- 结果为 `keep`、`review` 或 `reject`；所有已归档正文保留。`review` 和 `reject` 默认不生成切片。
- 纯感谢、顶帖、纯重复引用、系统动作、仅图片占位等不单独切片；验证成功的简短回复仍可保留。
- 已采纳答案记录为 `accepted`，其他内容默认为 `unverified`；普通建议不会自动当作已证实结论。

目前是可解释的规则过滤，不是经过人工标注评估的语义分类器。
规则无法保证完全识别广告、判断答案正确性或自动解析飞行日志/图片；原始附件引用保留在正文中，可后续单独处理。
脚本不提供数据库故障时的本地落盘队列：数据库不可用时不会推进游标，恢复后重试。

## 验证

```bash
.venv/bin/pytest -q tests/unit/test_discourse_pipeline.py tests/unit/test_job_runner_portability.py
bash -n tools/forum-crawler/scripts/run_scheduled_discourse.sh
```

设置 `FORUM_TEST_DATABASE_URL` 后，论坛测试使用 PostgreSQL 的随机临时 schema，结束后自动清理；
不修改已有业务表。默认使用临时 SQLite，PostgreSQL advisory lock 测试会跳过。

2026-10-21 验证记录：论坛 PostgreSQL 回归 22 项通过；本地论坛、任务入口和调度接口回归
36 项通过、1 项 PostgreSQL 专用锁测试跳过。真实 API 在临时 PostgreSQL schema 中验证：
ArduPilot 主题 141753 保存 1 条主帖及 21 条回复、生成 23 个切片；PX4 主题 49575 保存
1 条主帖及 22 条回复、生成 37 个切片。临时 schema 已清理，没有调用付费向量服务。
