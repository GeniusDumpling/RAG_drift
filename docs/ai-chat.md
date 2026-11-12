# AI 问答实现与运行

AI 问答对专业问题先检索知识库，再基于本轮证据生成带编号引用的答案；纯寒暄等消息自然回复。前端使用流式接口，原有 JSON `/answer` 保持可用。

2026-11-12 已发布问答耗时优化：关键词查询按问题选择索引候选或原短路计划，向量与关键词并行，复用完整重复上下文。新增 `generation.first_token_ms`、`generation.timings` 和检索分支耗时；实现、对照、发布与回退见 [耗时优化记录](answer-latency-optimization-2026-11-12.md)。

## 对话意图与上下文优先级

- 当前用户消息决定本轮任务。历史用于明确承接前文的指代与省略，不能把当前寒暄或新话题强行变成旧型号的问题。
- 完整、明确的寒暄、致谢、确认、告别和功能询问走 `conversation` 分支，例如“你好”“谢谢”“好的”“你能做什么”。不调用追问改写、Embedding、向量/关键词检索、图谱检索或 reranker，生成时不传旧历史和证据。
- 路由采用保守的完整消息匹配；“你好，请介绍 Mavic 3 Pro”“谢谢，请继续解释参数”仍走知识问答。未识别的消息默认保留知识检索流程，不将任意专业问题当作闲聊。
- 纯对话通常仍由已配置的 LLM 流式回复；缺少密钥或模型失败时返回简短的本地回复，界面标为“对话回复”，不显示“未找到支持证据”或“证据摘录”。模型中途失败仍用 `reset` 替换不完整文本。
- 知识问题的提示词要求先核对证据与当前型号、版本和场景；专业事实须引用本轮原文，条件推断说明前提。无关材料不必逐项复述，不要求用户提供系统本应检索的引用材料。
- 明确说“换个话题”等时不读取旧上下文。其他有历史的问题经改写模型判断：独立问题原样返回、清空本轮生成历史；明确追问保留所需上下文。改写失败时，只有存在明确追问线索才拼接最近两条用户问题，独立问题直接检索当前原文。
- 普通对话继续保存在会话中，但不占后续知识问答最近6轮的上下文窗口；早期没有 `response_mode` 字段的记录按原知识历史兼容处理。
- 为兼容 API 和审计，普通对话也有查询记录，但检索 trace 标为 `skipped`、结果数为0；无需数据库迁移。

回归验证包含“先讨论Mavic 3 Pro→连续寒暄→继续知识追问”、寒暄前缀后的专业问题、明确换话题、改写失败及模型中途失败。修复代码先在临时目录使用真实模型验证普通对话，随后已构建并上线 `rag-chat-context:20261029-v1`，正式多轮寒暄复测通过，见 [部署记录](chat-context-deployment-2026-10-29.md)。

## 证据上下文

- 型号查询保留完整短语（如 `Mavic 3 Pro`），英文词按词边界匹配；数字和通用型号后缀降权，避免 `3`、`pro` 子串主导结果。
- 关键词按查询覆盖度评分，同一词命中多个字段不重复累加；与向量合并时保留相似度差异，弱关键词命中不会直接将分数推满。
- SQL 候选每篇最多保留两个高分切片；普通检索先按内容去重再截取，问答先覆盖不同内容，再允许每篇第二个切片。
- 未启用 reranker 时，回答阶段召回 `3 × top_k` 条候选，最多 150 条；启用后由 `RERANKER_CANDIDATE_LIMIT` 控制候选数量，默认 20 条。
- 最终选择最多 `min(top_k, ANSWER_MAX_EVIDENCE)` 条生成证据，默认上限为 8。
- 启用 reranker 后，以改写后的问题和候选标题＋正文调用 SiliconFlow `BAAI/bge-reranker-v2-m3`，请求 `top_n=实际候选数`，完整保留所有候选的排序和相关性分数。正文默认最多 8,000 字符，截断 ID 会记录到 trace。
- 重排成功后按模型分数排序，同一内容最多两条；未启用或降级时沿用先覆盖不同 `content_item_id`、再补同源第二条的选择方式。
- 从 PostgreSQL 读取命中切片的完整正文；预算允许时补充前后相邻切片，排除 `obsolete` 切片。
- 编号证据上下文总上限为 12,000 字符；每条证据公平分配预算，长切片可能截断。
- 界面仍使用短 `snippet`。生成结果记录实际读取的 `context_chunk_ids` 和 `context_chars`，答案中的 `[n]` 对应本轮返回的证据顺序。

检索 trace 的 `ranking_version=phrase-coverage-v1` 标识本次排序规则，`model_phrases` 保存从实际检索问题识别的型号短语。旧视频向量缺少 `item_type` 时允许进入候选，随后仍按 PostgreSQL 的真实类型过滤；新采集视频会写入类型、来源、语言等过滤字段。详细修复与验证见 [检索排序修复记录](retrieval-ranking-fix-2026-10-21.md)。

## 知识图谱与 RAG 已断开（2026-11-11）

当前默认及本地 `.env` 均为 `ANSWER_GRAPH_ENABLED=false`。知识问答只执行关键词/向量检索、reranker 和生成；关闭时在问答主流程直接跳过图谱召回和证据合并，不输出 `graph_retrieving` 进度，`generation.graph_retrieval.status=disabled`。`docker-compose.deploy.yml` 显式透传该配置并默认关闭。

独立的 `/video-graph/retrieve`、图谱浏览、构建及存储接口继续可用。以下是保留的可选接入机制，仅显式设置 `ANSWER_GRAPH_ENABLED=true` 后才启用。

检查运行中的 `intelligence-rag-app`（`rag-parrot-note:20261106-v1`）发现：Settings 尚无图谱问答开关，`SearchService.stream_answer` 也没有图谱调用，线上本来未接入；本次无需重启或更换镜像。

验证：问答与生成测试共 34 项通过、9 项因未配置独立 Neo4j 测试实例而跳过；新增 JSON/SSE 测试将图谱召回替换成禁止调用的函数，验证关闭状态下仍正常返回普通 RAG 证据，且没有图谱进度与关系上下文。相关 Ruff 和问答模块类型检查通过。

## 保留的可选图谱接入（2026-11-06）

显式启用后，专业问答在混合检索后调用图谱检索能力，`/video-graph/retrieve` 与问答共享 `retrieve_graph_context` 服务，统一查询 Neo4j，再回 PostgreSQL 校验业务切片。图谱模块位于 `app/services/video_graph/`，旧后端选择配置已删除。寒暄分支不查询图谱，普通 `/search` 不改变。

图谱通过问题中的显式实体别名进行一跳关系召回。返回的证据按当前数据库中的原文切片补齐来源信息，执行与普通检索相同的来源、类型、语言、标签及发布时间筛选，排除 obsolete 切片，并再次核验引句能否在当前切片定位。无原文切片、关系端点不完整或引句已失效的条目不进入回答。

混合检索与图谱按名次交错合并、按 chunk_id 去重，重复切片保留原始检索分数并附加图谱证据。抽取置信度不参与相关性比较。合并后统一进入现有 reranker，完整返回所有候选排序，再执行最多 8 条和每篇最多 2 条的上下文选择。因此开启图谱后，20 条混合候选还可能补充最多 10 个图谱候选，并非将合并结果再次截为 20 条。

`supporting_evidence[].graph_evidence` 保存关系 ID、主谓宾、原文引句、陈述状态、适用条件及时间定位 URL。生成上下文及重排文本包含这些关系线索；长引句允许明确标记截断，状态与条件不按头尾裁剪。提示词要求保留 negated/uncertain，不得把关系路径或抽取置信度当作兼容性、因果关系或已核实事实。

配置默认关闭：

- `ANSWER_GRAPH_ENABLED=false`：默认断开图谱问答；显式改为 `true` 才补充图谱证据。
- `ANSWER_GRAPH_TOP_K=10`：图谱接口返回的证据条目上限（1–30），去重和过滤后可能更少。
- `ANSWER_GRAPH_TIMEOUT_SECONDS=5`：图谱召回及原文补齐的总等待上限。

图谱为空、故障或超时会沿用混合检索结果；PostgreSQL 查询使用保存点，图谱 SQL 错误不会使问答事务失效。Neo4j 同步查询运行在独立线程和数据库连接中；达到等待上限后回答继续，已经开始的线程会在底层查询返回或超时后清理资源，不承诺强制终止线程。

`generation.graph_retrieval` 记录后端、retrieved/empty/disabled/fallback、耗时、原始证据数、有效切片及最终选择的图谱切片 ID。字段随现有 JSON 审计、流式 done 和历史对话保存，不需要数据库迁移。

前端新增 `graph_retrieving` 状态及“图谱关联”证据标记，可展开查看关系、原文、否定/不确定状态及适用条件。

验证：专用测试数据库覆盖图谱独立召回进入答案和历史、去重、来源筛选、撤回、失效切片、SQL 故障及超时回退；现有 Neo4j 数据以 PX4、Mavic 3 Pro 完成只读召回与上下文组装检查。本次源码接入不包含线上镜像重建。

2026-11-06 验证结果：

- `test_chat_answers.py`、`test_answer_generation.py` 合计 41 项通过，包含 JSON `/answer` 与 SSE `/answer/stream` 的图谱证据传递。
- 前端问答与证据卡片 16 项测试通过，`npm run build` 通过。
- 相关 Python 文件 Ruff 通过；7 个核心变更模块使用 `mypy --follow-imports=silent` 检查通过（未声称整个既有图谱模块通过严格类型检查）。
- 真实 Neo4j 只读验证：PX4 返回 3 个可用切片，Mavic 3 Pro 返回 4 个，两者均包含图谱生成上下文。该检查未调用 LLM，不代表答案质量提升评测。
- 扩大到 `test_search_pipeline.py` 时，`test_search_persists_optimization_before_retrieval_failure` 因缺少 `query_trace_json.provider` 失败；在临时副本中使用 HEAD 原版 `search.py` 单独复测，同样失败。未改动该独立查询优化行为或放宽其断言。

## 状态追踪与降级

JSON 响应及流式 `done` 事件增加 `generation`：

| 字段 | 含义 |
| --- | --- |
| `status` | `generated`、`fallback`、`no_evidence`、`cancelled`、`error` |
| `response_mode` / `conversation_intent` | 知识问答或普通对话，以及本轮识别到的普通对话意图 |
| `history_used` | 本轮是否实际携带历史进行知识问答 |
| `reason` | 缺少密钥、模型请求失败、连接断开等原因代码 |
| `model` | 配置的生成模型 |
| `latency_ms` / `total_latency_ms` | 生成阶段 / 全流程耗时 |
| `evidence_count` / `context_chars` / `context_chunk_ids` | 实际证据与上下文统计 |
| `retrieval_query` / `rewrite_status` | 用于检索的独立问题及追问改写状态 |
| `reranking` | 重排状态、模型、候选数、耗时、全部候选的前后名次和分数，失败时的原因代码 |

最终答案、支持证据快照和生成信息保存在 `search_queries.result_summary_json`；生成信息也写入 `query_trace_json.generation`。服务端日志保留模型调用异常。

知识问答的模型不可用时返回带编号的证据摘录，前端明确标注；知识问答没有证据时不调用生成模型。模型流中途失败会先发送 `reset` 替换未完成文本，再以降级结果结束，避免将部分模型输出和原文摘录混合。没有收到 `done` 的连接按未完成处理。

重排超时、限流、缺少密钥或返回不完整/非法排序时，整批回退到原检索顺序，不中断回答。`rerank_score` 独立保存，不覆盖原 `score`、向量分数或关键词分数。客户端取消会继续向上传播，避免用户停止后继续生成。

重排设置：`RERANKER_PROVIDER=siliconflow` 启用，`none` 关闭（代码默认关闭）；独立的 `RERANKER_API_KEY` 未设置时，依次复用 `EMBEDDING_API_KEY`、`VLM_API_KEY`。`RERANKER_BASE_URL` 默认 `https://api.siliconflow.cn/v1`，总请求超时由 `RERANKER_TIMEOUT_SECONDS` 控制，默认 5 秒。此功能使用现有 JSON 审计字段，无需数据库迁移或向量重建。接口依据 [SiliconFlow 官方文档](https://docs.siliconflow.cn/docs/api/rerank-post)。

本项目的 [20/40/60 候选与4/8/12/20证据评测](reranker-evaluation-2026-10-27.md) 暂选 20 条候选全部重排、最终最多 8 条；候选数和证据上限均可独立配置。当前前端请求 `top_k=10`，若手动将证据上限设到 12/20，还需同步提高请求的 `top_k`。

当前已部署 `rag-chat-context:20261029-v1`，继续启用20/8重排，并包含对话意图与上下文修复；发布和回退方法见 [最新部署记录](chat-context-deployment-2026-10-29.md)。初始重排发布记录见 [2026-10-27部署记录](reranker-deployment-2026-10-27.md)。

## 会话和追问

新增 `chat_sessions`、`chat_turns` 表，保存用户问题、答案、状态、支持证据和生成诊断。

- `POST /chat-sessions`：创建会话。
- `GET /chat-sessions?limit=50&offset=0`：按最近更新分页查询会话，`limit` 最大 100。
- `GET /chat-sessions/{id}`：读取会话及历史问答。
- `DELETE /chat-sessions/{id}`：删除会话、全部问答及关联的查询/生成诊断，成功返回 `204`。不存在时返回 `404`，仍在生成时返回 `409`，请先停止生成。超过 10 分钟的遗留待完成请求允许随会话一起清理。其他对话仍引用的查询记录会保留。
- `/answer` 和 `/answer/stream` 均接受可选 `session_id`；不提供时维持无会话的单轮行为。
- 知识问答最多读取最近6条已完成的知识问答，用户问题每条最多1,000字符，历史答案每条最多2,000字符。
- 追问先结合历史改写为独立问题，再检索；独立问题不携带旧答案生成，改写失败时按上面的上下文规则回退，并记录状态。
- 历史只帮助理解意图，提示词要求事实仍由本轮证据支持，并禁止沿用历史引用编号。
- 同一会话只允许一条待完成请求；连接关闭时保存部分答案并标记 `cancelled`，未完成问答不进入后续模型历史。进程异常留下的待完成请求可在 10 分钟后由新请求回收。

浏览器只保存当前会话 ID，用于刷新或返回页面后从服务器恢复。新建对话不删除原有历史。生成中可停止，离开页面会取消请求。

选择历史对话后可点击“删除对话”，确认后永久删除。成功后清空当前消息、移除列表项和浏览器保存的会话 ID，进入新对话；失败时保留原会话并展示错误。删除功能使用现有外键级联，无需数据库迁移。

## 流式协议

`POST /answer/stream` 接受与 `/answer` 相同的 JSON，请求头可设置 `Accept: text/event-stream`。响应关闭缓存及 Nginx 上游缓冲。

事件依次包含：

1. `status`：知识问答包含 `retrieving`、显式启用图谱时的 `graph_retrieving`、可选的 `reranking`、`generating`；普通对话仅包含 `generating`。
2. `evidence`：检索记录 ID、问答 ID 和本轮支持证据。
3. `delta`：模型生成的文本增量，形如 `{"text":"内容"}`。
4. `reset`：出现降级时提供完整替换文本和生成状态。
5. `done`：完整 `AnswerResponse`，此时数据库写入已完成。

检索或会话处理失败会发送 `error` 事件，其 `message` 可展示给用户。流开始后 HTTP 状态码不能再变化，因此客户端必须处理事件内错误，不能只检查 HTTP 200。

流式响应自己管理数据库 session 生命周期，避免 FastAPI 请求依赖在响应体结束前关闭数据库连接。查询优化和同步向量检索放入线程池，模型流及追问改写使用异步 HTTP。

## 启用

本地源码运行前执行：

```bash
.venv/bin/alembic upgrade head
cd frontend
npm run build
```

然后重启后端或使用开发热重载。Docker 需要重新构建并更新 app 镜像；默认入口在启动时执行 `alembic upgrade head`。本次迁移新增两个表，不改动已有内容或向量数据。若使用反向代理，应允许 SSE 长连接且关闭响应缓冲。

已有运行镜像的依赖满足当前项目要求时，可复用依赖层并重新构建前端与后端：

```bash
docker build -f docker/Dockerfile.app-update \
  --build-arg APP_RUNTIME_IMAGE=<现有已验证镜像> \
  -t <新镜像标签> .
```

该构建执行 `pip check`；缺少依赖时构建会失败，此时应使用项目根目录的完整 Dockerfile 构建。

## 验证

新增测试覆盖完整切片与来源分散、上下文预算、模型 SSE、空响应与截断、部分输出降级替换、生成状态持久化、多轮追问、停止与断流清理、会话冲突，以及迁移升级/回退。模型调用使用模拟响应，持久化测试使用独立的 `intelligence_rag_test` 数据库。
