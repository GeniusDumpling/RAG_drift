# 视频知识图谱

统一入口：`build_graph.py`。基于已经保存的视频字幕或摘要切片执行主题判断、实体抽取/审核、关系分类/独立审核、原文验证、Neo4j 发布和查询回查。

图谱实体、别名、关系、证据、任务、租约和检查点**只存 Neo4j**。PostgreSQL 保存原始内容和切片，构图只读取业务材料，发布前使用源行锁核对材料。不存在 PostgreSQL 图谱后端或 `VIDEO_GRAPH_BACKEND` 切换项。

代码组织见[模块重构说明](../../docs/video-graph-refactor-2026-11-10.md)。历史导入工具保留为 `migrate_neo4j.py`，旧 PostgreSQL 图谱表只用于历史导出和 Alembic 兼容，见[迁移指南](../../docs/neo4j-migration.md)。

## 配置

- `SYNC_DATABASE_URL`：源内容 PostgreSQL 连接；与 `DATABASE_URL` 独立。测试或切换业务库时同时检查两者。
- `NEO4J_URI`、`NEO4J_USER`、`NEO4J_PASSWORD`、`NEO4J_DATABASE`：图谱连接；宿主机通常用 `bolt://localhost:7687`，Compose 网络用 `bolt://neo4j:7687`。
- `LLM_API_KEY`、`BASE_URL`、`LLM_MODEL`：抽取模型。没有密钥不生成模拟图谱；只读盘点与 pipeline dry-run 不需要模型密钥。
- `VIDEO_GRAPH_REQUEST_TIMEOUT_SECONDS`：单次请求超时，默认120秒。
- `VIDEO_GRAPH_EXTRACTION_EFFORT`、`VIDEO_GRAPH_REASONING_EFFORT`：抽取和审核推理档位，默认均 `none`。

新任务只允许 `video-graph-v2` 及后续基础版本。完整版本包含模型、抽取规则指纹和推理档位。规则或来源上下文变化时旧审核凭据不能复用。历史 v1 结果继续查询、展示和引用，但不能用新 worker 领取、补写 v1 任务；需使用当前版本新建有界计划。

## 推荐：有界构建并验证

```bash
# 只读源材料盘点
.venv/bin/python tools/video-knowledge-graph/build_graph.py inventory --limit 20

# 只读检查有界计划；会读取 Neo4j 状态，不写计划或数据库
.venv/bin/python tools/video-knowledge-graph/build_graph.py pipeline --limit 1 \
  --manifest data/video_graph_runs/build.plan.json \
  --report data/video_graph_runs/build.report.json --dry-run

# 抽取 → 发布 → 原文/图谱完整回读 → 检索验证
.venv/bin/python tools/video-knowledge-graph/build_graph.py pipeline --limit 1 \
  --manifest data/video_graph_runs/build.plan.json \
  --report data/video_graph_runs/build.report.json

# 明确重试失败或部分完成任务，沿用同一计划和报告
.venv/bin/python tools/video-knowledge-graph/build_graph.py pipeline --limit 1 \
  --manifest data/video_graph_runs/build.plan.json \
  --report data/video_graph_runs/build.report.json --retry-failed
```

可增加 `--content-id VIDEO_UUID`（可重复）限定来源。计划固定来源指纹、有效版本及本次处理范围；已过期计划或其他抽取版本的计划会被拒绝，应另建清单。只有全部所选视频通过构建和查询验证才退出0；失败、部分完成、证据损坏或来源过期均退出1。已经成功的同一计划重跑会回查状态和证据，不重复调用模型。

报告 `results` 包含每个视频的构建与验证；`token_usage` 为本次进程观察的用量，`cumulative_token_usage` 累计同一清单已经保存的报告。字段包括输入/输出/缓存/推理 tokens、HTTP 尝试次数、缺少 usage 的响应数和阶段检查点复用数。缓存复用不计为新的服务商调用，未知 usage 不当作0。突然终止且尚未落盘的用量无法由报告保证完整捕获。

## 抽取与恢复

每个视频一次主题判断。每个字幕切片先识别并审核实体，然后每16个候选实体对分类一次、每8条关系独立审核一次；每段最多96对、80条关系。摘要只贡献实体，少于两个有效实体时跳过关系。

网络、429/5xx、JSON解析故障最多重试一次；结构不完整允许一次修正。拒绝的实体、属性、关系保留原因，不以反复调用强行通过语义审核。每次真实请求前续租。

同一任务、同一切片、完全相同的请求可从 Neo4j 持久化检查点恢复，覆盖抽取、审核和证据格式修正。请求绑定版本、提供方/账户哈希及完整消息；不跨任务共享原始响应。结构或来源校验失败会清理对应片段缓存，完整切片通过后只保留正常审核结果。详见[token 用量与检查点](../../docs/video-graph-token-optimization-2026-11-06.md)。

发布在一个 Neo4j 事务内检查视频状态、目标版本和租约，再切换有效构建。首次部分完成可发布通过的切片；新版本部分完成时保留旧有效图谱；所有切片无效不会发布新图谱。模型调用不在数据库事务内。

## 其他入口

```bash
# 有界回补计划和执行；不包含 pipeline 的完整查询回查
.venv/bin/python tools/video-knowledge-graph/build_graph.py plan-backfill \
  --limit 20 --manifest data/video_graph_runs/backfill.plan.json
.venv/bin/python tools/video-knowledge-graph/build_graph.py backfill \
  --manifest data/video_graph_runs/backfill.plan.json --limit 20 --batch-size 5

# 传统队列入口
.venv/bin/python tools/video-knowledge-graph/build_graph.py scan
.venv/bin/python tools/video-knowledge-graph/build_graph.py run --limit 5
.venv/bin/python tools/video-knowledge-graph/build_graph.py watch --limit 5 --interval 120

# 撤回/恢复某视频的图谱贡献
.venv/bin/python tools/video-knowledge-graph/build_graph.py withdraw --content-id VIDEO_UUID
.venv/bin/python tools/video-knowledge-graph/build_graph.py restore --content-id VIDEO_UUID
```

`plan-backfill` 清单可以包含全部候选，实际执行量由 `backfill --limit` 控制。传统 `run/watch` 的 `--limit` 只限制本轮领取的视频数，扫描仍覆盖整个指定范围；首次试运行优先使用 `pipeline` 或指定 `--content-id`。`watch` 的显式重试参数仅在第一次扫描应用，避免反复收费。中断任务在租约到期后可由新 worker 恢复。

物理删除业务内容不会通过 SQL 外键删除 Neo4j 贡献；扫描会在本次范围内对账删除并撤回视频。删除源视频前可先显式撤回。原始内容、切片及其他业务关系表不由图谱工具删除。

## API 与问答检索

`GET /video-graph/overview`、`/entities`、`/graph`、`/relations/{id}/evidence`、`/videos/{id}/status` 和 `POST /video-graph/retrieve` 保持路径与响应格式。查询只读取有效、未撤回、未删除的投影。实体作用域、断言状态、适用条件和原文引用保留。

问答检索通过 `app/services/graph_retrieval.py` 读取同一个 Neo4j 查询服务，再回到 PostgreSQL 切片验证内容、生命周期及过滤条件。Neo4j 不可用时图谱 API 返回503，普通问答可以按现有策略回退到文本检索；不会回读过期 PostgreSQL 图谱。

## 验证

golden 验证可精确重放旧请求，不产生模型费用：

```bash
.venv/bin/python scripts/evaluate_relation_gold.py \
  --output data/video_graph_runs/new-gold-result.json \
  --response-cache data/video_graph_runs/token-baseline-responses-20261106 \
  --cache-only
```

缓存绑定模型、账户和完整请求，缺失请求会报错，不会偷偷调用模型。缓存目录需由评测者实际提供。

Neo4j 集成测试必须显式选择**空的独立测试图谱**，并提供保存测试来源的独立 PostgreSQL：

```bash
VIDEO_GRAPH_TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/SOURCE_TEST_DB \
NEO4J_TEST_URI=bolt://TEST_HOST:7687 NEO4J_TEST_PASSWORD=TEST_PASSWORD \
.venv/bin/python -m pytest backend/tests/test_video_graph_build_pipeline.py \
  backend/tests/test_neo4j_graph.py -q
```

测试拒绝清理已有 `VG*` 业务节点的图谱。问答集成测试另需 `TEST_DATABASE_URL`、`SYNC_DATABASE_URL` 同时指向独立测试业务库；不要把集成测试连接到生产数据库。
