# 论坛图谱实现与验收跟踪

目标：完整实现 [论坛管线设计](forum-knowledge-graph-design-2026-11-11.md) 的文本阶段及其增量、发布、核验和 RAG 接入。附件按设计保留未解析状态；不以附件推测生成事实。

当前状态：文本阶段实现及下列工程验收已完成；未向生产图谱发布论坛数据，未部署服务或安装定时任务。API/问答默认关闭。真实模型验证限于两个公开开发样例，不能据此推断全论坛精确率或召回率。

## 必须验证的要求

- [x] 完整快照与加工绑定；逐帖原文、引用、代码、chunk 区间及稳定来源身份。
- [x] 前置 assessment 分别判技术相关性和价值；skip/defer 不进入抽取，长帖完整覆盖，短反馈和新回复重判。
- [x] 有界窗口、逐帖/依赖指纹、实体独立审核与兼容的全局/案例身份。
- [x] 技术主张、案例、操作组合、针对具体建议的成功/失败反馈；断言与采纳状态分离。
- [x] 所有提及与事实逐帖原文验证、审核凭据、数值/版本和引用作者约束。
- [x] Neo4j 任务、租约、检查点、有界失败重试和精确请求缓存、真实/未知 usage。
- [x] FG 原生和 KG 共享层同事务发布；完整主题激活，失败/过期 worker 不覆盖旧构建。
- [x] skip、删除/隐藏、来源变更、撤回/恢复、跨来源共享事实的可见性与范围对账。
- [x] 原生及共享投影完整回读、来源现时性、图谱检索和 API。
- [x] 论坛图谱 RAG 回源与问题/行动/反馈召回；保留具体楼层和条件，故障时文本降级。
- [x] 盘点、固定计划、dry-run、构建、续跑、核验、查询、撤回、恢复与增量调度入口。
- [x] 单元回归、隔离 PostgreSQL/Neo4j 集成与真实来源只读盘点/有界模型验证，记录限制与成本。
- [x] 使用文档、配置示例、评测/负例与逐要求完成审计。

## 实现与逐项证据

核心模块位于 [forum_graph](../backend/app/services/forum_graph/)，构建入口是 [build_graph.py](../tools/forum-knowledge-graph/build_graph.py)。完整参数、配置、重试和调度用法见 [工具说明](../tools/forum-knowledge-graph/README.md)。

| 设计要求 | 当前实现与验证证据 |
| --- | --- |
| §1–3：一个主题一个业务 item，主帖及回复完整读取 | `source.load_material` 读取 topic-v1、完整快照及最新加工任务，拒绝未加工或绑定不符的快照。`test_source_reads_complete_posts_without_embedding_context`、`test_pending_latest_capture_is_not_a_current_source` 在真实 PostgreSQL 验证；不改现有业务表结构。 |
| §5.1：两项前置判断均通过才抽取 | 独立 `Assessment` schema、`assess`、窗口聚合先于所有实体/主张调用。`test_strict_assessment_rejects_extract_without_both_conditions`、`test_skip_and_defer_never_extract`、`test_long_topic_assessment_must_cover_tail_before_skip` 检查不放行、零后续调用及完整覆盖；纯感谢预筛、附件暂缓。 |
| §3/5.2：有界窗口、主帖/父回复/引用区分 | `windows.make_windows` 按正文块分窗，仅带实际父链、引用及根问题，标明截断。`test_deep_context_and_long_blocks_remain_bounded`、引用/代码边界测试；`embed_text` 中的附加上下文不是当前作者证据。 |
| §4/5.3：实体身份和独立审核 | `extraction` 先识别案例/实体，独立逐项审核，再提取/独立审核主张；身份兼容全局 v2 namespace，主题/案例局部身份分开。全局别名测试、无效候选不拖累有效候选测试、跨作者两案例条件隔离测试。 |
| §4.3/4.4：观察、建议、推测、问题及反馈分开 | `Claim/Evaluation` 保留 act/assertion/targets/outcome/combined_operations。问题和假设仅保留原生层；建议以 instruction+recommendation 发布。`test_mitigates_requires_same_case_targeted_success` 覆盖成功/失败 × 本案例/其他案例四种组合：跨案例反馈保留独立原生记录，不挂到原案例建议的共享证据，也不能据此生成原案例 mitigates。跨作者观察不能归入楼主设备案例。采纳不作为成功依据。 |
| §5.4/6：原文及当前 chunk 可定位 | 程序检查唯一引文、帖内偏移、作者、参数名、数值符号/完整版本及活动 chunk 连续覆盖。FGSpan 和 Evidence.locator 包含块类型、映射版本、快照、来源指纹、构建/审核版本。篡改 receipt、数值/版本、来源编辑、原生节点损坏及代码证据类型测试。 |
| §7.1：增量重判及复用 | assessment 与抽取 receipt 绑定实际输入、已知案例和可见主张；上下文变化失效。`test_new_reply_reassesses_only_new_window`、跳过主题新增价值、相同正文重新抓取保留原快照锚点测试。chunk 重绑定不直接强制模型重跑。 |
| §7.2：调度、合并及恢复 | 固定有界计划与目标校验；自动更新静默期/最长等待，旧积压配额；租约到期可接管；失败/暂缓显式重试。覆盖固定计划篡改、过期租约接管、更新合并及最长等待、dry-run 零写入、CLI 全流程测试。最长等待指开始构建，持续变化仍须通过最终现时性检查。 |
| §7.3/8：两层原子发布及来源隔离 | `storage.publish` 在同一 Neo4j 事务中写原生投影、调用共享 publish 并切换有效构建。注入共享写入失败验证两层回滚；过期 worker 不得写检查点；论坛撤回不删除其他来源共用的事实；规则升级失败可继续服务可验证的旧版本。 |
| §7.3：删改、隐藏、撤回恢复 | 查询回源；编辑后旧版本拒绝返回，隐藏/删除原帖不能作为证据；物理删除在范围内对账撤回；restore 后须重新激活。相应生命周期与对账测试均运行。一次抓取失败不等于来源删除。 |
| §8/9：统一查询、API 及 RAG | 原生/共享完整回读；有关键词的查询只核验命中的候选来源，无关键词或显式来源检查整个范围。`test_unrelated_changed_topic_does_not_disable_valid_query_candidates`、API 开关/当前引用测试。两 API 应用均注册 `/forum-graph`。 |
| §9：问题—行动—反馈与保存历史 | `forum_graph.answer` 回源、映射及再次核验，保留三种角色和各自楼层；无检索资格的反馈只留原生层。实际 AsyncSession + ChatRepository + SearchService 集成验证模型上下文、审计 chunk ID 与保存后的论坛证据；前端验证条件、楼层链接、联合操作、采纳和未验证建议。 |
| §11：验收、反例及成本 | 下列测试日志、两公开开发样例、运行前冻结的 `request-preview.json` 与真实 usage。工程一致性、语义开发样例和留出集质量统计分开记录。 |

## 实际验证结果

日志保存在本地 [checks](../data/forum_graph_runs/20261112-pilot/checks/)；测试数据均位于新建随机名称、以 `_test` 结尾的数据库，逐例 schema 隔离，结束后删除测试库。测试 Neo4j 只绑定 `localhost:45970`，没有挂载生产数据卷。

收尾只读查询确认本次随机测试库剩余数为 0；核对容器创建 ID 和专用标签后，已停止并自动删除临时 Neo4j。公开试点与完整回读结果保留为下列文件，没有保留测试服务。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 论坛完整 PostgreSQL/Neo4j 集成、客户端协议及 CLI | **57 passed**，无跳过 | [forum.log](../data/forum_graph_runs/20261112-pilot/checks/forum.log)、[论坛测试](../backend/tests/test_forum_graph.py)、[客户端测试](../backend/tests/test_forum_graph_client.py) |
| 视频/文档统一图谱、实际 Neo4j 聊天、检索、切片、向量回归 | **96 passed**，无跳过 | [shared.log](../data/forum_graph_runs/20261112-pilot/checks/shared.log)；对应 `test_knowledge_graph / test_chat_answers / test_search_pipeline / test_chunking_and_indexing / test_embedding_reindex` |
| 论坛证据卡、原证据卡、图片、聊天、流协议 | **27 passed** | [frontend.log](../data/forum_graph_runs/20261112-pilot/checks/frontend.log) |
| 前端 TypeScript + Vite 构建 | 通过；存在现有的 bundle 大小提示 | [frontend-build.log](../data/forum_graph_runs/20261112-pilot/checks/frontend-build.log) |
| 涉及的 Python 模块/测试/CLI Ruff，Git whitespace 检查 | 通过 | [ruff.log](../data/forum_graph_runs/20261112-pilot/checks/ruff.log)、`git diff --check` |

客户端协议测试使用本地 HTTP transport 验证响应截断、认证失败不重试、429 有界重试、未知 usage 不记零、精确缓存/账户绑定和损坏缓存。集成测试中的模型响应是 fixture，用于验证工程约束，不能作为模型准确率证据。聊天集成以本地模型流替身验证完整数据路径，没有把测试内容发给外部模型。

另有旧前端 `app-search-content-navigation` 用例使用已改名的“检索问答”导航，实际 HEAD 中为“检索”，该用例失败与本次论坛变更无关，未将它计入通过结果或改动该导航测试。

## 两论坛真实开发样例

先只读盘点当前来源，并在首次模型调用前保存 [范围与预期](../data/forum_graph_runs/20261112-pilot/request-preview.json)。范围严格限定以下两帖共四个发言、1,130 字符的公开材料；没有发送数据库凭据、cookie、图片或私有文档。模型使用当前配置的 Ark `deepseek-v4.1-flash-economy`。图谱只写隔离实例。

| 来源 | 预先要求及人工回核 | 最终产物 |
| --- | --- | --- |
| [ArduPilot 132068](https://discuss.ardupilot.org/t/132068) | 保留恢复默认设置/重刷后参数仍存在的观察及 Mission Planner 1.3.82、ArduPlane 4.5.3、H743 条件；回复作为未验证操作建议，不能生成成功结论。回核符合。 | 2 FGPost、1 FGCase、5 FGClaim，4 条共享事实，0 个成功反馈；含 1 条未验证 instruction。 |
| [PX4 31268](https://discuss.px4.io/t/31268) | 保留 QGC 航线穿过排除围栏的未解决案例，保留 rover/boat 范围；不猜图片，不把用户希望的软件能力写成实际功能。回核符合。 | 2 FGPost、1 FGCase、2 FGClaim，1 条共享状态事实，0 个成功反馈；提问留在原生层。 |

真实模型 v6 运行报告为 [pilot-v6.report.json](../data/forum_graph_runs/20261112-pilot/pilot-v6.report.json)。最终证据定位字段补齐后，以相同模型请求的精确缓存执行 `cache_only` 重放，未再次调用模型：

- [最终固定计划](../data/forum_graph_runs/20261112-pilot/pilot-release.plan.json)、[最终报告](../data/forum_graph_runs/20261112-pilot/pilot-release.report.json)。
- [ArduPilot 完整审查 JSON](../data/forum_graph_runs/20261112-pilot/132068.release.review.json)、[HTML](../data/forum_graph_runs/20261112-pilot/132068.release.review.html)。
- [PX4 完整审查 JSON](../data/forum_graph_runs/20261112-pilot/31268.release.review.json)、[HTML](../data/forum_graph_runs/20261112-pilot/31268.release.review.html)。

最终回读逐项核对当前来源、窗口覆盖、审核 receipt、原生节点/边、投影哈希、共享实体/事实/证据及有效指针。最终两主题合计 5 条共享事实。图谱回读通过本身不证明语义正确；开发中曾发现“机械通过但无有效主张”及用户预期误作功能的情况，已修正规则并重新检查这些预先冻结的要求。

成本按报告的实际 provider usage 汇总：v1–v6 共 **69 次 HTTP 请求、167,003 tokens**，包括失败和开发迭代；v6 单轮 **38,698 tokens**。最后 release 重放 **21 次精确请求缓存命中、0 次 HTTP 请求**，没有新增模型 tokens。未给出货币金额，因本次没有核对账户实际计费单价；缓存命中与 provider token cache 不是同一指标。

## 交付边界与后续质量验收

- 本次交付已实现设计文本阶段、独立构建/增量入口、完整来源校验和问答接入。PostgreSQL 不新增图谱任务表，不改现有 item/chunk 身份，不运行向量化或重设论坛采集任务。
- 构建来源最终使用短暂 `SELECT ... FOR UPDATE` 行锁；虽然没有业务 DML，账户仍需相应锁权限。PG 与 Neo4j 不是分布式原子事务，发布后和查询时继续回源校验。
- 图片、附件日志、外部链接目标尚未解析，不能生成视觉/日志验证事实；这是设计第四阶段，非本期已完成能力。
- 两帖是开发集，已用于修正规则。尚未完成独立留出集的技术相关性/入图价值误过滤率、实体/关系精确率召回率、跨版本与大规模长帖质量统计；不报告虚构的 F1 或语义质量 100%。规模化发布前应按设计 §11 冻结独立样本和门槛后再评估。
- `FORUM_GRAPH_ENABLED` 默认 false；生产应用需部署新代码并显式配置/启用读取。CLI 可以手动构建指定范围；本次没有启用生产论坛图谱或安装定时任务。
