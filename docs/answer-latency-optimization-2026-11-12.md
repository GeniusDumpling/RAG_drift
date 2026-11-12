# AI 问答耗时优化与发布（2026-11-12）

## 实现

按关键词 SQL、并行检索、生成冗余的顺序优化，保留现有模型、20 条候选全量重排、最多 8 条证据及 12,000 字符上下文预算。

1. 对标题、摘要、标签和切片两类正文建立 5 个 `pg_trgm` GIN 索引。选择性较强的型号问题通过内容匹配和正文匹配两个分支获得候选，再按原正则边界、完整词集和评分规则排序。仅将累计最高贡献严格小于 0.20 接受阈值的弱词移出候选预筛选，完整词集仍参与最终评分；存在实体加分时保留全部词。宽泛问题继续使用短路匹配更有利的原查询形状。
2. 按 0.50/0.30/0.20/0.10 递减权重使用 CASE 代替 GREATEST，命中较高权重字段后不再计算昂贵的正文正则，结果保持等价。连续纯中文词采用等价的字面子串匹配。
3. 向量分支（Embedding、Qdrant 初始化及查询）在线程中执行，与关键词异步 SQL 并行。只有关键词分支使用当前 AsyncSession，合并和正文补齐在两分支结束后执行；失败或取消会取消并收拢任务。
4. 生成上下文仅复用已经完整呈现的切片，截断的切片不能被当成完整原文引用；重复来源头部可引用前条完整头部。独有正文、独立证据编号、来源和图片保留。提示词补充按问题复杂度控制长度、避免重复结论，详细列举仍保留必要细节。
5. `generation.first_token_ms` 记录服务端首次正文增量，`generation.timings` 记录改写、检索、重排、上下文和模型首字阶段。检索 trace 记录关键词、向量、Embedding 和正文补齐耗时。

`pg_trgm` 支持相应文本匹配索引；在线索引使用 CONCURRENTLY 构建，以便采集写入继续进行。[PostgreSQL pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html)、[CREATE INDEX](https://www.postgresql.org/docs/current/sql-createindex.html)。

## 对照结果

使用同一只读 REPEATABLE READ 数据库快照，旧查询取自优化前源文件。每题返回最多 200 个关键词候选，比较完整有序切片 ID 和分数：

| 问题 | 优化前 | 优化后 | 结果 |
| --- | ---: | ---: | --- |
| DJI Mavic 3 Pro 主板芯片型号 | 8.529 秒 | 0.430 秒 | 200 条顺序、分数一致 |
| PX4 外部定位 | 2.324 秒 | 1.958 秒 | 200 条顺序、分数一致 |
| Mavic Pro GPS 故障 | 4.622 秒 | 4.261 秒 | 200 条顺序、分数一致 |

这些是固定问题的单次对照，不是并发压测或全体问题的延迟保证。原始结果见 [关键词对照](../data/evaluations/answer-latency-20261112/keyword-comparison.json)。

另将一次真实向量召回结果冻结，比较完整混合检索的 20 条 EvidenceObject（包括图片）：优化前后内容、顺序、分数和图片均相同。[混合检索对照](../data/evaluations/answer-latency-20261112/mixed-comparison.json)。此计时排除了两版共同的真实 Embedding/向量请求，不能当作端到端延迟。

近期 10 条真实问答的上下文未发现可复用的完整重复切片，去重未缩短其输入；没有据此宣称普遍降低模型输入耗时。[上下文对照](../data/evaluations/answer-latency-20261112/context-comparison.json)。

模型侧使用固定证据交错比较默认推理与 low，各 4 次：首字中位数分别为 3.913 和 5.962 秒，low 未稳定提速，因此没有上线降低推理强度的设置。保留原模型及默认推理行为。[模型对照](../data/evaluations/answer-latency-20261112/reasoning-controlled.json)。

## 验证和发布

问答、检索、取消、回退、图片及索引迁移测试 70 项通过，9 项独立 Neo4j 测试因没有配置专用实例而跳过。新增测试验证分支确实重叠执行、弱词筛选不能漏掉达到阈值的匹配、截断切片不误复用，以及并发索引中断后的恢复和降级删除。Ruff、5 个相关模块的 mypy 检查通过；没有声称整个项目或既有图谱模块通过严格类型检查。

索引已在主业务库在线创建并确认有效。本次只增加索引，业务 Alembic 版本仍为 0009；新增 `0011_keyword_trigram_indexes.py` 跟随仓库现有 0010 版本，供后续统一升级时复用。没有顺带执行尚未发布的图谱业务迁移。

先发布 `rag-answer-speed:20261112-v1`，再以 `rag-answer-speed:20261112-v2` 完成短路评分更新，并补齐复用邻接原文时当前引用的图片关联。对应测试验证原文只出现一次时，两条引用仍保留各自应有的图片。发布以实际运行镜像为基础，只打包对应性能模块，未引入工作区其他并行功能。线上保留现有前端、59 个环境变量、6 个挂载和网络配置；图谱仍未接入问答。

预览及正式流式回答以“DJI Mavic 3 Pro 的主板芯片是什么型号？”验证，检查 7 个固定参考型号、Likely 限定词、合法引用和配图。v1 正式调用的生成前准备耗时为 1.391 秒，服务端总计时 17.461 秒；同题此前发布验证分别为 9.046 和 27.129 秒。这只是各一次调用，模型阶段存在明显波动，不能将总耗时差全部归因于检索优化。[阶段对照](../data/evaluations/answer-latency-20261112/production-timing-comparison.json)。

v2 最终正式接口复测：HTTP 首字 9.822 秒、完成 11.976 秒；服务端生成前准备 1.866 秒，其中检索 0.959 秒、重排 0.889 秒、上下文 0.016 秒，模型自身首字等待 7.879 秒。7 个参考型号、限定词、引用、图片再次通过。该结果仍是单次可用性检查，不作稳定延迟承诺。[最终正式验证](../data/evaluations/answer-latency-20261112/production-final-speed.json)。

快照和私有运行配置保存在 `data/releases/answer-speed-20261112-v1/`、`data/releases/answer-speed-20261112-v2/`。回退配置包含连接凭据，目录权限为 700，配置文件权限为 600。回退到 v1：

```bash
docker compose -p rag_drift \
  -f data/releases/answer-speed-20261112-v2/rollback-compose.json \
  up -d --no-deps --no-build app
```

回退到性能优化前版本则使用 v1 目录下的 rollback-compose.json。回退后同步调整 `.env` 的镜像设置；索引可保留，旧查询仍可正常执行。

复测命令（项目根目录）：

```bash
PYTHONPATH=backend .venv/bin/python data/evaluations/answer-latency-20261112/benchmark_keyword_sql.py
PYTHONPATH=backend .venv/bin/python data/evaluations/answer-latency-20261112/benchmark_mixed_search.py
```
