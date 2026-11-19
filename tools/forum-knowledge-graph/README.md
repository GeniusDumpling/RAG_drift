# 论坛知识图谱

从 PostgreSQL 中已加工的 ArduPilot/PX4 完整主题读取主帖及回复，先判断技术相关性和入图价值，再抽取、独立审核、发布论坛原生 `FG*` 和统一图谱 `KG*`。一个主题仍对应一个业务 item；构图不修改采集数据、采集水位或向量。

设计见 [论坛管线设计](../../docs/forum-knowledge-graph-design-2026-11-11.md)，实现及验证记录见 [验收记录](../../docs/forum-graph-implementation-2026-11-12.md)。当前为文本阶段：图片、飞行日志附件、链接目标保留未解析状态。

2026-11-19 已新增独立论坛实例并切换前端展示，端口、固定 100 项构图命令和回滚方法见 [论坛部署说明](../../docs/forum-graph-deployment-2026-11-19.md)。

## 配置

在仓库根目录运行，Python 环境安装项目及开发依赖。默认读取根目录 `.env`，也可在子命令前传入 `--env-file PATH`；显式文件不存在会失败。额外配置文件覆盖 `.env`，进程环境变量优先。

```dotenv
# 业务来源；管线不修改业务行。发布前会短暂取得来源行锁。
SYNC_DATABASE_URL=postgresql+psycopg://reader:password@localhost:5432/rag

# 与当前项目共用的 OpenAI compatible 文本模型配置。
BASE_URL=https://your-provider.example/v1
LLM_API_KEY=
LLM_MODEL=
# 留空则使用 LLM_MODEL，可为前置判断单独选择轻量模型。
FORUM_GRAPH_ASSESSMENT_MODEL=

# 先在独立 Neo4j 目标验证；缺失目标/密码会拒绝连接，不回退视频图谱。
UNIFIED_GRAPH_ENABLED=false
FORUM_GRAPH_NEO4J_URI=bolt://localhost:7691
FORUM_GRAPH_NEO4J_USER=neo4j
FORUM_GRAPH_NEO4J_PASSWORD=
FORUM_GRAPH_NEO4J_DATABASE=neo4j
FORUM_GRAPH_ARTIFACTS_DIR=data/forum_graph_runs
FORUM_GRAPH_WINDOW_CHARS=4000
FORUM_GRAPH_CONTEXT_CHARS=12000
FORUM_GRAPH_TIMEOUT_SECONDS=120
FORUM_GRAPH_DEBOUNCE_SECONDS=60
FORUM_GRAPH_MAX_WAIT_SECONDS=1800

# 这两个开关控制 API/问答读取；CLI 构建不依赖读取开关。
FORUM_GRAPH_ENABLED=false
ANSWER_GRAPH_ENABLED=false
```

接入统一图谱时，设置 `UNIFIED_GRAPH_ENABLED=true` 并完整配置 `UNIFIED_GRAPH_NEO4J_*`，论坛两层同时写入该实例。不会跨两个 Neo4j 做“原子发布”。启用 API 需要 `FORUM_GRAPH_ENABLED=true`；启用聊天论坛图谱召回还需要 `ANSWER_GRAPH_ENABLED=true`。仅安装代码不会改变运行中的服务配置。

盘点和查询只需要读取权限。构建在最终发布前使用 `SELECT ... FOR UPDATE` 核对来源，需要 PostgreSQL 对应的行锁权限，不能使用强制只读事务；代码不对业务行执行插入、更新或删除。

## 盘点、计划与运行

```bash
# 仅读源库；limit 是主题数，不是楼层数。
.venv/bin/python tools/forum-knowledge-graph/build_graph.py inventory \
  --forums ardupilot px4 --limit 20

# 仅读源库和图谱，不创建计划、报告、约束，不调用模型。
.venv/bin/python tools/forum-knowledge-graph/build_graph.py pipeline \
  --forums ardupilot px4 --limit 10 --dry-run \
  --manifest data/forum_graph_runs/batch-001.plan.json \
  --report data/forum_graph_runs/batch-001.report.json \
  --response-cache data/forum_graph_runs/responses

# 将待处理范围冻结成文件；plan 写本地文件，不写图谱、不调用模型。
.venv/bin/python tools/forum-knowledge-graph/build_graph.py plan \
  --forums ardupilot px4 --limit 10 \
  --manifest data/forum_graph_runs/batch-001.plan.json

# 执行同一固定计划。
.venv/bin/python tools/forum-knowledge-graph/build_graph.py pipeline \
  --limit 10 --manifest data/forum_graph_runs/batch-001.plan.json \
  --report data/forum_graph_runs/batch-001.report.json \
  --response-cache data/forum_graph_runs/responses
```

可重复 `--content-id UUID` 限定主题。显式主题绕过自动合并等待，但仍须是完整、已加工且未撤回的来源。计划、报告、响应缓存及复核输出必须位于 `FORUM_GRAPH_ARTIFACTS_DIR`。不要覆盖已经执行的计划或把另一个计划写到旧报告路径。

计划绑定目标实例、来源指纹、规则版本和限额。原文更新、规则变更或切换目标后，应建立新计划；旧计划不能自动扩大范围或替换更新的目标。正常重跑复用有效检查点与精确请求缓存。需要重试失败或暂缓的任务时，在上述 `pipeline` 命令末尾加 `--retry-failed`；可加 `--cache-only` 强制无外部模型调用，缺少精确响应会失败。

返回码：完成或仅读返回 0，未完成、核验失败或配置错误返回 1。终态 `skipped` 表示已完成判断且无须抽取；`deferred` 表示待材料/复核，会使批次报告为 `incomplete`。空批次表示当前没有可执行任务，合并等待或需显式重试的原因保存在计划的 `waiting` 中。

## 增量与恢复

```bash
# 单轮增量；适合由现有调度器定时调用，每轮产生新计划和报告。
.venv/bin/python tools/forum-knowledge-graph/build_graph.py watch \
  --forums ardupilot px4 --limit 20 --once \
  --response-cache data/forum_graph_runs/responses

# 持续运行；不传 --once，每 300 秒发现一次变化。
.venv/bin/python tools/forum-knowledge-graph/build_graph.py watch \
  --forums ardupilot px4 --limit 20 --interval 300 \
  --response-cache data/forum_graph_runs/responses
```

自动增量将短时间内的已知主题更新合并；默认观察到变化后静默 60 秒或累计等待达到 1800 秒即进入构建，精度受轮询周期影响。连续变化不重置最早等待时间。最大等待约束的是进入构建的等待；模型运行期间来源又变化时，仍拒绝发布失效结果，下一轮处理新快照。首次历史主题直接进入有界批次；批次至少 5 项时为旧积压保留约 20% 配额。

新增回复仅使其自身及实际依赖的上下文失效。正文未变的重新抓取不重做模型判断。跳过主题新增有效回复会重新判断。过期运行租约可自动接管；HTTP 错误最多尝试两次，结构错误最多修复一次，合法审核拒绝不会为追求非空图谱反复调用模型。`watch --retry-failed` 仅在启动首轮重试终态失败/暂缓任务，避免每轮无界重试。

删除的主题在限定范围内对账并撤回；来源变化或不可校验时拒绝查询旧证据。规则升级失败、实际来源未变且旧版本仍能通过当前回读验证时，查询可继续使用上一有效构建，状态接口同时显示失败的新目标。不兼容的旧版本回退文本检索。全部窗口达到明确终态并通过检查后，才在同一事务激活 `FG*` 与 `KG*`。

本工具提供调度入口，不安装定时任务，也不更改现有论坛采集任务。

## 状态、查询、复核与撤回

以下以 `forum:discuss.px4.io:123` 演示来源身份：

```bash
.venv/bin/python tools/forum-knowledge-graph/build_graph.py status \
  --source-id forum:discuss.px4.io:123
.venv/bin/python tools/forum-knowledge-graph/build_graph.py query --q GPS --limit 20
.venv/bin/python tools/forum-knowledge-graph/build_graph.py verify \
  --source-id forum:discuss.px4.io:123 \
  --output data/forum_graph_runs/123.review.json
.venv/bin/python tools/forum-knowledge-graph/build_graph.py withdraw \
  --source-id forum:discuss.px4.io:123
.venv/bin/python tools/forum-knowledge-graph/build_graph.py restore \
  --source-id forum:discuss.px4.io:123
```

`verify --output` 导出完整原生节点/边、共享投影、判断、审核和验证结果，同时生成转义后的 HTML。`status` 只返回任务摘要，不返回模型请求缓存。恢复只解除撤回状态；之后须以新计划重新验证/激活，不能直接恢复历史证据。

HTTP 仅读接口：`GET /forum-graph/status?source_id=...`、`GET /forum-graph/verify?source_id=...`、`GET /forum-graph/retrieve?q=...&limit=20&offset=0`。主 API 与独立统一图谱 API 都已注册。未启用或当前证据不可用时返回 503；查询错误不会使聊天失去文本检索回退。

RAG 保留问题、操作建议及反馈的具体楼层、作者、条件和链接。其他用户的案例不继承楼主设备；建议、获采纳和作者报告成功分开表示。只有明确且同案例的反馈能关联具体建议，联合操作不拆成单项成功。

## 验证

无需外部模型的基础检查：

```bash
.venv/bin/pytest -q backend/tests/test_forum_graph.py backend/tests/test_forum_graph_client.py
.venv/bin/ruff check backend/app/services/forum_graph backend/app/api/forum_graph.py \
  backend/tests/test_forum_graph.py backend/tests/test_forum_graph_client.py \
  tools/forum-knowledge-graph
```

未配置隔离 Neo4j 时，生命周期集成用例会明确跳过。完整集成需专用空 Neo4j（测试夹具限定 `bolt://localhost:45970`、用户名 `neo4j`、密码 `forum-graph-test-password`）及临时 PostgreSQL 测试库。设置 `FORUM_GRAPH_TEST_NEO4J_URI` 和 `FORUM_TEST_DATABASE_URL` 后运行同一测试命令；每例 PostgreSQL 使用随机 schema 并在结束后清理，Neo4j 测试会清空该测试实例。不要将测试变量指向业务实例。前端证据卡、聊天与构建验证命令及实际结果见验收记录。

模型质量应在独立冻结标注集上评价。程序回读一致、单元测试通过和开发样例通过都不能证明全论坛语义质量或召回率；已运行的真实样例范围、反例及 token 用量单独记录。
