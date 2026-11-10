# 视频知识图谱模块重构（2026-11-10）

图谱相关在线代码统一归入 `backend/app/services/video_graph/`，构建、任务、检查点、发布、浏览和问答图谱检索统一使用 Neo4j。PostgreSQL 继续保存业务原文、切片和其他业务数据。

本次没有部署应用、停止线上 worker、修改生产图谱或自动发起全量回补。线上存量读取验证只执行查询。

## 职责与入口

| 模块 | 职责 |
|---|---|
| `material.py` | 不可变视频/切片模型、源数据读取与发布前源行锁、来源指纹、原文定位 |
| `extraction.py` | v2 实体抽取、关系分类及独立审核编排、唯一模型调用与传输重试 |
| `review.py` | 独立实体/关系审核、结构修正、来源及结果绑定的审核凭据 |
| `validation.py` | 原文/语义/审核校验、覆盖率与质量汇总 |
| `identity.py`、`normalization.py`、`semantics.py`、`grounding.py`、`terms.py` | 身份归一、类型/方向/断言约束、证据校验及术语表 |
| `tasks.py` | 唯一的来源扫描、任务领取后执行、切片恢复、失败处理和发布编排 |
| `storage.py` | 唯一的图谱/任务存储：Neo4j 视频锁、排队、领取、续租、检查点、事务发布、撤回、有效状态快照 |
| `queries.py`、`evidence.py` | 有效投影查询、图谱遍历、原文证据、时间定位、检索上下文 |
| `backfill.py`、`pipeline.py` | 固定范围计划、有界执行、完整发布与查询验证闭环 |
| `request_checkpoint.py`、`usage.py`、`diagnostics.py` | 阶段响应检查点、真实 token 统计、脱敏错误诊断 |

旧的 `services/video_graph.py`、`services/video_graph_*.py` 及 `services/neo4j_graph/` 已删除，不保留导入转发层。`__init__.py` 只描述包职责；调用方直接从相应职责模块导入。

`build_graph.py` 仍为命令行入口；图谱 API 的 `/video-graph/*` 路径和数据结构保持兼容。`graph_retrieval.py` 保留为问答与图谱 API 共用入口，只连接 Neo4j；`answer_graph.py` 继续回业务切片验证来源与过滤条件。

调用链为：

```text
CLI / pipeline / backfill
  → tasks.enqueue_videos / process_next
  → storage.claim / checkpoint
  → extraction → review → validation
  → storage.activate（源指纹、目标构建、租约校验与事务发布）
  → queries（图谱、提及、证据与检索验证）
```

原来 PostgreSQL 与 Neo4j 各有一份 worker、发布和查询实现，现在只剩一条 Neo4j 流程。计划和验证共用 `GraphStore.active_snapshot()`，不再分别维护 SQL/Neo4j 状态判断。阶段响应复用、切片恢复和 token 统计仍使用同一套实现。

## PostgreSQL 与 v1 的保留边界

重构前只读盘点发现：

- PostgreSQL 留有483个 v1 构建（213成功、20部分完成、250失败）、2,130条关系、2,215条证据。
- Neo4j 有294个有效视频构建，均为 `video-graph-v1:deepseek-flash`；还有历史失败/待处理/运行中任务。

因此没有删除历史数据或直接丢弃 v1 读取能力。

| 项目 | 处理 |
|---|---|
| 在线 PostgreSQL 图谱入库、排队、领取、发布、查询 | 删除 |
| `VIDEO_GRAPH_BACKEND` 配置及 API/CLI/问答后端分支 | 删除；环境模板和 Compose 不再设置该字段 |
| v1 LLM 联合抽取提示、一次调用路径和旧实体身份写入器 | 删除 |
| v1 新排队与领取 | 拒绝；CLI 和存储层都有版本门槛 |
| 所有新投影 | 必须通过 v2 独立审核及来源校验，不能关闭审核凭据检查 |
| 已发布 v1 图谱、旧 UUID、别名作用域、断言和证据 | 继续只读展示及检索，保持历史链接有效 |
| `models/video_graph.py` 与历史 Alembic | 保留历史表定义，供迁移兼容，不作为在线图谱后端 |
| `migrate_neo4j.py` | 保留单独的一次性历史导出/导入/校验工具；`video_graph_v1` 提及标记仅属于历史快照格式 |
| PostgreSQL 原始内容、切片、聊天等业务数据 | 保留，仍由原业务流程管理 |

旧 v1 任务不能直接交给新 worker 继续生成；如需升级某视频，使用当前 v2 版本新建有界计划。已有旧版本计划会因目标版本不同被拒绝。实际部署更新前应协调已有旧 worker，避免继续执行旧代码；本次未代替运维做停机或重建。

## 质量与流程验证

当前 golden 为20条人工复核片段、101个实体、77条关系。完整流程精确重放91次历史模型请求，新增模型调用0次。每条请求都按模型、提供方/账户及完整消息匹配；缺失会失败，不会转为联网调用。

| 指标 | 重构前 | 重构后 |
|---|---:|---:|
| 实体 TP / FP / FN | 88 / 10 / 13 | 88 / 10 / 13 |
| 实体类型匹配 F1 | 88.4% | 88.4% |
| 关系三元组＋断言 TP / FP / FN | 52 / 9 / 25 | 52 / 9 / 25 |
| 关系三元组＋断言 F1 | 75.4% | 75.4% |
| 关系全部限定条件严格 F1 | 59.4% | 59.4% |

20条输出中的实体、关系逐项完全一致。评分函数 AST 与重构前相同；评分脚本只调整导入与格式，golden 数据未修改。这是结构重构的行为保持验证，不是一次新的模型抽样。

Neo4j 集成覆盖首次构建、幂等、有界计划、部分完成与旧投影保留、过期租约、来源变化、撤回/恢复、证据损坏、历史导入及 v1 读取、拒绝 v1 新任务、API 不可用时503、问答回退和当前来源验证。

严格请求重放通过实际 CLI 与 HTTP fixture 完成构建、发布和检索；最后一条证据修正请求失败后更换 worker，重试只发出1个请求，另外4个成功请求从 Neo4j 检查点恢复。测试同时确认源切片仍在 PostgreSQL，而 PostgreSQL 图谱构建/证据表没有新写入。

还以当前代码只读查询了线上 Neo4j：294个有效视频可计数，抽查 v1 关系证据、邻接图和检索上下文均成功。独立测试库之外没有执行写入或初始化。

wheel 使用离线构建验证，包含全部新包模块及 `terms.json`，不含旧 `video_graph.py`、`video_graph_*.py` 或 `neo4j_graph/` 模块。

## 回归范围与已有问题

主回归 **463 passed in 40.04s**。最终结果和日志索引记录在 `data/video_graph_runs/refactor-20261110/implementation-audit.json`。范围包含全部图谱/实体关系评测契约、Neo4j 构建与恢复、聊天图谱接入和 Compose 配置检查，均在明确指定的隔离 PostgreSQL/Neo4j 实例执行。相关新增/修改 Python 文件 Ruff 检查通过。

扩展运行其他回答/搜索测试时发现两处原有失败，并在恢复重构前图谱代码的隔离副本中复现了相同断言：

- `test_search_persists_optimization_before_retrieval_failure`：既有查询 trace 不含测试期望的顶层 `provider`。
- `test_search_and_answer_diversify_before_truncating_long_video`：既有回答证据选择未覆盖测试期望的三个来源。

未修改这两项搜索行为或测试。另一个回答测试受开发环境真实模型密钥影响，明确禁用密钥后在重构前后均通过。相关基线/重构后日志一并保留，不把扩展回归描述为全部通过。

## 使用

详见更新后的[构图入口说明](../tools/video-knowledge-graph/README.md)。基本流程仍为：

```bash
.venv/bin/python tools/video-knowledge-graph/build_graph.py pipeline --limit 1 \
  --manifest data/video_graph_runs/new-build.plan.json \
  --report data/video_graph_runs/new-build.report.json
```

`NEO4J_*` 指向图谱库，`SYNC_DATABASE_URL` 指向业务原文库；没有后端选择项。失败恢复沿用同一清单和 `--retry-failed`。生产数据的批量回补和应用部署需另行执行，本次没有触发。
